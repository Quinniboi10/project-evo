from project_evo.database import Database, PrimaryTableRow
from project_evo.error import ConfigError, DatabaseException, KillWorkerException
from project_evo.task import Task
from project_evo import config
from project_evo import evolve

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import unittest
import tempfile

class IslandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="evo-islands-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.db"
        self.db = Database(self.path, island_count=4)
        self.addCleanup(self.db.close)
        self.baseline = PrimaryTableRow("Baseline", "baseline", None, None, None, 10)
        self.db.insert_baseline(self.baseline)

    def add_attempt(self, island: int, score: float, parent: PrimaryTableRow|None=None) -> PrimaryTableRow:
        child = PrimaryTableRow.create_new_child(parent or self.baseline, Task.IMPROVE)
        child.score = score
        self.db.insert_attempt(child, island)
        return child

    def test_local_parents_and_stagnation(self):
        local = self.add_attempt(0, 20)
        self.add_attempt(1, 1000)
        self.add_attempt(0, 20)
        self.add_attempt(2, 2000)
        self.add_attempt(0, 15)
        self.assertEqual(self.db.iters_since_last_improvement(0), 2)
        self.assertEqual(self.db.iters_since_last_improvement(1), 0)
        self.assertEqual(self.db.iters_since_last_improvement(3), 0)
        choices = Mock(return_value=[1])
        with patch.object(config, "cfg", SimpleNamespace(softmax_temp=0.05)), patch("project_evo.database.random.choices", choices):
            self.assertEqual(self.db.weighted_sample(0).id, local.id)
        self.assertEqual(choices.call_args.args[0], [0, 1, 2, 3])
        self.assertEqual(choices.call_args.args[1][1], 1)
        self.add_attempt(0, 30)
        self.assertEqual(self.db.iters_since_last_improvement(0), 0)

    def test_foreign_elite_and_local_budget(self):
        local = self.add_attempt(0, 12)
        elite = self.add_attempt(1, 50)
        self.add_attempt(1, 50)
        self.add_attempt(1, 15)
        self.add_attempt(2, 100)
        source = Mock(return_value=(1,))
        with patch("project_evo.database.random.random", return_value=0), patch("project_evo.database.random.choice", source):
            refs = self.db.sample_inspirations(self.baseline, 2, 0, 0.1)
            self.assertEqual([row.id for row in refs], [local.id, elite.id])
            self.assertEqual(source.call_args.args[0], [(1,), (2,)])
            self.assertEqual([row.id for row in self.db.sample_inspirations(self.baseline, 1, 0, 1)], [elite.id])
            self.assertEqual(self.db.sample_inspirations(self.baseline, 0, 0, 1), [])
        with patch("project_evo.database.random.random", return_value=0.1):
            self.assertEqual([row.id for row in self.db.sample_inspirations(self.baseline, 8, 0, 0.1)], [local.id])
        with patch("project_evo.database.random.random") as draw:
            self.assertEqual([row.id for row in self.db.sample_inspirations(self.baseline, 8, 0, 0)], [local.id])
            draw.assert_not_called()

    def test_baseline_only_foreign_islands_and_parent_exclusion(self):
        self.assertEqual(self.db.sample_inspirations(self.baseline, 4, 0, 1), [])
        child = self.add_attempt(0, 11)
        self.assertEqual([row.id for row in self.db.sample_inspirations(self.baseline, 4, 0, 1)], [child.id])
        self.assertEqual([row.id for row in self.db.sample_inspirations(child, 4, 0, 1)], [self.baseline.id])

    def test_atomic_writes_and_local_parent_validation(self):
        foreign = self.add_attempt(1, 11)
        with self.assertRaises(DatabaseException):
            self.add_attempt(0, 12, foreign)
        before = self.db.select("SELECT * FROM evolve")
        self.db.cursor.execute("CREATE TRIGGER reject_member BEFORE INSERT ON evolve BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
        with self.assertRaises(DatabaseException):
            self.add_attempt(0, 12)
        self.assertEqual(self.db.select("SELECT * FROM evolve"), before)

    def test_resume_and_count_mismatch(self):
        self.add_attempt(2, 11)
        with closing(Database(self.path, island_count=4)) as resumed:
            self.assertEqual(resumed.select("SELECT COUNT(*) FROM evolve"), [(2,)])
        with self.assertRaisesRegex(DatabaseException, "must match"):
            Database(self.path, island_count=1)

    def test_concurrent_memberships(self):
        def insert(island: int):
            with closing(Database(self.path, island_count=4)) as db:
                child = PrimaryTableRow.create_new_child(self.baseline, Task.IMPROVE)
                child.score = 11
                db.insert_attempt(child, island)
                return island, child.id
        with ThreadPoolExecutor(max_workers=4) as pool:
            expected = list(pool.map(insert, [0, 1, 2, 3] * 3))
        self.assertEqual(set(self.db.select("SELECT island_id, id FROM evolve WHERE parent_id IS NOT NULL")), set(expected))

    def test_island_quality_bias_flattens_with_population_imbalance(self):
        choices = Mock(return_value=[0])
        with patch.object(config, "cfg", SimpleNamespace(softmax_temp=0.05)), patch("project_evo.database.random.choices", choices):
            self.db.sample_island()
            self.assertEqual(choices.call_args.args[1], [0.25] * 4)
            for island, score in enumerate((20, 10, 10, 10)):
                self.add_attempt(island, score)
            self.db.sample_island()
            balanced = choices.call_args.args[1]
            self.assertGreater(balanced[0], 0.99)
            for _ in range(18):
                self.add_attempt(0, 10)
            self.db.sample_island()
            imbalanced = choices.call_args.args[1]
            self.assertAlmostEqual(sum(imbalanced), 1)
            self.assertGreater(imbalanced[0], 0.25)
            self.assertLess(imbalanced[0], balanced[0])
            self.assertAlmostEqual(imbalanced[0], 0.1 * balanced[0] + 0.9 / 4)
            self.assertEqual(choices.call_args.args[0], [0, 1, 2, 3])

    def test_weighted_dispatch_and_replacement(self):
        calls = []
        def worker(island: int):
            calls.append(island)
            if len(calls) == 2:
                raise KillWorkerException("replace island 1")
        cfg = SimpleNamespace(concurrency=1, iterations=6, island_count=4, gnhf=True)
        with patch.object(config, "cfg", cfg), patch.object(evolve, "select_island", side_effect=[2, 1, 3, 3, 0, 2]), patch.object(evolve, "run_worker", side_effect=worker):
            self.assertEqual(evolve.run_iterations(), 0)
        self.assertEqual(calls, [2, 1, 1, 3, 3, 0, 2])

class IslandConfigTests(unittest.TestCase):
    def test_validation(self):
        cfg = config.Config.__new__(config.Config)
        cfg.softmax_temp = 0.05
        cfg.objective = "test"
        cfg.inspiration_count = 2
        cfg._routing = {"fallback": "test"}
        cfg.concurrency = 1
        cfg.iterations = 1
        cfg.max_fix_attempts = 0
        cfg.llm_timeout_sec = 10
        cfg.evaluation_concurrency = 1
        cfg.fallback_model = "test"
        cfg._providers = {"test": {"max_concurrency": 1, "adapter": "codex", "model": "test"}}
        validate = Mock()
        cfg.args = Namespace(smoke=True, smoke_session=SimpleNamespace(validate=validate))
        for count, probability in ((4, 0.1), (1, 0), (8, 1)):
            cfg.island_count, cfg.cross_island_inspiration_probability = count, probability
            cfg._validate()
        for count, probability in ((0, 0.1), (-1, 0.1), (True, 0.1), (1.5, 0.1), (4, True), (4, -0.1), (4, 1.1), (4, float("nan")), (4, float("inf")), (4, "0.1")):
            cfg.island_count, cfg.cross_island_inspiration_probability = count, probability
            with self.subTest(count=count, probability=probability), self.assertRaisesRegex(ConfigError, "general\\."):
                cfg._validate()

if __name__ == "__main__":
    unittest.main()
