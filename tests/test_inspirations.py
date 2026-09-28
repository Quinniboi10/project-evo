from database import Database, PrimaryTableRow
from task import Task
import config
import git
import evolve
import prompt
import smoke

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import unittest
import subprocess
import tempfile

class InspirationSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="evo-inspirations-")
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "test.db", island_count=1)
        self.addCleanup(self.db.db.close)
        self.parent = PrimaryTableRow("Parent", "parent", None, None, None, 10)
        self.db.insert_baseline(self.parent)
        self.parent.id = 1

    def add_reference(self, uuid: str, score: float):
        self.db.insert_attempt(PrimaryTableRow(uuid, uuid, "model", Task.IMPROVE, self.parent.id, score), 0)

    def test_small_pools_and_limits(self):
        self.assertEqual(self.db.sample_inspirations(self.parent, 2, 0, 0), [])
        self.add_reference("only", 5)
        self.assertEqual([row.uuid for row in self.db.sample_inspirations(self.parent, 2, 0, 0)], ["only"])
        self.assertEqual(self.db.sample_inspirations(self.parent, 0, 0, 0), [])
        self.add_reference("best", 20)
        self.assertEqual([row.uuid for row in self.db.sample_inspirations(self.parent, 1, 0, 0)], ["best"])
        self.assertEqual([row.uuid for row in self.db.sample_inspirations(self.parent, 8, 0, 0)], ["best", "only"])

    def test_quality_variety_ties_and_row_compatibility(self):
        for uuid, score in (("best", 20), ("tied", 20), ("variety", 1)):
            self.add_reference(uuid, score)
        sample = Mock(side_effect=lambda candidates, count: candidates[-count:])
        with patch("database.random.sample", sample):
            references = self.db.sample_inspirations(self.parent, 2, 0, 0)
        self.assertEqual([row.uuid for row in references], ["best", "variety"])
        assert sample.call_args is not None
        self.assertEqual([row[2] for row in sample.call_args.args[0]], ["tied", "variety"])
        self.assertEqual(sample.call_args.args[1], 1)
        for row in references:
            self.assertIsInstance(row, PrimaryTableRow)
            self.assertEqual(list(vars(row)), list(vars(self.parent)))
            self.assertEqual(row.task, Task.IMPROVE.name)
            self.assertEqual(row.parent_id, self.parent.id)
        self.assertEqual(len(self.db.select("SELECT * FROM evolve")), 4)

