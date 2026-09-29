from src.evaluation import EvaluationResult

from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch

import subprocess
import unittest

class RuntimeEvaluatorTests(unittest.TestCase):
    def test_runtime_examples(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("command-runtime", "compiled-runtime"):
            module = SourceFileLoader(f"_test_{name.replace('-', '_')}", str(root / "examples" / name / "evaluate.py")).load_module()
            command = module.PREFLIGHT_COMMANDS[0] if name == "compiled-runtime" else module.TARGET_COMMAND
            errors = [
                subprocess.CalledProcessError(7, command, output="test output", stderr="test error"),
                subprocess.TimeoutExpired(command, 600, output=b"partial output\xff", stderr=b"partial error"),
                RuntimeError("unexpected failure")
            ]
            for error in errors:
                with self.subTest(name=name, error=error):
                    run = Mock(side_effect=error)
                    with patch.object(module.subprocess, "run", run):
                        result = module.evaluate(root)
                    self.assertIsInstance(result, EvaluationResult)
                    self.assertFalse(result.passed)
                    self.assertEqual(result.score, 0)
                    run.assert_called_once_with(command, cwd=root, input=None, text=True, capture_output=True, check=True, timeout=600)
                    if isinstance(error, subprocess.CalledProcessError):
                        self.assertIn("Exit status: 7", result.feedback)
                        self.assertIn("stdout:\ntest output", result.feedback)
                        self.assertIn("stderr:\ntest error", result.feedback)
                        self.assertIn(repr(command), result.feedback)
                    elif isinstance(error, subprocess.TimeoutExpired):
                        self.assertIn("Timed out after 600 seconds", result.feedback)
                        self.assertIn("stdout:\npartial output\ufffd", result.feedback)
                        self.assertIn("stderr:\npartial error", result.feedback)
                        self.assertIn(repr(command), result.feedback)
                    else:
                        self.assertEqual(result.feedback, "RuntimeError: unexpected failure")
            for output, expected in ((b"first\nsecond\xff", "first\nsecond\ufffd"), (None, "")):
                with self.subTest(name=name, timeout_output=output):
                    error = subprocess.TimeoutExpired(command, 600, output=output, stderr=output)
                    with patch.object(module.subprocess, "run", side_effect=error):
                        result = module.evaluate(root)
                    self.assertEqual(result, EvaluationResult(False, 0, f"Command: {command!r}\nTimed out after 600 seconds\nstdout:\n{expected}\nstderr:\n{expected}"))
            # One timed run; compilation time remains excluded from the score.
            times = [0, 0, 0, 2, 6]
            if name == "compiled-runtime":
                times = [0, 10, 10, 20] + times
            run = Mock()
            with self.subTest(name=name, success=True), patch.object(module.subprocess, "run", run), patch.object(module.time, "monotonic", side_effect=times):
                self.assertEqual(module.evaluate(root), EvaluationResult(True, 0.5))
            self.assertEqual(run.call_count, 3 if name == "compiled-runtime" else 1)

if __name__ == "__main__":
    unittest.main()
