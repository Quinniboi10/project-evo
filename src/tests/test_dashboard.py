from project_evo.database import Database, PrimaryTableRow
from project_evo.error import DatabaseException
from project_evo.task import Task
from project_evo import dashboard

from pathlib import Path
from unittest.mock import patch

import json
import tempfile
import unittest

class DashboardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="evo-dashboard-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "islands.db"
        self.db = Database(self.path, island_count=4)
        self.addCleanup(self.db.close)
        self.baseline = PrimaryTableRow("Baseline", "base", None, None, None, 10)
        self.db.insert_baseline(self.baseline)
        context = patch.object(dashboard, "DB_FILE", self.path)
        context.start()
        self.addCleanup(context.stop)
        self.client = dashboard.app.test_client()

    def attempt(self, island: int, score: float, references: list[int]|None=None):
        row = PrimaryTableRow.create_new_child(self.baseline, Task.IMPROVE)
        row.score = score
        self.db.insert_attempt(row, island, references)
        return row

    def test_graph_and_row_metadata(self):
        historical = self.attempt(0, 20)
        recorded = self.attempt(1, 20, [])
        assert historical.id is not None and recorded.id is not None
        inspired = self.attempt(2, 30, [recorded.id, historical.id])
        graph = self.client.get("/api/graph").get_json()
        self.assertEqual(graph["islands"], [0, 1, 2, 3])
        self.assertEqual(graph["nodes"][0]["island_ids"], [0, 1, 2, 3])
        self.assertIsNone(graph["nodes"][1]["inspiration_ids"])
        self.assertEqual(graph["nodes"][2]["inspiration_ids"], [])
        self.assertEqual(graph["nodes"][3]["inspiration_ids"], [recorded.id, historical.id])
        self.assertEqual([node["level"] for node in graph["nodes"]], [0, 1, 1, 1])
        self.assertEqual([edge["from"] for edge in graph["edges"]], [self.baseline.id] * 3)
        row = self.client.get(f"/api/row/{inspired.id}").get_json()
        self.assertEqual(row["island_ids"], [2])
        self.assertEqual(row["inspiration_ids"], [recorded.id, historical.id])
        self.assertEqual(row["parent_id"], self.baseline.id)
        self.assertEqual(self.client.get("/api/row/999").status_code, 404)
        self.assertEqual(self.client.get("/api/row/not-an-id").status_code, 404)

    def test_read_only_and_incompatible_schema(self):
        before = self.path.read_bytes()
        self.assertEqual(self.client.get("/api/graph").status_code, 200)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(DatabaseException), dashboard.read_snapshot() as db:
            db.execute("DELETE FROM evolve")
        self.db.cursor.execute("ALTER TABLE evolve RENAME COLUMN reference_ids TO old_references")
        self.db.db.commit()
        before = self.path.read_bytes()
        response = self.client.get("/api/graph")
        self.assertEqual(response.status_code, 500)
        self.assertIn("reference_ids", response.get_json()["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_provenance_atomicity_and_validation(self):
        reference = self.attempt(0, 20)
        assert reference.id is not None
        for references in ([999], [reference.id, reference.id], [1]):
            with self.assertRaises(DatabaseException):
                self.attempt(1, 30, references)
        self.assertEqual(self.db.select("SELECT COUNT(*) FROM evolve"), [(2,)])
        self.db.cursor.execute("CREATE TRIGGER reject_provenance BEFORE INSERT ON evolve BEGIN SELECT RAISE(ABORT, 'rollback'); END")
        with self.assertRaises(DatabaseException):
            self.attempt(1, 30, [reference.id])
        self.assertEqual(self.db.select("SELECT COUNT(*) FROM evolve"), [(2,)])
        self.db.cursor.execute("DROP TRIGGER reject_provenance")
        child = self.attempt(1, 30, [reference.id])
        stored = self.db.cursor.execute("SELECT reference_ids FROM evolve WHERE id = ?", (child.id,)).fetchone()
        self.assertEqual(json.loads(stored[0]), [reference.id])

    def test_deep_tree_and_snapshot_local_depths(self):
        parent = self.baseline
        for _ in range(1050):
            row = PrimaryTableRow.create_new_child(parent, Task.IMPROVE)
            row.score = 10
            self.db.insert_attempt(row, 0, [])
            parent = row
        graph = self.client.get("/api/graph").get_json()
        self.assertEqual(graph["nodes"][-1]["level"], 1050)
        self.db.cursor.execute("UPDATE evolve SET parent_id = 1 WHERE id = ?", (parent.id,))
        self.db.db.commit()
        self.assertEqual(self.client.get("/api/graph").get_json()["nodes"][-1]["level"], 1)

if __name__ == "__main__":
    unittest.main()
