from error import KillPoolException, KillWorkerException
from task import Task
import config
import git
import llm
import main
import smoke

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import closing

import unittest
import subprocess
import tempfile
import sqlite3
import shutil
import logging
import sys
import os

ROOT = Path(__file__).resolve().parent.parent

class SmokeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evo-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.config = self.root / "config.toml"
        self.config.write_text((ROOT / "config.toml").read_text())

    def run_smoke(self, *args: str, env: dict|None = None):
        result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--smoke", "-c", str(self.config), *args], cwd=self.root, env=env, text=True, capture_output=True, timeout=30)
        paths = [line.removeprefix("Smoke artifacts: ") for line in result.stdout.splitlines() if line.startswith("Smoke artifacts: ")]
        self.assertEqual(len(paths), 1, result.stderr)
        artifacts = Path(paths[0])
        self.addCleanup(shutil.rmtree, artifacts)
        return result, artifacts

    def test_retries_commits_and_logs(self):
        for retries, successful in ((0, 2), (1, 4), (3, 4)):
            with self.subTest(retries=retries):
                self.config.write_text((ROOT / "config.toml").read_text().replace("max_fix_attempts = 3", f"max_fix_attempts = {retries}"))
                result, artifacts = self.run_smoke("-i", "6", "--debug", "--gnhf")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"{successful} successful", result.stdout)
                with closing(sqlite3.connect(artifacts / "database" / "smoke.db")) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM evolve").fetchone()[0], successful + 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM evolve WHERE score <= 0").fetchone()[0], 0)
                log = (artifacts / "logs" / "project-evo.log").read_text()
                for message in ("SMOKE PASSED", "OUTBOUND:", "INBOUND STDOUT: SMOKE:", "Starting ", "Worker finished", "Skipping child"):
                    self.assertIn(message, log)
                self.assertEqual("retrying" in log, retries > 0)

    def test_external_inputs_and_git_environment_are_protected(self):
        project = self.root / "original"
        project.mkdir()
        sentinel = project / "keep.txt"
        sentinel.write_text("unchanged")
        evaluator = self.root / "do-not-import.py"
        evaluator.write_text("raise RuntimeError('External evaluator executed')\n")
        database = self.root / "existing.db"
        database.write_text("not a smoke database")
        logfile = self.root / "existing.log"
        logfile.write_text("existing log")
        objective = self.root / "objective.txt"
        objective.write_text("Smoke objective from a file")
        hooks = self.root / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 99\n")
        hook.chmod(0o755)
        global_config = self.root / "gitconfig"
        global_config.write_text(f"[core]\n hooksPath = {hooks}\n[commit]\n gpgsign = true\n")
        env = os.environ.copy()
        env.update({"GIT_DIR": str(project), "GIT_WORK_TREE": str(project), "GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(hooks)})
        originals = {path: path.read_bytes() for path in (sentinel, evaluator, database, logfile, objective, global_config, hook)}
        result, artifacts = self.run_smoke(str(project), str(evaluator), "--objective_file", str(objective), "--db", str(database), "--logfile", str(logfile), "-i", "3", "--debug", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(project.iterdir()), [sentinel])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue((artifacts / "database" / database.name).exists())
        log = (artifacts / "logs" / logfile.name).read_text()
        self.assertIn(objective.read_text(), log)
        self.assertFalse((self.root / "project-evo.log").exists())

    def test_invalid_git_settings_are_rejected(self):
        for key, value in (("workspace_base", "../outside"), ("workspace_base", "/tmp/outside"), ("workspace_base", ".git"), ("branch_base", "$(touch escaped)"), ("branch_base", "bad..branch")):
            with self.subTest(key=key, value=value):
                original = "workspaces" if key == "workspace_base" else "project-evo"
                self.config.write_text((ROOT / "config.toml").read_text().replace(f'{key} = "{original}"', f'{key} = "{value}"'))
                result, artifacts = self.run_smoke("-i", "1")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("SMOKE FAILED", result.stdout)
                self.assertFalse((artifacts / "database" / "smoke.db").exists())

    def test_zero_iterations_and_nested_workspaces(self):
        self.config.write_text(self.config.read_text().replace('workspace_base = "workspaces"', 'workspace_base = "nested/workspaces"'))
        result, artifacts = self.run_smoke("-i", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 attempts", result.stdout)
        self.assertNotIn("OUTBOUND:", (artifacts / "logs" / "project-evo.log").read_text())
        result, _ = self.run_smoke("-i", "3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_cli_still_requires_inputs(self):
        for args in (("-i", "1"), ("project", "eval.py", "-i", "1")):
            result = subprocess.run([sys.executable, str(ROOT / "main.py"), *args], cwd=self.root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("required without --smoke", result.stderr)
        self.assertFalse((self.root / "project-evo.log").exists())

    def test_existing_database_validation_and_verification_failure(self):
        self.config.write_text((ROOT / "config.toml").read_text().replace("island_count = 4", "island_count = 1"))
        script = '''import main
import smoke
import config
import sqlite3
import sys

def run(args):
    result = main.run(args)
    main.init_db() # Validate the existing database and its branches.
    if sys.argv[2] == "corrupt":
        with sqlite3.connect(config.cfg.db_file) as db:
            db.execute("UPDATE evolve SET score = 999")
    elif sys.argv[2] in ("parent", "missing_parent"):
        with sqlite3.connect(config.cfg.db_file) as db:
            children = db.execute("SELECT id FROM evolve WHERE parent_id IS NOT NULL ORDER BY id").fetchall()
            parent = children[-1][0] if sys.argv[2] == "parent" else -1
            db.execute("UPDATE evolve SET parent_id = ? WHERE id = ?", (parent, children[0][0]))
    return result

args = main.build_parser().parse_args(["--smoke", "-i", "3", "-c", sys.argv[1]])
sys.exit(smoke.run(args, run))
'''
        for mode, message in (("resume", None), ("corrupt", "Smoke commit does not match its database score"), ("parent", "Smoke commit ancestry does not match its database parent"), ("missing_parent", "Smoke database references a missing parent")):
            result = subprocess.run([sys.executable, "-c", script, str(self.config), mode], cwd=ROOT, text=True, capture_output=True, timeout=30)
            artifacts = Path(next(line.removeprefix("Smoke artifacts: ") for line in result.stdout.splitlines() if line.startswith("Smoke artifacts: ")))
            self.addCleanup(shutil.rmtree, artifacts)
            self.assertEqual(result.returncode, 1 if message else 0, result.stdout + result.stderr)
            if message:
                self.assertIn(message, result.stdout)
            self.assertTrue((artifacts / "logs" / "project-evo.log").exists())

    def test_invalid_config_and_unsupported_adapter_fail_without_queries(self):
        for old, new in (("concurrency = 5", "concurrency = 0"), ("max_fix_attempts = 3", "max_fix_attempts = -1"), ('adapter = "opencode"', 'adapter = "unsupported"')):
            with self.subTest(new=new):
                self.config.write_text((ROOT / "config.toml").read_text().replace(old, new))
                result, artifacts = self.run_smoke("-i", "3")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("SMOKE PASSED", result.stdout)
                self.assertTrue((artifacts / "logs" / "project-evo.log").exists())

    def test_configured_inspiration_counts(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                self.config.write_text((ROOT / "config.toml").read_text().replace("inspiration_count = 2", f"inspiration_count = {count}").replace("concurrency = 5", "concurrency = 1").replace("island_count = 4", "island_count = 1"))
                result, artifacts = self.run_smoke("-i", "6", "--debug")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                log = (artifacts / "logs" / "project-evo.log").read_text()
                counts = [section.count("BEGIN INSPIRATION REFERENCE") for section in log.split("OUTBOUND:")[1:]]
                self.assertEqual(max(counts), count)

    def test_invalid_or_missing_inspiration_count(self):
        for value in ("-1", "1.5", "true", '"2"', None):
            with self.subTest(value=value):
                replacement = f"inspiration_count = {value}" if value is not None else "# inspiration_count omitted"
                self.config.write_text((ROOT / "config.toml").read_text().replace("inspiration_count = 2", replacement))
                result, _ = self.run_smoke("-i", "1")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("general.inspiration_count is required and must be a nonnegative integer", result.stdout)

class RoutingTests(unittest.TestCase):
    def setUp(self):
        cfg = SimpleNamespace(
            args=SimpleNamespace(smoke=True, smoke_session=Mock()),
            exploration_model="explore", improvement_model="improve", fallback_model="fallback",
            adapter=lambda model: "codex", model_full_name=lambda model: model,
            extra_args=lambda model: [], max_concurrency=lambda model: 1,
            llm_timeout_sec=10,
        )
        self.cfg = cfg
        self.query = cfg.args.smoke_session.query
        self.query.return_value = subprocess.CompletedProcess(["smoke"], 0, "simulated", "")
        self.autocommit = Mock()
        self.codex = Mock()
        self.opencode = Mock()
        for context in (patch.object(config, "cfg", cfg), patch.dict(llm.running_processes, {}, clear=True), patch.object(git, "autocommit", self.autocommit), patch.object(llm, "_codex", self.codex), patch.object(llm, "_opencode", self.opencode)):
            context.start()
            self.addCleanup(context.stop)

    def test_tasks_adapters_and_fallback(self):
        for adapter in ("codex", "opencode"):
            self.cfg.adapter = lambda model: adapter
            self.assertEqual(llm.route_prompt(Path("workspace"), "prompt", Task.EXPLORE), "explore")
            self.assertEqual(llm.route_prompt(Path("workspace"), "prompt", Task.IMPROVE), "improve")
        llm.running_processes["explore"] = 1
        self.assertEqual(llm.route_prompt(Path("workspace"), "prompt", Task.EXPLORE), "fallback")
        self.assertEqual(llm.running_processes["fallback"], 0)
        self.codex.assert_not_called()
        self.opencode.assert_not_called()

    def test_nonzero_timeout_and_unsupported_adapter(self):
        self.query.return_value = subprocess.CompletedProcess(["smoke"], 2, "out", "error")
        with self.assertRaises(subprocess.CalledProcessError):
            llm.route_prompt(Path("workspace"), "prompt", Task.IMPROVE)
        self.query.side_effect = subprocess.TimeoutExpired("smoke", 10)
        self.assertEqual(llm.route_prompt(Path("workspace"), "prompt", Task.IMPROVE), "improve")
        self.cfg.adapter = lambda model: "unsupported"
        with self.assertRaises(KillPoolException):
            llm.route_prompt(Path("workspace"), "prompt", Task.IMPROVE)
        self.assertEqual(llm.running_processes["improve"], 0)
        self.assertEqual(self.autocommit.call_count, 3)

    def test_normal_mode_uses_real_adapter_boundary(self):
        self.cfg.args.smoke = False
        for adapter, mock in (("codex", self.codex), ("opencode", self.opencode)):
            self.cfg.adapter = lambda model: adapter
            mock.return_value = subprocess.CompletedProcess([adapter], 0, "out", "")
            llm.route_prompt(Path("workspace"), "prompt", Task.IMPROVE)
            mock.assert_called_once()
        self.query.assert_not_called()

    def test_agent_requirements_are_skipped_only_in_smoke_mode(self):
        self.cfg.models = {"improve"}
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/git" if name == "git" else None):
            main.check_requirements()
            self.cfg.args.smoke = False
            with self.assertRaises(KillPoolException):
                main.check_requirements()

class WorkerFailureTests(unittest.TestCase):
    def test_pool_failures_and_replacement(self):
        for gnhf, error, expected in ((False, KillWorkerException, 1), (True, KillWorkerException, 0), (False, KillPoolException, 1), (True, KillPoolException, 1), (True, RuntimeError, 1), (False, KeyboardInterrupt, 0)):
            with self.subTest(gnhf=gnhf, error=error):
                cfg = SimpleNamespace(concurrency=1, iterations=1, island_count=4, gnhf=gnhf)
                with patch.object(config, "cfg", cfg), patch.object(main, "select_island", return_value=0), patch.object(main, "run_worker", side_effect=[error("test"), None]) as worker:
                    self.assertEqual(main.run_iterations(), expected)
                    self.assertEqual(worker.call_count, 2 if gnhf and error is KillWorkerException else 1)

    def test_cancellation_while_waiting(self):
        cfg = SimpleNamespace(concurrency=1, iterations=1, island_count=4, gnhf=False)
        with patch.object(config, "cfg", cfg), patch.object(main, "select_island", return_value=0), patch.object(main, "run_worker"), patch.object(main, "wait", side_effect=KeyboardInterrupt):
            self.assertEqual(main.run_iterations(), 0)

    def test_workspace_cleanup_on_evaluation_error(self):
        from database import PrimaryTableRow
        cfg = SimpleNamespace(db_file="unused", inspiration_count=2, cross_island_inspiration_probability=0.1, eval_fn=Mock(side_effect=RuntimeError("evaluation failed")))
        parent = PrimaryTableRow("Baseline", "baseline", None, None, None, 1)
        parent.id = 1
        with patch.object(config, "cfg", cfg), patch.object(main, "Database") as database, patch.object(git, "create_new_workspace", return_value=Path("workspace")), patch.object(git, "delete_workspace") as cleanup, patch.object(llm, "route_prompt"), patch.object(main, "build_prompt", return_value="prompt"):
            database.return_value.weighted_sample.return_value = parent
            database.return_value.sample_inspirations.return_value = []
            with self.assertRaisesRegex(RuntimeError, "evaluation failed"):
                main.run_worker(0)
            cleanup.assert_called_once_with(Path("workspace"))
            database.return_value.insert_attempt.assert_not_called()

    def test_git_environment_restored_on_failure(self):
        with patch.dict(os.environ, {"GIT_DIR": "original", "GIT_CONFIG_COUNT": "2"}):
            original = {key: value for key, value in os.environ.items() if key.startswith("GIT_")}
            with self.assertRaises(RuntimeError):
                with smoke.isolated_git():
                    self.assertNotIn("GIT_DIR", os.environ)
                    raise RuntimeError("test")
            self.assertEqual({key: value for key, value in os.environ.items() if key.startswith("GIT_")}, original)

if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