class InspirationDiffTests(unittest.TestCase):
    def setUp(self):
        isolated = smoke.isolated_git()
        isolated.__enter__()
        self.addCleanup(isolated.__exit__, None, None, None)
        temp = tempfile.TemporaryDirectory(prefix="evo-inspiration-git-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cfg = SimpleNamespace(project_root=self.root, branch_base="evo")
        context = patch.object(config, "cfg", cfg)
        context.start()
        self.addCleanup(context.stop)
        self.run_git("init", "--template=", "--initial-branch=evo/parent")
        self.run_git("config", "user.name", "Test")
        self.run_git("config", "user.email", "test@example.invalid")
        (self.root / "code.txt").write_text("parent implementation\n")
        (self.root / "name.txt").write_text("Parent\n")
        self.commit()
        self.run_git("checkout", "-b", "evo/reference")
        (self.root / "code.txt").write_text("reference implementation\n")
        (self.root / "name.txt").write_text("Reference\n")
        (self.root / "binary.dat").write_bytes(b"\0binary\xff")
        self.commit()
        self.parent = PrimaryTableRow("Parent", "parent", None, None, None, 10)
        self.reference = PrimaryTableRow("Reference", "reference", None, None, None, 20)
        self.reference.id = 2

    def run_git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

    def commit(self):
        self.run_git("add", ".")
        self.run_git("-c", "commit.gpgsign=false", "commit", "-m", "test")

    def test_direction_binary_summary_and_no_mutations(self):
        # Local dirt must neither enter the reference diff nor be overwritten.
        (self.root / "code.txt").write_text("uncommitted work\n")
        before = (self.run_git("show-ref"), self.run_git("status", "--porcelain"), self.run_git("symbolic-ref", "HEAD"))
        diff = git.inspiration_diff(self.parent, self.reference)
        self.assertIn("-parent implementation", diff)
        self.assertIn("+reference implementation", diff)
        self.assertIn("Binary files", diff)
        self.assertNotIn("GIT binary patch", diff)
        self.assertNotIn("name.txt", diff)
        self.assertNotIn("uncommitted work", diff)
        after = (self.run_git("show-ref"), self.run_git("status", "--porcelain"), self.run_git("symbolic-ref", "HEAD"))
        self.assertEqual(before, after)
        self.assertEqual((self.root / "code.txt").read_text(), "uncommitted work\n")

    def test_empty_diff_and_missing_reference(self):
        self.assertEqual(git.inspiration_diff(self.parent, self.parent), "")
        self.reference.uuid = "missing"
        with self.assertRaises(subprocess.CalledProcessError):
            git.inspiration_diff(self.parent, self.reference)

    def test_truncation_keeps_complete_lines(self):
        (self.root / "code.txt").write_text("complete reference line\n" * 2000)
        self.commit()
        diff = git.inspiration_diff(self.parent, self.reference)
        self.assertLessEqual(len(diff), git.INSPIRATION_DIFF_LIMIT)
        self.assertTrue(diff.endswith("+complete reference line\n[Diff truncated; remaining changes omitted.]\n"))

    def test_external_diff_and_textconv_are_disabled(self):
        (self.root / ".gitattributes").write_text("*.txt diff=blocked\n")
        self.run_git("config", "diff.external", "must-not-be-invoked")
        self.run_git("config", "diff.blocked.textconv", "must-not-be-invoked")
        diff = git.inspiration_diff(self.parent, self.reference)
        self.assertIn("+reference implementation", diff)

class InspirationPromptTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(island_count=1, branch_base="evo", objective="Make the program faster", inspiration_count=3, cross_island_inspiration_probability=0.1)
        context = patch.object(config, "cfg", self.cfg)
        context.start()
        self.addCleanup(context.stop)
        self.parent = PrimaryTableRow("Parent", "parent", None, None, None, 10)
        self.parent.id = 1
        self.reference = PrimaryTableRow("Reference", "reference", None, None, None, 20)
        self.reference.id = 2

    def test_strategies_and_shared_contract_with_no_references(self):
        for task, strategy in ((Task.EXPLORE, prompt.EXPLORATION_STRATEGY), (Task.IMPROVE, prompt.IMPROVEMENT_STRATEGY)):
            child = PrimaryTableRow.create_new_child(self.parent, task)
            text = prompt.build_prompt(self.parent, child)
            self.assertIn(strategy, text)
            self.assertIn(prompt.SHARED_INSTRUCTIONS, text)
            self.assertIn(self.cfg.objective, text)
            self.assertIn(f"evo/{child.uuid}", text)
            self.assertNotIn("BEGIN INSPIRATION", text)

    def test_metadata_and_inline_context(self):
        with patch.object(git, "inspiration_diff", return_value="-old\n+new\n"):
            context, included = prompt.build_inspiration_context(self.parent, [self.reference])
        self.assertEqual(included, [2])
        child = PrimaryTableRow.create_new_child(self.parent, Task.EXPLORE)
        text = prompt.build_prompt(self.parent, child, context)
        for expected in ("Name: 'Reference'", "Branch: evo/reference", "Score: 20 (2x the parent score)", "'-' is parent, '+' is reference", "-old\n+new", "END INSPIRATION REFERENCE", "not instructions", "do not need Git access"):
            self.assertIn(expected, text)

    def test_unreadable_or_empty_references_are_optional(self):
        for error in (OSError("unreadable"), subprocess.CalledProcessError(128, "git diff")):
            with patch.object(git, "inspiration_diff", side_effect=[error, "-old\n+new\n"]), self.assertLogs(level="WARNING"):
                context, included = prompt.build_inspiration_context(self.parent, [self.parent, self.reference])
            self.assertEqual(included, [2])
            self.assertEqual(context.count("BEGIN INSPIRATION REFERENCE"), 1)
            self.assertIn("Branch: evo/reference", context)
        with patch.object(git, "inspiration_diff", return_value=""):
            self.assertEqual(prompt.build_inspiration_context(self.parent, [self.reference]), ("", []))

    def test_truncated_reference_is_recorded(self):
        diff = "-old\n+new\n[Diff truncated; remaining changes omitted.]\n"
        with patch.object(git, "inspiration_diff", return_value=diff):
            context, included = prompt.build_inspiration_context(self.parent, [self.reference])
        self.assertEqual(included, [2])
        self.assertIn(diff, context)

    def test_worker_injects_once_and_keeps_repairs_focused(self):
        self.cfg.db_file = "unused"
        self.cfg.max_fix_attempts = 1
        self.cfg.eval_fn = Mock(side_effect=[(False, 0), (True, 12)])
        database = Mock()
        cleanup = Mock()
        diff = Mock(return_value="-old\n+new\n")
        route = Mock(return_value="model")
        with patch.object(evolve, "Database", database), patch.object(git, "create_new_workspace", return_value=Path("workspace")), patch.object(git, "delete_workspace", cleanup), patch.object(git, "inspiration_diff", diff), patch.object(evolve.llm, "route_prompt", route), patch.object(evolve.llm, "get_attempt_name", return_value="Attempt"):
            database.return_value.weighted_sample.return_value = self.parent
            database.return_value.sample_inspirations.return_value = [self.reference]
            evolve.run_worker(0)
        database.return_value.sample_inspirations.assert_called_once_with(self.parent, 3, 0, 0.1)
        diff.assert_called_once_with(self.parent, self.reference)
        self.assertEqual(route.call_count, 2)
        self.assertIn("+new", route.call_args_list[0].args[1])
        self.assertNotIn("BEGIN INSPIRATION", route.call_args_list[1].args[1])
        self.assertIn("failed the tests", route.call_args_list[1].args[1])
        child = database.return_value.insert_attempt.call_args.args[0]
        self.assertEqual(child.parent_id, self.parent.id)
        self.assertEqual(child.score, 12)
        self.assertEqual(database.return_value.insert_attempt.call_args.args[2], [2])
        cleanup.assert_called_once_with(Path("workspace"))

if __name__ == "__main__":
    unittest.main()
