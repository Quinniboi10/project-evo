from project_evo.database import Database, PrimaryTableRow
from project_evo.error import DatabaseException
from project_evo.task import Task
from project_evo import visualize

from argparse import Namespace
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import tempfile
import unittest

class VisualizeTests(unittest.TestCase):
    def test_empty_database(self):
        with tempfile.TemporaryDirectory(prefix="evo-visualize-") as directory:
            path = Path(directory) / "run.db"
            with closing(Database(path, island_count=1)):
                pass
            graph = Mock()
            with patch.object(visualize.graphviz, "Digraph", return_value=graph):
                visualize.run(Namespace(db=path, format="svg"))
            graph.node.assert_not_called()
            graph.render.assert_called_once()

    def test_branches_and_missing_parent(self):
        with tempfile.TemporaryDirectory(prefix="evo-visualize-") as directory:
            path = Path(directory) / "run.db"
            with closing(Database(path, island_count=1)) as db:
                parent = PrimaryTableRow("Baseline", "base", None, None, None, 1)
                db.insert_baseline(parent)
                for score in (3, 2, 0.5):
                    row = PrimaryTableRow("Child", str(score), None, Task.IMPROVE, parent.id, score)
                    db.insert_attempt(row, 0)
            graph = Mock()
            with patch.object(visualize.graphviz, "Digraph", return_value=graph):
                visualize.run(Namespace(db=path, format="svg"))
            colors = [call.kwargs.get("color") for call in graph.node.call_args_list]
            self.assertEqual(colors, ["cornflowerblue", "cyan3", "green4", None])
            self.assertEqual(graph.edge.call_count, 3)
            with closing(Database(path)) as db:
                db.db.execute("UPDATE evolve SET parent_id = 999 WHERE id = 2")
                db.db.commit()
            with patch.object(visualize.graphviz, "Digraph", return_value=graph), self.assertRaises(DatabaseException):
                visualize.run(Namespace(db=path, format="svg"))

    def test_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="evo-visualize-") as directory:
            path = Path(directory) / "run.db"
            with closing(Database(path, island_count=1)) as db:
                parent = PrimaryTableRow("Baseline", "base", None, None, None, 1)
                db.insert_baseline(parent)
                row = PrimaryTableRow("Child", "child", None, Task.IMPROVE, parent.id, 2)
                db.insert_attempt(row, 0)
                db.db.execute("UPDATE evolve SET parent_id = id WHERE id = ?", (row.id,))
                db.db.commit()
            with patch.object(visualize.graphviz, "Digraph"), self.assertRaisesRegex(DatabaseException, "cycle"):
                visualize.run(Namespace(db=path, format="svg"))

if __name__ == "__main__":
    unittest.main()
