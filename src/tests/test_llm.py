from project_evo import config
from project_evo import llm

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import json
import os
import subprocess
import sys
import tempfile
import unittest

class AdapterTests(unittest.TestCase):
    def test_subprocess_arguments_input_and_environment(self):
        # Execute a local stand-in, so no provider binary or account is used.
        with tempfile.TemporaryDirectory(prefix="evo-adapters-") as directory:
            root = Path(directory)
            script = root / "provider.py"
            script.write_text("import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'prompt': sys.stdin.read(), 'cwd': os.getcwd(), 'sandbox': os.environ.get('OPENCODE_SANDBOX_CONFIG'), 'session': os.getsid(0) if os.name == 'posix' else None}))\n")
            real_run = subprocess.run

            def run(command: list[str], **kwargs):
                return real_run([sys.executable, str(script), *command], **kwargs)

            intercepted = Mock(side_effect=run)
            prompt = "Improve this\nKeep Unicode: λ\n$(must remain literal)"
            with patch.object(config, "cfg", SimpleNamespace(llm_timeout_sec=5)), patch.object(llm.subprocess, "run", intercepted):
                for adapter in (llm._codex, llm._opencode):
                    with self.subTest(adapter=adapter.__name__):
                        result = adapter(root, "model/name", prompt, ["--profile", "with spaces"])
                        result.check_returncode()
                        payload = json.loads(result.stdout)
                        self.assertEqual(payload["prompt"], prompt)
                        self.assertEqual(payload["cwd"], str(root))
                        if os.name == "posix":
                            self.assertNotEqual(payload["session"], os.getsid(0))
                        if adapter is llm._codex:
                            self.assertEqual(payload["argv"], ["codex", "exec", "--ephemeral", "--cd", str(root), "--sandbox", "workspace-write", "-m", "model/name", "--profile", "with spaces", "-"])
                        else:
                            self.assertEqual(payload["argv"], ["opencode", "run", "--standalone", "--model", "model/name", "--profile", "with spaces"])
                            self.assertIsInstance(json.loads(payload["sandbox"]), dict)
                        self.assertEqual(intercepted.call_args.kwargs["timeout"], 5)

    def test_subprocess_timeout(self):
        real_run = subprocess.run

        def run(command: list[str], **kwargs):
            return real_run([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

        with tempfile.TemporaryDirectory(prefix="evo-timeout-") as directory:
            with patch.object(config, "cfg", SimpleNamespace(llm_timeout_sec=0.05)), patch.object(llm.subprocess, "run", side_effect=run):
                for adapter in (llm._codex, llm._opencode):
                    with self.subTest(adapter=adapter.__name__), self.assertRaises(subprocess.TimeoutExpired):
                        adapter(Path(directory), "model", "prompt", [])

    def test_attempt_name(self):
        with tempfile.TemporaryDirectory(prefix="evo-name-") as directory:
            root = Path(directory)
            self.assertEqual(llm.get_attempt_name(root), "FAILED TO FETCH NAME")
            for text, expected in (("", "FAILED TO FETCH NAME"), ("  First name  \nSecond line", "First name"), ("λ", "λ")):
                (root / "name.txt").write_text(text)
                self.assertEqual(llm.get_attempt_name(root), expected)

if __name__ == "__main__":
    unittest.main()
