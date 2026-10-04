from project_evo.database import Database, PrimaryTableRow
from project_evo.error import EvalError
from project_evo.evaluation import EvaluationResult, normalize_result, format_feedback
from project_evo.prompt import build_run_fix_prompt
from project_evo import config
from project_evo import evolve
from project_evo import git
from project_evo import llm

from importlib.machinery import SourceFileLoader
from typing import cast
from pathlib import Path
from threading import BoundedSemaphore
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

    def test_result_contract(self):
        self.assertEqual(normalize_result((True, 2)), EvaluationResult(True, 2))
        result = EvaluationResult(False, float("nan"), "failed check")
        self.assertIs(normalize_result(result), result)
        for score in (0, -1, float("inf"), float("-inf")):
            self.assertEqual(normalize_result((False, score)).score, score)
        invalid: list[object] = [None, [True, 2], (True,), (True, 2, "text"), (1, 2), (True, True), (False, None), (True, "2"), EvaluationResult(False, 0, cast(str, None))]
        for score in (0, -1, float("nan"), float("inf"), float("-inf")):
            invalid.extend([(True, score), EvaluationResult(True, score)])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(EvalError):
                normalize_result(value)

    def test_feedback_formatting_and_prompt(self):
        for text in ("", " \n\t"):
            self.assertEqual(format_feedback(text), "")
        for size in (7999, 8000):
            text = "a" * size
            self.assertEqual(format_feedback(text), text)
        text = "a" * 4000 + "OMITTED" + "z" * 4000
        bounded = format_feedback(text)
        self.assertTrue(bounded.startswith("a" * 4000))
        self.assertTrue(bounded.endswith("z" * 4000))
        self.assertIn("[Feedback truncated; middle omitted.]", bounded)
        self.assertNotIn("OMITTED", bounded)
        self.assertIn("truncated", format_feedback("a" * 8001))
        parent = PrimaryTableRow("Baseline", "baseline", None, None, None, 10)
        child = PrimaryTableRow("Child", "child", None, None, None, 5)
        with patch.object(config, "cfg", SimpleNamespace(branch_base="evo", objective="Faster")):
            generic = build_run_fix_prompt(parent, child)
            self.assertEqual(build_run_fix_prompt(parent, child, " \n"), generic)
            self.assertNotIn("BEGIN EVALUATOR FEEDBACK", generic)
            self.assertIn(bounded, build_run_fix_prompt(parent, child, text))

    def test_integer_scores_are_storable_and_overflow_is_an_evaluation_error(self):
        result = normalize_result((True, 10**20))
        with closing(Database(self.root / "integer.db", island_count=1)) as db:
            db.insert_baseline(PrimaryTableRow("Baseline", "base", None, None, None, result.score))
            self.assertEqual(db.select("SELECT score FROM evolve"), [(1e20,)])
        with self.assertRaises(EvalError):
            normalize_result((True, 10**400))

    def test_external_evaluator_import(self):
        evaluator = self.root / "evaluate.py"
        evaluator.write_text('from project_evo.evaluation import EvaluationResult\ndef evaluate(workspace):\n    return EvaluationResult(False, 0, "external diagnostic")\n')
        module = SourceFileLoader("_test_external_eval", str(evaluator)).load_module()
        self.assertEqual(normalize_result(module.evaluate(self.root)), EvaluationResult(False, 0, "external diagnostic"))

    def test_baseline_feedback_and_success(self):
        for passed in (False, True):
            path = self.root / f"typed-baseline-{passed}.db"
            feedback = "a" * 4000 + "OMITTED" + "z" * 4000
            def check_baseline(workspace: Path):
                self.assertFalse(cfg.evaluation_semaphore.acquire(blocking=False))
                return EvaluationResult(passed, 10, feedback)

            evaluate = Mock(side_effect=check_baseline)
            bootstrap = Mock()
            cfg = SimpleNamespace(db_file=path, island_count=1, project_root=self.root, eval_fn=evaluate, evaluation_semaphore=BoundedSemaphore(1), branch_base="evo")
            with patch.object(config, "cfg", cfg), patch.object(git, "bootstrap", bootstrap):
                if passed:
                    evolve.init_db()
                    bootstrap.assert_called_once()
                else:
                    with self.assertRaises(EvalError) as error:
                        evolve.init_db()
                    self.assertEqual(str(error.exception), "Baseline failed to pass evaluate()\n" + format_feedback(feedback))
                    bootstrap.assert_not_called()
            with closing(Database(path, island_count=1)) as db:
                self.assertEqual(db.select("SELECT score FROM evolve"), [(10,)] if passed else [])

    def test_worker_feedback_retries_and_cleanup(self):
        outcomes = [EvaluationResult(True, 5, "unused success feedback"), EvaluationResult(False, 0, "final failure"), RuntimeError("evaluator broke")]
        for index, outcome in enumerate(outcomes):
            with self.subTest(outcome=outcome):
                path = self.root / f"feedback-{index}.db"
                with closing(Database(path, island_count=1)) as db:
                    db.insert_baseline(PrimaryTableRow("Baseline", "baseline", None, None, None, 10))
                results = iter([EvaluationResult(False, 0, "FIRST FAILURE"), EvaluationResult(False, 0, "SECOND FAILURE"), outcome])

                def check_evaluation(workspace: Path):
                    self.assertFalse(cfg.evaluation_semaphore.acquire(blocking=False))
                    result = next(results)
                    if isinstance(result, RuntimeError):
                        raise result
                    return result

                def check_agent(*args):
                    self.assertTrue(cfg.evaluation_semaphore.acquire(blocking=False))
                    cfg.evaluation_semaphore.release()
                    return "test-model"

                evaluate = Mock(side_effect=check_evaluation)
                route = Mock(side_effect=check_agent)
                cleanup = Mock()
                query = Mock()
                workspace = self.root / "workspace"
                cfg = SimpleNamespace(db_file=path, island_count=1, exploration_probability=0.3, inspiration_count=0, cross_island_inspiration_probability=0, softmax_temp=0.05, eval_fn=evaluate, evaluation_semaphore=BoundedSemaphore(1), max_fix_attempts=2, objective="Faster", branch_base="evo")
                with patch.object(config, "cfg", cfg), patch.object(git, "create_new_workspace", return_value=workspace), patch.object(git, "delete_workspace", cleanup), patch.object(llm, "run_agent", route), patch.object(llm, "get_attempt_name", return_value="Attempt"):
                    if isinstance(outcome, RuntimeError):
                        with self.assertRaisesRegex(RuntimeError, "evaluator broke"):
                            evolve.run_worker(0, query)
                    else:
                        evolve.run_worker(0, query)
                self.assertEqual(evaluate.call_count, 3)
                self.assertEqual(route.call_count, 3)
                self.assertTrue(all(call.args[3] is query for call in route.call_args_list))
                first = route.call_args_list[1].args[1]
                second = route.call_args_list[2].args[1]
                self.assertIn("FIRST FAILURE", first)
                self.assertNotIn("SECOND FAILURE", first)
                self.assertIn("SECOND FAILURE", second)
                self.assertNotIn("FIRST FAILURE", second)
                cleanup.assert_called_once_with(workspace)
                with closing(Database(path, island_count=1)) as db:
                    expected = [(10,), (5,)] if index == 0 else [(10,)]
                    self.assertEqual(db.select("SELECT score FROM evolve ORDER BY id"), expected)

    def test_invalid_baseline_never_creates_branch_or_row(self):
        results = [(False, 10), (True, 0), (True, -1), (True, float("nan")), (True, float("inf")), (True, -float("inf"))]
        for index, result in enumerate(results):
            with self.subTest(result=result):
                path = self.root / f"baseline-{index}.db"
                evaluate = Mock(return_value=result)
                bootstrap = Mock()
                cfg = SimpleNamespace(db_file=path, island_count=1, project_root=self.root, eval_fn=evaluate, evaluation_semaphore=BoundedSemaphore(1), branch_base="evo")
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
                    cfg = SimpleNamespace(db_file=path, island_count=1, exploration_probability=0.3, inspiration_count=0, cross_island_inspiration_probability=0, softmax_temp=0.05, eval_fn=evaluate, evaluation_semaphore=BoundedSemaphore(1), max_fix_attempts=1, objective="Faster", branch_base="evo")
                    with patch.object(config, "cfg", cfg), patch.object(git, "create_new_workspace", return_value=workspace), patch.object(git, "delete_workspace", cleanup), patch.object(llm, "run_agent", route):
                        with self.assertRaises(EvalError):
                            evolve.run_worker(0)
                    self.assertEqual(evaluate.call_count, len(results))
                    self.assertEqual(route.call_count, len(results))
                    cleanup.assert_called_once_with(workspace)
                    with closing(Database(path, island_count=1)) as db:
                        self.assertEqual(db.select("SELECT name, score FROM evolve"), [("Baseline", 10)])

if __name__ == "__main__":
    unittest.main()
