from project_evo.database import Database
from project_evo import config
from project_evo import git
from project_evo import main
from project_evo import smoke

from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import os
import subprocess
import sys
import tempfile
import unittest

class RunIntegrationTests(unittest.TestCase):
    def test_normal_run_repairs_and_resume_with_local_provider(self):
        with tempfile.TemporaryDirectory(prefix="evo-normal-run-") as directory, smoke.isolated_git():
            root = Path(directory)
            project = root / "project with spaces"
            project.mkdir()
            binary = root / "bin"
            binary.mkdir()
            provider = binary / "codex"
            provider.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys
sys.stdin.read()
workspace = Path.cwd()
marker = workspace / "repair-needed"
if marker.exists():
    marker.unlink()
else:
    marker.write_text("repair me")
    score = workspace / "score.txt"
    score.write_text(str(float(score.read_text()) + 1))
(workspace / "name.txt").write_text("Local provider attempt")
''')
            provider.chmod(0o755)
            evaluator = root / "evaluate.py"
            evaluator.write_text('def evaluate(workspace):\n    return not (workspace / "repair-needed").exists(), float((workspace / "score.txt").read_text())\n')
            settings = root / "config.toml"
            template = (Path(config.__file__).parent / "configs" / "default.toml").read_text()
            settings.write_text(template.replace('exploration = "ling-flash"', 'exploration = "sol"').replace('improvement = "muse"', 'improvement = "sol"').replace('workspace_base = "workspaces"', 'workspace_base = "nested/workspaces"'))
            (project / "score.txt").write_text("1")
            (project / ".gitignore").write_text("nested/\n")
            subprocess.run(["git", "init", "--template=", "--initial-branch=main", str(project)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.invalid"], check=True)
            git.autocommit(project)
            database = root / "run.db"
            args = ["run", str(project), str(evaluator), "--objective", "Increase score", "--config", str(settings), "--db", str(database), "--logfile", str(root / "run.log")]
            # PATH contains our executable ahead of any authenticated provider.
            with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}), patch.object(config, "cfg"), patch.object(config.logging, "basicConfig"), redirect_stdout(StringIO()):
                self.assertEqual(main.main([*args, "-i", "6"]), 0)
                self.assertEqual(main.main([*args, "-i", "3"]), 0)
            with closing(Database(database, require_exist=True)) as db:
                rows = db.select("SELECT name, uuid, score FROM evolve ORDER BY id")
                self.assertEqual(len(rows), 10)
                self.assertEqual(rows[0][2], 1)
                for name, uuid, score in rows[1:]:
                    self.assertEqual(name, "Local provider attempt")
                    self.assertGreater(score, 1)
                    tree = subprocess.run(["git", "-C", str(project), "ls-tree", "--name-only", f"project-evo/{uuid}"], check=True, text=True, capture_output=True).stdout
                    self.assertNotIn("repair-needed", tree)
            self.assertEqual(list((project / "nested" / "workspaces").iterdir()), [])
            worktrees = subprocess.run(["git", "-C", str(project), "worktree", "list", "--porcelain"], check=True, text=True, capture_output=True).stdout
            self.assertEqual(worktrees.count("worktree "), 1)
            self.assertEqual((project / "score.txt").read_text(), "1")
            for workspace in list(git.workspace_locks):
                if workspace.is_relative_to(root):
                    git.workspace_locks.pop(workspace)

if __name__ == "__main__":
    unittest.main()
