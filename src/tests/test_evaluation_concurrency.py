from project_evo.error import ConfigError, EvalError
from project_evo.evaluation import EvaluationResult
from project_evo import config
from project_evo import evolve

from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import tempfile
import unittest

class EvaluationConcurrencyTests(unittest.TestCase):
    def test_configuration(self):
        template = (Path(__file__).resolve().parents[2] / "config.toml").read_text()
        with tempfile.TemporaryDirectory(prefix="evo-concurrency-") as temporary:
            root = Path(temporary)
            evaluator = root / "evaluate.py"
            evaluator.write_text("def evaluate(workspace):\n    return True, 1\n")
            settings = root / "config.toml"
            args = Namespace(config=str(settings), logfile=str(root / "test.log"), debug=False, objective="Faster", project_path=str(root), eval_file=str(evaluator), iterations=1, db=None, gnhf=False, smoke=True, smoke_session=SimpleNamespace(validate=Mock()))
            for value in (None, "1", "2", "9", "0", "-1", "true", "1.5", '"2"'):
                with self.subTest(value=value):
                    replacement = "# omitted" if value is None else f"evaluation_concurrency = {value}"
                    settings.write_text(template.replace("evaluation_concurrency = 1", replacement))
                    with patch.object(config.logging, "basicConfig"):
                        if value in (None, "1", "2", "9"):
                            cfg = config.Config(args)
                            expected = 5 if value is None else int(value)
                            self.assertEqual(cfg.evaluation_concurrency, expected)
                            self.assertFalse(cfg.reevaluate_idle)
                            for idle in (True, False, 1, 0, "true", None):
                                setattr(cfg, "reevaluate_idle", idle)
                                if type(idle) is bool:
                                    cfg._validate()
                                else:
                                    with self.assertRaisesRegex(ConfigError, "reevaluate_idle must be a boolean"):
                                        cfg._validate()
                            for _ in range(expected):
                                self.assertTrue(cfg.evaluation_semaphore.acquire(blocking=False))
                            self.assertFalse(cfg.evaluation_semaphore.acquire(blocking=False))
                            for _ in range(expected):
                                cfg.evaluation_semaphore.release()
                        else:
                            with self.assertRaisesRegex(ConfigError, "evaluation_concurrency must be a positive integer"):
                                config.Config(args)

    def test_concurrent_evaluations_respect_limit(self):
        for limit in (1, 2):
            with self.subTest(limit=limit):
                lock = Lock()
                all_waiting = Event()
                saturated = Event()
                release = Event()
                callers = limit + 2
                attempted = 0
                active = 0
                peak = 0

                class ObservedSemaphore(BoundedSemaphore):
                    def __enter__(self, blocking: bool=True, timeout: float|None=None):
                        nonlocal attempted
                        with lock:
                            attempted += 1
                            if attempted == callers:
                                all_waiting.set()
                        return super().__enter__(blocking, timeout)

                def evaluate(workspace: Path) -> tuple[bool, float]:
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(peak, active)
                        if active == limit:
                            saturated.set()
                    try:
                        if not release.wait(5):
                            raise RuntimeError("Test did not release evaluators")
                        return True, 1
                    finally:
                        with lock:
                            active -= 1

                cfg = SimpleNamespace(eval_fn=evaluate, evaluation_semaphore=ObservedSemaphore(limit))
                with patch.object(config, "cfg", cfg), ThreadPoolExecutor(max_workers=callers) as pool:
                    futures = [pool.submit(evolve._evaluate, Path("workspace")) for _ in range(callers)]
                    try:
                        self.assertTrue(all_waiting.wait(5))
                        self.assertTrue(saturated.wait(5))
                        with lock:
                            self.assertEqual(active, limit)
                    finally:
                        release.set()
                    for future in futures:
                        self.assertEqual(future.result(timeout=5), EvaluationResult(True, 1))
                self.assertEqual(peak, limit)

    def test_slot_released_for_every_outcome(self):
        outcomes = [(True, 1), (False, 0), (True, 0), RuntimeError("evaluation broke")]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                gate = BoundedSemaphore(1)

                def evaluate(workspace: Path):
                    self.assertFalse(gate.acquire(blocking=False))
                    if isinstance(outcome, RuntimeError):
                        raise outcome
                    return outcome

                with patch.object(config, "cfg", SimpleNamespace(eval_fn=evaluate, evaluation_semaphore=gate)):
                    if isinstance(outcome, RuntimeError):
                        with self.assertRaisesRegex(RuntimeError, "evaluation broke"):
                            evolve._evaluate(Path("workspace"))
                    elif outcome == (True, 0):
                        with self.assertRaises(EvalError):
                            evolve._evaluate(Path("workspace"))
                    else:
                        evolve._evaluate(Path("workspace"))
                self.assertTrue(gate.acquire(blocking=False))
                gate.release()

if __name__ == "__main__":
    unittest.main()
