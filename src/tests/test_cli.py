from project_evo.database import Database, PrimaryTableRow
from project_evo.version import version_string
from project_evo import dashboard
from project_evo import main
from project_evo import evolve

from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import chdir, closing, redirect_stderr, redirect_stdout
from io import StringIO

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]

class CliTests(unittest.TestCase):
    def test_help_and_version(self):
        for args in (["--help"], ["run", "--help"], ["dashboard", "--help"], ["visualize", "--help"], ["init", "--help"], ["--version"]):
            with self.subTest(args=args), redirect_stdout(StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    main.main(args)
                self.assertEqual(raised.exception.code, 0)
                self.assertIn(version_string if args == ["--version"] else "usage:", output.getvalue())

    def test_invalid_arguments(self):
        for args in ([], ["unknown"], ["run"], ["run", "-i", "1"], ["dashboard"], ["visualize"], ["dashboard", "--db", "run.db", "--unknown"]):
            with self.subTest(args=args), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main.main(args)
                self.assertEqual(raised.exception.code, 2)

    def test_init_configuration(self):
        for destination in (None, "custom.toml"):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as directory, chdir(directory), redirect_stdout(StringIO()) as output:
                args = ["init"] if destination is None else ["init", destination]
                self.assertEqual(main.main(args), 0)
                path = Path(destination or "config.toml")
                contents = path.read_text(encoding="utf-8")
                template = Path(main.__file__).parent / "configs" / "default.toml"
                self.assertEqual(contents, template.read_text(encoding="utf-8"))
                self.assertIn("routing", tomllib.loads(contents))
                self.assertIn(str(path.resolve()), output.getvalue())

    def test_init_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory), redirect_stderr(StringIO()):
            path = Path("config.toml")
            path.write_text("existing settings")
            with self.assertRaises(SystemExit) as raised:
                main.main(["init"])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(path.read_text(), "existing settings")

    def test_init_write_failures(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory), redirect_stderr(StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                main.main(["init", "missing/config.toml"])
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(Path("missing").exists())
            self.assertIn("Cannot create", output.getvalue())
            write_mock = Mock(side_effect=PermissionError(13, "Permission denied"))
            with patch.object(Path, "open", write_mock), patch.object(Path, "read_text", return_value="template"):
                with self.assertRaises(SystemExit) as raised:
                    main.main(["init"])
            self.assertEqual(raised.exception.code, 2)
            write_mock.assert_called_once_with("x", encoding="utf-8")

    def test_evolution_dispatch_and_exit_code(self):
        run_mock = Mock(return_value=7)
        with patch.object(evolve, "run", run_mock):
            self.assertEqual(main.main(["run", "project", "evaluate.py", "--objective", "Faster", "-i", "5"]), 7)
        run_mock.assert_called_once()
        args = run_mock.call_args.args[0]
        self.assertEqual((args.project_path, args.eval_file, args.objective, args.iterations), ("project", "evaluate.py", "Faster", 5))

    def test_dashboard_dispatch_and_assets(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            path = Path(directory) / "run.db"
            with closing(Database(path, island_count=1)):
                pass
            serve_mock = Mock()
            with patch.object(dashboard, "DB_FILE", path), patch.object(dashboard.app, "run", serve_mock):
                self.assertEqual(main.main(["dashboard", "--db", "run.db"]), 0)
                self.assertEqual(dashboard.DB_FILE, path.resolve())
                serve_mock.assert_called_once_with()
                with dashboard.app.test_client() as client:
                    for url in ("/", "/dashboard/style.css", "/dashboard/fonts/plex-sans-regular.ttf"):
                        with client.get(url) as response:
                            self.assertEqual(response.status_code, 200, url)

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is required")
    def test_visualize_export(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            with closing(Database("run.db", island_count=1)) as db:
                db.insert_baseline(PrimaryTableRow("Baseline", "base", None, None, None, 1))
            self.assertEqual(main.main(["visualize", "--db", "run.db", "--format", "svg"]), 0)
            self.assertIn("Baseline", Path("visualization.svg").read_text())

    def test_unified_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-m", "project_evo.main", "run", "--smoke", "-i", "12", "--debug", "-c", str(ROOT / "config.toml")], cwd=directory, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True, capture_output=True, timeout=60)
            for line in result.stdout.splitlines():
                if line.startswith("Smoke artifacts: "):
                    self.addCleanup(shutil.rmtree, Path(line.removeprefix("Smoke artifacts: ")))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("SMOKE PASSED: 12 attempts", result.stdout)

if __name__ == "__main__":
    unittest.main()
