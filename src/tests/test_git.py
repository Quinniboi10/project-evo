from project_evo.error import KillWorkerException, KillPoolException
from project_evo.database import PrimaryTableRow
from project_evo.task import Task
from project_evo import config
from project_evo import git
from project_evo import smoke

from pathlib import Path
from types import SimpleNamespace
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

    def test_worktree_paths_are_literal_and_nested_directories_are_created(self):
        (self.root / "code.txt").write_text("initial\n")
        git.autocommit(self.root)
        parent = PrimaryTableRow("Baseline", "baseline", None, None, None, 1)
        for base in ("nested/workspaces", "spaces and 'quotes'", 'literal-$(touch SHOULD_NOT_EXIST)-"quotes"', "-workspaces"):
            with self.subTest(base=base):
                cfg = SimpleNamespace(project_root=self.root, workspace_base=base, branch_base="evo")
                with patch.object(config, "cfg", cfg):
                    if not self.run_git("branch", "--list", "evo/baseline"):
                        git.bootstrap("evo/baseline")
                    child = PrimaryTableRow.create_new_child(parent, Task.IMPROVE)
                    workspace = git.create_new_workspace(parent, child)
                    self.assertEqual(workspace, self.root / base / child.uuid)
                    self.assertEqual((workspace / "code.txt").read_text(), "initial\n")
                    git.ensure_branch_exists(child.uuid)
                    git.delete_workspace(workspace)
                    self.assertFalse(workspace.exists())
                    self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_resume_requires_a_branch_not_a_tag(self):
        (self.root / "code.txt").write_text("initial\n")
        git.autocommit(self.root)
        self.run_git("tag", "evo/tag-only")
        with patch.object(config, "cfg", SimpleNamespace(project_root=self.root, branch_base="evo")):
            for uuid in ("missing", "tag-only"):
                with self.subTest(uuid=uuid), self.assertRaises(KillPoolException):
                    git.ensure_branch_exists(uuid)

if __name__ == "__main__":
    unittest.main()
