from project_evo.error import KillWorkerException
from project_evo import git
from project_evo import smoke

from pathlib import Path
from unittest.mock import Mock, patch

import subprocess
import tempfile
import unittest

class AutocommitTests(unittest.TestCase):
    def setUp(self):
        isolated = smoke.isolated_git()
        isolated.__enter__()
        self.addCleanup(isolated.__exit__, None, None, None)
        temp = tempfile.TemporaryDirectory(prefix="evo-autocommit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.addCleanup(git.workspace_locks.pop, self.root, None)
        self.run_git("init", "--template=", "--initial-branch=main")
        self.run_git("config", "user.name", "Test")
        self.run_git("config", "user.email", "test@example.invalid")

    def run_git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

    def test_initial_commit_and_clean_workspace(self):
        (self.root / "code.txt").write_text("initial\n")
        git.autocommit(self.root)
        head = self.run_git("rev-parse", "HEAD")
        self.assertEqual(self.run_git("show", "HEAD:code.txt"), "initial\n")
        git.autocommit(self.root)
        self.assertEqual(self.run_git("rev-parse", "HEAD"), head)
        self.assertEqual(self.run_git("status", "--porcelain"), "")

    def test_commits_modified_added_and_deleted_files(self):
        (self.root / "code.txt").write_text("initial\n")
        (self.root / "removed.txt").write_text("remove me\n")
        git.autocommit(self.root)
        (self.root / "code.txt").write_text("changed\n")
        (self.root / "added.txt").write_text("new\n")
        (self.root / "removed.txt").unlink()
        git.autocommit(self.root)
        self.assertEqual(self.run_git("show", "HEAD:code.txt"), "changed\n")
        self.assertEqual(self.run_git("show", "HEAD:added.txt"), "new\n")
        self.assertEqual(self.run_git("ls-tree", "--name-only", "HEAD"), "added.txt\ncode.txt\n")
        self.assertEqual(self.run_git("status", "--porcelain"), "")

    def test_failing_hook_raises(self):
        (self.root / "code.txt").write_text("initial\n")
        git.autocommit(self.root)
        head = self.run_git("rev-parse", "HEAD")
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        (self.root / "code.txt").write_text("changed\n")
        with self.assertRaises(KillWorkerException):
            git.autocommit(self.root)
        self.assertEqual(self.run_git("rev-parse", "HEAD"), head)
        self.assertEqual(self.run_git("show", ":code.txt"), "changed\n")

    def test_staging_and_comparison_errors_raise(self):
        for results in (
            [subprocess.CalledProcessError(1, ["git", "add", "."])],
            [subprocess.CompletedProcess(["git", "add", "."], 0), subprocess.CompletedProcess(["git", "diff"], 128)]
        ):
            with self.subTest(results=results):
                run_mock = Mock(side_effect=results)
                with patch.object(git.subprocess, "run", run_mock):
                    with self.assertRaises(KillWorkerException):
                        git.autocommit(self.root)
                self.assertEqual(run_mock.call_count, len(results))

if __name__ == "__main__":
    unittest.main()
