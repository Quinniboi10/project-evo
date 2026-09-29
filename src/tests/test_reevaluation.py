from project_evo.database import Database, PrimaryTableRow
from project_evo.error import DatabaseException, EvalError
from project_evo.reevaluation import IdleEvaluator
from project_evo.task import Task
from project_evo import config, evolve, git

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import subprocess
import tempfile
import unittest

class ReevaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evo-reevaluation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "run.db"
        self.db = Database(self.path, island_count=2)
        self.addCleanup(self.db.close)
        self.row = PrimaryTableRow("Baseline", "baseline", None, None, None, 10)
        self.db.insert_baseline(self.row)
        self.cfg = SimpleNamespace(db_file=self.path, evaluation_concurrency=1, gnhf=False, eval_fn=None)
        cfg_patch = patch.object(config, "cfg", self.cfg)
        cfg_patch.start()
        self.addCleanup(cfg_patch.stop)

    @contextmanager
    def workspace(self, row: PrimaryTableRow):
        yield Path(str(row.id))

    def scheduler(self, evaluate) -> IdleEvaluator:
        self.cfg.eval_fn = evaluate
        workspace_patch = patch.object(git, "evaluation_workspace", self.workspace)
        workspace_patch.start()
        self.addCleanup(workspace_patch.stop)
        scheduler = IdleEvaluator()
        self.addCleanup(scheduler.close)
        return scheduler

    def wait_event(self, event: Event):
        if not event.wait(5):
            raise RuntimeError("Timed out waiting for test event")

    def test_running_mean_and_concurrent_updates(self):
        self.assertEqual(self.db.record_evaluation(1, 20), (15, 2))
        self.assertEqual(self.db.record_evaluation(1, 30), (20, 3))
        def measure(score: int):
            with closing(Database(self.path, require_exist=True)) as db:
                db.record_evaluation(1, score)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(measure, range(40, 140, 10)))
        with closing(Database(self.path, require_exist=True)) as db:
            row = db.sample_reevaluation(set())
            assert row is not None
            self.assertAlmostEqual(row.score, 70, places=12)
            self.assertEqual(row.evaluation_count, 13)
        before = self.db.select("SELECT * FROM evolve")
        with self.assertRaises(DatabaseException):
            self.db.record_evaluation(999, 20)
        self.assertEqual(self.db.select("SELECT * FROM evolve"), before)
        self.db.record_evaluation(1, 1e308)
        self.assertGreater(self.db.record_evaluation(1, 1e308)[0], 0)

    def test_least_measured_including_baseline_and_exclusions(self):
        self.db.insert_attempt(PrimaryTableRow("Child", "child", None, Task.IMPROVE, 1, 20), 1)
        seen = set()
        for _ in range(80):
            row = self.db.sample_reevaluation(set())
            assert row is not None
            seen.add(row.id)
        self.assertEqual(seen, {1, 2})
        self.db.record_evaluation(2, 20)
        row = self.db.sample_reevaluation(set())
        assert row is not None
        self.assertEqual(row.id, 1)
        row = self.db.sample_reevaluation({1})
        assert row is not None
        self.assertEqual(row.id, 2)
        self.assertIsNone(self.db.sample_reevaluation({1, 2}))

    def test_foreground_fifo_precedes_background_and_shutdown_drains(self):
        background = Event()
        release_background = Event()
        first = Event()
        release_first = Event()
        calls: list[str] = []
        def evaluate(path: Path):
            calls.append(str(path))
            if str(path) == "1":
                background.set()
                self.wait_event(release_background)
            elif str(path) == "first":
                first.set()
                self.wait_event(release_first)
            return True, 20
        scheduler = self.scheduler(evaluate)
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                self.wait_event(background)
                one = pool.submit(scheduler.evaluate, Path("first"))
                with scheduler.condition:
                    self.assertTrue(scheduler.condition.wait_for(lambda: len(scheduler.waiters) == 1, timeout=5))
                two = pool.submit(scheduler.evaluate, Path("second"))
                with scheduler.condition:
                    self.assertTrue(scheduler.condition.wait_for(lambda: len(scheduler.waiters) == 2, timeout=5))
                release_background.set()
                self.wait_event(first)
                scheduler.stop()
                release_first.set()
                self.assertTrue(one.result(timeout=5).passed)
                self.assertTrue(two.result(timeout=5).passed)
            finally:
                scheduler.stop()
                release_background.set()
                release_first.set()
        scheduler.close()
        self.assertEqual(calls, ["1", "first", "second"])
        self.assertEqual(self.db.select("SELECT score, evaluation_count FROM evolve"), [(15, 2)])

    def test_multiple_idle_slots_reserve_distinct_revisions(self):
        self.cfg.evaluation_concurrency = 2
        first, both, release = Event(), Event(), Event()
        calls: list[str] = []
        lock = Lock()
        def evaluate(path: Path):
            with lock:
                calls.append(str(path))
                first.set()
                if len(calls) == 2:
                    both.set()
            self.wait_event(release)
            return True, 20
        scheduler = self.scheduler(evaluate)
        try:
            self.wait_event(first)
            self.db.insert_attempt(PrimaryTableRow("Child", "child", None, Task.IMPROVE, 1, 20), 1)
            self.wait_event(both)
            with scheduler.condition:
                self.assertEqual(scheduler.active, 2)
                self.assertEqual(scheduler.reserved, {1, 2})
                self.assertCountEqual(calls, ["1", "2"])
        finally:
            scheduler.stop()
            release.set()
            scheduler.close()
        self.assertEqual(self.db.select("SELECT evaluation_count FROM evolve"), [(2,), (2,)])

    def test_failed_or_invalid_recheck_preserves_score(self):
        for result in [(False, 0), (True, float("nan"))]:
            with self.subTest(result=result):
                scheduler = self.scheduler(lambda path: result)
                with self.assertRaisesRegex(EvalError, "baseline"):
                    scheduler.failure.result(timeout=5)
                scheduler.close()
                self.assertEqual(self.db.select("SELECT score, evaluation_count FROM evolve"), [(10, 1)])

    def test_gnhf_skips_failed_revision_but_accepts_foreground(self):
        self.cfg.gnhf = True
        evaluate = Mock(return_value=(False, 0))
        scheduler = self.scheduler(evaluate)
        with scheduler.condition:
            self.assertTrue(scheduler.condition.wait_for(lambda: scheduler.failed == {1}, timeout=5))
        self.assertFalse(scheduler.failure.done())
        evaluate.return_value = (True, 20)
        self.assertTrue(scheduler.evaluate(Path("new")).passed)
        scheduler.close()
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(self.db.select("SELECT score, evaluation_count FROM evolve"), [(10, 1)])
        # A new invocation retries the previously failed saved revision.
        evaluate.return_value = (False, 0)
        restarted = self.scheduler(evaluate)
        with restarted.condition:
            self.assertTrue(restarted.condition.wait_for(lambda: restarted.failed == {1}, timeout=5))
        restarted.close()
        self.assertEqual(evaluate.call_count, 3)

    def test_background_failure_wakes_run_loop(self):
        self.cfg.reevaluate_idle = True
        self.cfg.iterations = self.cfg.concurrency = 1
        agent_started = Event()
        released = Event()
        def evaluate(path: Path):
            self.wait_event(agent_started)
            return False, 0
        def agent(island: int):
            agent_started.set()
            self.wait_event(released)
        original_stop = IdleEvaluator.stop
        def stop(scheduler: IdleEvaluator):
            released.set()
            original_stop(scheduler)
        self.cfg.eval_fn = evaluate
        with patch.object(git, "evaluation_workspace", self.workspace), patch.object(evolve, "select_island", return_value=0), patch.object(evolve, "run_worker", side_effect=agent), patch.object(IdleEvaluator, "stop", stop):
            self.assertEqual(evolve.run_iterations(), 1)
        self.assertTrue(released.is_set())
        self.assertIsNone(evolve._idle_evaluator)

    def test_normal_completion_checks_last_background_failure(self):
        self.cfg.reevaluate_idle = True
        self.cfg.iterations = self.cfg.concurrency = 1
        background, released = Event(), Event()
        def evaluate(path: Path):
            background.set()
            self.wait_event(released)
            return False, 0
        def agent(island: int):
            self.wait_event(background)
        original_stop = IdleEvaluator.stop
        def stop(scheduler: IdleEvaluator):
            released.set()
            original_stop(scheduler)
        self.cfg.eval_fn = evaluate
        with patch.object(git, "evaluation_workspace", self.workspace), patch.object(evolve, "select_island", return_value=0), patch.object(evolve, "run_worker", side_effect=agent), patch.object(IdleEvaluator, "stop", stop):
            self.assertEqual(evolve.run_iterations(), 1)
        self.assertIsNone(evolve._idle_evaluator)

    def test_old_schema_is_not_migrated(self):
        self.db.db.execute("ALTER TABLE evolve DROP COLUMN evaluation_count")
        self.db.db.execute("UPDATE metadata SET schema_version = 1")
        self.db.db.commit()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(DatabaseException, "evaluation_count"):
            Database(self.path, island_count=2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_detached_worktree_preserves_saved_and_active_files(self):
        self.cfg.project_root = self.root / "project"
        self.cfg.project_root.mkdir()
        self.cfg.workspace_base, self.cfg.branch_base = "workspaces", "evo"
        def command(*args: str):
            return subprocess.check_output(["git", *args], cwd=self.cfg.project_root, text=True).strip()
        command("init", "--initial-branch=main")
        command("config", "user.name", "Test")
        command("config", "user.email", "test@example.invalid")
        state = self.cfg.project_root / "state.txt"
        state.write_text("saved")
        command("add", ".")
        command("-c", "commit.gpgsign=false", "commit", "-m", "baseline")
        command("branch", "evo/baseline")
        commit = command("rev-parse", "HEAD")
        state.write_text("active")
        for failed in (False, True):
            workspace = self.root / "uncreated"
            try:
                with git.evaluation_workspace(self.row) as workspace:
                    self.assertEqual((workspace / "state.txt").read_text(), "saved")
                    (workspace / "state.txt").write_text("modified by evaluator")
                    if failed:
                        raise EvalError("test failure")
            except EvalError:
                self.assertTrue(failed)
            self.assertFalse(workspace.exists())
            self.assertEqual(command("rev-parse", "evo/baseline"), commit)
            self.assertEqual(command("branch", "--show-current"), "main")
            self.assertEqual(state.read_text(), "active")
        self.assertEqual(command("worktree", "list", "--porcelain").count("worktree "), 1)

if __name__ == "__main__":
    unittest.main()
