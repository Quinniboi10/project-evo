from project_evo.error import ConfigError
from project_evo import config

from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import tempfile
import unittest
import sys

class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evo-config-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.template = (Path(config.__file__).parent / "configs" / "default.toml").read_text()
        self.settings = self.root / "config.toml"
        evaluator = self.root / "evaluate.py"
        evaluator.write_text("def evaluate(workspace):\n    return True, 1\n")
        self.args = Namespace(config=str(self.settings), logfile=str(self.root / "test.log"), debug=False, objective="Faster", project_path=str(self.root), eval_file=str(evaluator), iterations=1, db=None, gnhf=False)

    def load(self, text: str) -> config.Config:
        self.settings.write_text(text)
        with patch.object(config.logging, "basicConfig"):
            return config.Config(self.args)

    def test_invalid_numeric_settings_fail_before_provider_setup(self):
        settings = {
            "exploration_probability = 0.3": ("exploration_probability", ["-0.1", "1.1", "nan", "inf", "-inf", "true", '\"0.5\"']),
            "temperature = 0.05": ("temperature", ["0", "-1", "nan", "inf", "-inf", "true", '\"0.5\"']),
            "concurrency = 5": ("concurrency", ["0", "-1", "true", "1.5", '\"2\"']),
            "max_fix_attempts = 3": ("max_fix_attempts", ["-1", "true", "1.5", '\"2\"']),
            "llm_timeout_sec  = 10800": ("llm_timeout_sec", ["0", "-1", "true", "1.5", '\"2\"'])
        }
        for original, (key, values) in settings.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ConfigError, key):
                    self.load(self.template.replace(original, f"{key} = {value}"))

    def test_iteration_bounds(self):
        self.args.iterations = -1
        with self.assertRaisesRegex(ConfigError, "iterations"):
            self.load(self.template)
        self.args.iterations = 0
        cfg = self.load(self.template.replace("max_fix_attempts = 3", "max_fix_attempts = 0"))
        self.assertEqual(cfg.iterations, 0)
        self.assertEqual(cfg.max_fix_attempts, 0)

    def test_invalid_provider_settings(self):
        variants = [
            ('fallback    = "sol"', 'fallback = "missing"', "missing"),
            ('fallback    = "sol"', 'fallback = ["sol"]', "routing model"),
            ('adapter = "codex"', 'adapter = "unknown"', "adapter"),
            ('model = "gpt-5.6-sol"', 'model = ""', "model"),
            ('max_concurrency = 5', 'max_concurrency = -1', "max_concurrency"),
            ('max_concurrency = 5', 'max_concurrency = true', "max_concurrency"),
            ('max_concurrency = 5', 'max_concurrency = 1.5', "max_concurrency"),
            ('model = "gpt-5.6-sol"', 'model = "gpt-5.6-sol"\nextra_args = "--verbose"', "extra_args"),
            ('model = "gpt-5.6-sol"', 'model = "gpt-5.6-sol"\nextra_args = [1]', "extra_args")
        ]
        for old, new, message in variants:
            with self.subTest(new=new), self.assertRaisesRegex(ConfigError, message):
                self.load(self.template.replace(old, new))

    def test_default_configuration(self):
        cfg = self.load(self.template)
        self.assertEqual(cfg.concurrency, 5)
        self.assertEqual(cfg.exploration_probability, 0.3)
        self.assertEqual(cfg.extra_args("sol"), [])

    def test_exploration_probability_defaults_and_boundaries(self):
        cfg = self.load(self.template.replace("exploration_probability = 0.3", ""))
        self.assertEqual(cfg.exploration_probability, 0.3)
        for value in ("0", "1", "0.0", "1.0", "0.7"):
            with self.subTest(value=value):
                cfg = self.load(self.template.replace("exploration_probability = 0.3", f"exploration_probability = {value}"))
                self.assertEqual(cfg.exploration_probability, float(value))

    def test_zero_primary_capacity_uses_fallback_configuration(self):
        cfg = self.load(self.template.replace("max_concurrency = 3", "max_concurrency = 0"))
        self.assertEqual(cfg.max_concurrency(cfg.exploration_model), 0)
        self.assertEqual(cfg.max_concurrency(cfg.improvement_model), 0)
        self.assertGreaterEqual(cfg.max_concurrency(cfg.fallback_model), cfg.concurrency)

    def test_evaluator_reload_does_not_reuse_previous_function(self):
        self.load(self.template)
        evaluator = self.root / "another_evaluator.py"
        self.args.eval_file = str(evaluator)
        for contents in ("# No evaluate function\n", "evaluate = 42\n"):
            evaluator.write_text(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ConfigError, "evaluate"):
                self.load(self.template)

    def test_provider_confirmation_accepts_normalized_answers(self):
        cfg = self.load(self.template)
        answers = Mock(side_effect=["invalid", " Y ", "n"])
        with patch("builtins.input", answers), self.assertRaises(SystemExit) as raised:
            cfg._confirm_providers()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(answers.call_count, 3)

    def test_objective_file_and_provider_confirmation(self):
        objective = self.root / "objective.txt"
        objective.write_text("Improve λ throughput")
        self.args.objective = None
        self.args.objective_file = str(objective)
        answers = Mock(return_value="Y")
        with patch("builtins.input", answers), redirect_stdout(StringIO()) as output:
            cfg = self.load(self.template)
            answers.assert_not_called()
            cfg._confirm_providers()
        self.assertEqual(cfg.objective, "Improve λ throughput")
        self.assertEqual(answers.call_count, 2)
        self.assertIn("OpenCode", output.getvalue())

    def test_evaluator_import_failure_is_cleaned_up(self):
        evaluator = Path(self.args.eval_file)
        evaluator.write_text('raise RuntimeError("broken import")\n')
        with self.assertRaisesRegex(RuntimeError, "broken import"):
            self.load(self.template)
        self.assertNotIn("_workspace_eval_module", sys.modules)
        evaluator.write_text('from __future__ import annotations\nfrom dataclasses import dataclass\n@dataclass\nclass Result:\n    score: float = 2\ndef evaluate(workspace):\n    return True, Result().score\n')
        cfg = self.load(self.template)
        self.assertEqual(cfg.eval_fn(self.root), (True, 2))

if __name__ == "__main__":
    unittest.main()
