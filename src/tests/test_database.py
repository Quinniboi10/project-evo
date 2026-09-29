from src.database import Database, PrimaryTableRow, SCHEMA_VERSION
from src.error import DatabaseException
from src.task import Task
from src.version import version_string
from src import config
from src import dashboard

from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sqlite3
import tempfile
import unittest

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evo-database-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run.db"

    def create(self) -> Database:
        db = Database(self.path, island_count=3)
        self.addCleanup(db.close)
        db.insert_baseline(PrimaryTableRow("Baseline", "baseline", None, None, None, 10))
        return db

    def test_complete_schema_and_read_only_reopen(self):
        db = self.create()
        tables = {row[0] for row in db.select("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(tables, {"evolve", "metadata"})
        self.assertEqual(db.select("SELECT schema_version, created_with_version, island_count FROM metadata"), [(SCHEMA_VERSION, version_string, 3)])
        self.assertEqual(db.select("SELECT island_id, reference_ids FROM evolve"), [(None, None)])
        before = self.path.read_bytes()
        with closing(Database(self.path, island_count=3)):
            pass
        self.assertEqual(self.path.read_bytes(), before)
        with closing(Database(":memory:", island_count=2)) as memory:
            self.assertEqual(memory.select("SELECT island_count FROM metadata"), [(2,)])

    def test_creation_requires_count_and_existing_empty_file_is_not_repaired(self):
        with self.assertRaisesRegex(DatabaseException, "requires island_count"):
            Database(self.path)
        self.assertFalse(self.path.exists())
        self.path.touch()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(DatabaseException, "no such table"):
            Database(self.path, island_count=3)
        self.assertEqual(self.path.read_bytes(), before)

    def test_initialization_rollback_and_connection_cleanup(self):
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE metadata (id INTEGER)")
        connection.commit()
        # The duplicate metadata table fails after attempt DDL has executed.
        with patch("src.database.sqlite3.connect", return_value=connection), self.assertRaises(DatabaseException):
            Database(":memory:", island_count=3)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with closing(sqlite3.connect(self.path)) as reopened:
            self.assertEqual(reopened.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [("metadata",)])

    def test_version_mismatch_warns_once_and_allows_writes(self):
        db = self.create()
        for version in (0, SCHEMA_VERSION + 1, SCHEMA_VERSION + 4):
            with self.subTest(version=version):
                db.db.execute("UPDATE metadata SET schema_version = ?", (version,))
                db.db.commit()
                before = self.path.read_bytes()
                log = Mock()
                with patch("src.database.logging.log", log):
                    with closing(Database(self.path, island_count=3)):
                        pass
                    self.assertEqual(self.path.read_bytes(), before)
                    with closing(Database(self.path, island_count=3)) as reopened:
                        child = PrimaryTableRow("Child", f"child-{version}", None, Task.IMPROVE, 1, 11)
                        reopened.insert_attempt(child, 2, [])
                        self.assertEqual(child.island_id, 2)
                warnings = [call for call in log.call_args_list if call.args[0] == 30]
                self.assertEqual(len(warnings), 1)
                self.assertIn(f"schema version {version}", warnings[0].args[1])
                self.assertEqual(db.select("SELECT schema_version FROM metadata"), [(version,)])

    def test_unversioned_compatible_database_can_resume(self):
        db = self.create()
        db.db.execute("ALTER TABLE metadata DROP COLUMN schema_version")
        db.db.commit()
        with self.assertLogs(level="WARNING") as logs, closing(Database(self.path, island_count=3)) as reopened:
            reopened.insert_attempt(PrimaryTableRow("Child", "child", None, Task.IMPROVE, 1, 11), 0)
        self.assertIn("schema version 0", logs.output[0])
        self.assertEqual(db.select("SELECT COUNT(*) FROM evolve"), [(2,)])

    def test_old_layout_supports_basic_reads_without_repair(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE evolve (id INTEGER, parent_id INTEGER, name TEXT, score REAL, task TEXT)")
            connection.execute("INSERT INTO evolve VALUES (1, NULL, 'Baseline', 10, NULL)")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertLogs(level="WARNING"), closing(Database(self.path, require_exist=True)) as db:
            self.assertEqual(db.select("SELECT id, parent_id FROM evolve"), [(1, None)])
        with self.assertRaisesRegex(DatabaseException, "cannot support"), closing(Database(self.path, island_count=3)):
            pass
        self.assertEqual(self.path.read_bytes(), before)

    def test_v1_database_upgrade_failures_preserve_original(self):
        # Match the schema shipped in v1.0.0, including its original column order.
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE evolve (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, uuid TEXT NOT NULL, model TEXT, task TEXT, parent_id INTEGER, score REAL NOT NULL)")
            connection.execute("INSERT INTO evolve VALUES (1, 'Baseline', 'baseline', NULL, NULL, NULL, 10)")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertLogs(level="WARNING"), self.assertRaisesRegex(DatabaseException, "island_id"):
            Database(self.path, island_count=1)
        with patch.object(dashboard, "DB_FILE", self.path), dashboard.app.test_client() as client:
            for url in ("/api/graph", "/api/row/1"):
                with self.subTest(url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 500)
                    self.assertIn("Database cannot support this operation", response.get_json()["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_extra_columns_and_reference_roundtrip(self):
        db = self.create()
        db.db.execute("ALTER TABLE evolve ADD COLUMN extra TEXT DEFAULT 'kept'")
        db.db.commit()
        first = PrimaryTableRow("First", "first", None, Task.IMPROVE, 1, 11)
        second = PrimaryTableRow("Second", "second", None, Task.IMPROVE, 1, 12)
        db.insert_attempt(first, 1)
        db.insert_attempt(second, 1, [])
        assert first.id is not None and second.id is not None
        child = PrimaryTableRow("Child", "child", None, Task.IMPROVE, 1, 13)
        references = [second.id, first.id]
        db.insert_attempt(child, 0, references)
        references.clear()
        self.assertEqual(child.reference_ids, [second.id, first.id])
        with closing(Database(self.path, island_count=3)) as reopened, patch.object(config, "cfg", SimpleNamespace(softmax_temp=1)), patch("src.database.random.choices", return_value=[1]):
            loaded = reopened.weighted_sample(0)
        self.assertEqual((loaded.island_id, loaded.reference_ids), (0, [second.id, first.id]))
        self.assertEqual(db.select("SELECT extra FROM evolve"), [("kept",)] * 4)

    def test_invalid_insert_leaves_row_and_database_unchanged(self):
        db = self.create()
        child = PrimaryTableRow("Child", "child", None, Task.IMPROVE, 1, 11)
        invalid: list[tuple[int, list[int]]] = [(-1, []), (3, []), (True, []), (0, [1]), (0, [999]), (0, [True])]
        for island, references in invalid:
            with self.subTest(island=island, references=references), self.assertRaises(DatabaseException):
                db.insert_attempt(child, island, references)
            self.assertEqual((child.id, child.island_id, child.reference_ids), (None, None, None))
            self.assertEqual(db.select("SELECT COUNT(*) FROM evolve"), [(1,)])
        db.db.execute("CREATE TRIGGER reject_attempt AFTER INSERT ON evolve BEGIN SELECT RAISE(ABORT, 'rollback'); END")
        db.db.commit()
        with self.assertRaisesRegex(DatabaseException, "rollback"):
            db.insert_attempt(child, 0, [])
        self.assertIsNone(child.id)
        self.assertEqual(db.select("SELECT COUNT(*) FROM evolve"), [(1,)])

    def test_dashboard_bad_references_and_version_warning(self):
        db = self.create()
        db.db.execute("UPDATE metadata SET schema_version = 9")
        db.db.commit()
        with patch.object(dashboard, "DB_FILE", self.path), dashboard.app.test_client() as client:
            with self.assertLogs(level="WARNING"):
                response = client.get("/api/row/1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["island_ids"], [0, 1, 2])
            self.assertNotIn("reference_ids", response.get_json())
            for value in ('invalid', '{}', '[true]', '[2, 2]'):
                db.db.execute("UPDATE evolve SET reference_ids = ?", (value,))
                db.db.commit()
                self.assertEqual(client.get("/api/graph").status_code, 500)

if __name__ == "__main__":
    unittest.main()
