from src.database import Database, PrimaryTableRow
from src.error import EvalError
from src import config
from src import evolve
from src import git
from src import llm

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import closing

import tempfile
import unittest

class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evo-evaluation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_invalid_baseline_never_creates_branch_or_row(self):
        results = [(False, 10), (True, 0), (True, -1), (True, float("nan")), (True, float("inf")), (True, -float("inf"))]
        for index, result in enumerate(results):
            with self.subTest(result=result):
                path = self.root / f"baseline-{index}.db"
                evaluate = Mock(return_value=result)
                bootstrap = Mock()
                cfg = SimpleNamespace(db_file=path, island_count=1, project_root=self.root, eval_fn=evaluate, branch_base="evo")
                with patch.object(config, "cfg", cfg), patch.object(git, "bootstrap", bootstrap):
                    with self.assertRaises(EvalError):
                        evolve.init_db()
                evaluate.assert_called_once_with(self.root)
                bootstrap.assert_not_called()
                with closing(Database(path, island_count=1)) as db:
                    self.assertEqual(db.select("SELECT COUNT(*) FROM evolve"), [(0,)])

    def test_invalid_attempt_scores_are_not_stored_and_workspace_is_cleaned(self):
        for repaired in (False, True):
            for index, score in enumerate((0, -1, float("nan"), float("inf"), -float("inf"))):
                with self.subTest(repaired=repaired, score=score):
                    path = self.root / f"attempt-{repaired}-{index}.db"
                    with closing(Database(path, island_count=1)) as db:
                        db.insert_baseline(PrimaryTableRow("Baseline", "baseline", None, None, None, 10))
                    results = [(False, 0), (True, score)] if repaired else [(True, score)]
                    evaluate = Mock(side_effect=results)
                    route = Mock(return_value="test-model")
                    cleanup = Mock()
                    workspace = self.root / "workspace"
                    cfg = SimpleNamespace(db_file=path, island_count=1, inspiration_count=0, cross_island_inspiration_probability=0, softmax_temp=0.05, eval_fn=evaluate, max_fix_attempts=1, objective="Faster", branch_base="evo")
                    with patch.object(config, "cfg", cfg), patch.object(git, "create_new_workspace", return_value=workspace), patch.object(git, "delete_workspace", cleanup), patch.object(llm, "route_prompt", route):
                        with self.assertRaises(EvalError):
                            evolve.run_worker(0)
                    self.assertEqual(evaluate.call_count, len(results))
                    self.assertEqual(route.call_count, len(results))
                    cleanup.assert_called_once_with(workspace)
                    with closing(Database(path, island_count=1)) as db:
                        self.assertEqual(db.select("SELECT name, score FROM evolve"), [("Baseline", 10)])

if __name__ == "__main__":
    unittest.main()
