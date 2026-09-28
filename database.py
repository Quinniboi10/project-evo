from error import assert_db, DatabaseException
from task import Task
from version import version_string
import config

from typing import Optional, Self
from pathlib import Path
from uuid import uuid7
from contextlib import contextmanager
from threading import Lock

import json
import sqlite3
import logging
import random
import math

SCHEMA_VERSION = 1
_warned_versions: set[tuple[str, str]] = set()
_warning_lock = Lock()

@contextmanager
def database_errors():
    try:
        yield
    except (sqlite3.Error, ValueError, TypeError) as e:
        raise DatabaseException(f"Database cannot support this operation: {e}") from e

def check_schema_version(db: sqlite3.Connection, file: str|Path):
    stored, created = 0, None
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'").fetchone()
    if table is not None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(metadata)")}
        if "schema_version" in columns:
            row = db.execute("SELECT schema_version FROM metadata LIMIT 1").fetchone()
            stored = row[0] if row is not None else 0
        if "created_with_version" in columns:
            row = db.execute("SELECT created_with_version FROM metadata LIMIT 1").fetchone()
            created = row[0] if row is not None else None
    if stored != SCHEMA_VERSION:
        path = str(Path(file).resolve()) if str(file) != ":memory:" else ":memory:"
        key = (path, str(stored))
        with _warning_lock:
            if key not in _warned_versions:
                logging.log(logging.WARNING, f"Database {path} has schema version {stored}; supported version is {SCHEMA_VERSION}. Created with {created or 'unknown application version'}. Compatibility is unverified; attempting normal reads and writes without migration. Operations may fail.")
                _warned_versions.add(key)

def read_island_count(db: sqlite3.Connection) -> int:
    rows = db.execute("SELECT island_count FROM metadata").fetchall()
    assert_db(len(rows) == 1, "Database requires one metadata row")
    count = rows[0][0]
    assert_db(type(count) is int and count > 0, "Database island_count must be a positive integer")
    return count

def decode_references(value: str|None) -> list[int]|None:
    if value is None:
        return None
    references = json.loads(value)
    assert_db(isinstance(references, list) and all(type(item) is int and item > 0 for item in references), "Database reference_ids must be a list of positive integers")
    assert_db(len(set(references)) == len(references), "Database reference_ids must be unique")
    return references

class PrimaryTableRow():
    def __init__(self, name: str, uuid: str, model: Optional[str], task: Optional[Task], parent_id: Optional[int], score: float):
        self.id: Optional[int] = None
        self.name = name
        self.uuid = uuid
        self.model = model
        self.task = task.name if task is not None else None
        self.parent_id = parent_id
        self.score = score
        self.island_id: int|None = None
        self.reference_ids: list[int]|None = None

    @classmethod
    def create_new_child(cls, parent: PrimaryTableRow, task: Task) -> Self:
        return cls("New Child", uuid7().hex, None, task, parent.id, -1)

    def __repr__(self) -> str:
        args = [self.name, self.uuid, self.model, self.task, self.parent_id, self.score]
        args = [repr(a) for a in args]
        return f"{type(self).__name__}({", ".join(args)})"

class Database():
    PRIMARY_TABLE = "evolve"
    COLUMNS = "id, name, uuid, model, task, parent_id, score, island_id, reference_ids"

    def __init__(self, file: str|Path, require_exist: bool=False, *, island_count: int|None=None):
        memory = str(file) == ":memory:"
        path = Path(file)
        if require_exist:
            path = path.resolve(strict=True)
        new = memory or not path.exists()
        if island_count is not None:
            assert_db(type(island_count) is int and island_count > 0, "island_count must be a positive integer")
        assert_db(not new or island_count is not None, "Creating a database requires island_count")
        if not memory:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(":memory:" if memory else path)
        self.cursor = self.db.cursor()
        try:
            with database_errors():
                if new:
                    assert island_count is not None
                    self._init_table(island_count)
                check_schema_version(self.db, file)
                if island_count is not None:
                    self._validate(island_count)
        except BaseException:
            self.db.close()
            raise
        logging.log(logging.INFO, f"{'Created' if new else 'Loaded'} database at {file}")

    def close(self):
        self.db.close()

    def _init_table(self, count: int):
        with self.db:
            self.cursor.execute("BEGIN")
            self.cursor.execute("""CREATE TABLE evolve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                uuid TEXT NOT NULL,
                model TEXT,
                task TEXT,
                parent_id INTEGER,
                score REAL NOT NULL,
                island_id INTEGER,
                reference_ids TEXT,
                CHECK ((parent_id IS NULL AND island_id IS NULL) OR
                       (parent_id IS NOT NULL AND island_id IS NOT NULL AND island_id >= 0))
            )""")
            self.cursor.execute("CREATE INDEX attempt_island ON evolve (island_id, id)")
            self.cursor.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL, created_with_version TEXT NOT NULL, island_count INTEGER NOT NULL CHECK (island_count > 0))")
            self.cursor.execute("INSERT INTO metadata VALUES (1, ?, ?, ?)", (SCHEMA_VERSION, version_string, count))

    def _validate(self, island_count: int):
        self.cursor.execute(f"SELECT {self.COLUMNS} FROM evolve LIMIT 0")
        assert_db(read_island_count(self.db) == island_count, "Configured island_count must match the database island count")

    def _read_row(self, values: tuple) -> PrimaryTableRow:
        row = PrimaryTableRow(values[1], values[2], values[3], None, values[5], values[6])
        row.id, row.task, row.island_id = values[0], values[4], values[7]
        row.reference_ids = decode_references(values[8])
        return row

    def _insert_row(self, row: PrimaryTableRow, island_id: int|None, reference_ids: list[int]|None) -> int:
        values = (row.name, row.uuid, row.model, row.task, row.parent_id, row.score, island_id, json.dumps(reference_ids) if reference_ids is not None else None)
        self.cursor.execute("INSERT INTO evolve (name, uuid, model, task, parent_id, score, island_id, reference_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        attempt_id = self.cursor.lastrowid
        assert attempt_id is not None
        return attempt_id

    def insert_baseline(self, row: PrimaryTableRow):
        with database_errors(), self.db:
            assert_db(row.parent_id is None, "Baseline cannot have a parent")
            assert_db(not self.select("SELECT id FROM evolve LIMIT 1"), "Baseline requires an empty database")
            attempt_id = self._insert_row(row, None, None)
        row.id, row.island_id, row.reference_ids = attempt_id, None, None

    def insert_attempt(self, row: PrimaryTableRow, island_id: int, reference_ids: list[int]|None=None):
        with database_errors(), self.db:
            count = read_island_count(self.db)
            assert_db(type(island_id) is int and 0 <= island_id < count, "Attempt island is outside the configured range")
            parent = self.cursor.execute("SELECT 1 FROM evolve WHERE id = ? AND (island_id = ? OR (island_id IS NULL AND parent_id IS NULL))", (row.parent_id, island_id)).fetchone()
            assert_db(parent is not None, "Attempt parent must belong to its island")
            if reference_ids is not None:
                assert_db(all(type(reference) is int and reference > 0 for reference in reference_ids), "Inspiration references must be positive integers")
                assert_db(len(set(reference_ids)) == len(reference_ids), "Inspiration references must be unique")
                for reference_id in reference_ids:
                    reference = self.cursor.execute("SELECT id FROM evolve WHERE id = ?", (reference_id,)).fetchone()
                    assert_db(reference is not None and reference_id != row.parent_id, "Inspiration must reference an existing alternative attempt")
            attempt_id = self._insert_row(row, island_id, reference_ids)
            assert_db(reference_ids is None or all(reference < attempt_id for reference in reference_ids), "Inspiration must reference an earlier attempt")
        row.id, row.island_id = attempt_id, island_id
        row.reference_ids = list(reference_ids) if reference_ids is not None else None

    def select(self, cmd: str):
        with database_errors():
            return self.cursor.execute(cmd).fetchall()

    def iters_since_last_improvement(self, island_id: int) -> int:
        with database_errors():
            best = self.cursor.execute(f"SELECT id FROM {self.PRIMARY_TABLE} WHERE (island_id = ? OR island_id IS NULL) ORDER BY score DESC, id ASC LIMIT 1", (island_id,)).fetchone()
            if best is None:
                return 0
            return self.cursor.execute("SELECT COUNT(*) FROM evolve WHERE (island_id = ? OR island_id IS NULL) AND id > ?", (island_id, best[0])).fetchone()[0]

    def sample_inspirations(self, parent: PrimaryTableRow, limit: int, island_id: int, cross_probability: float) -> list[PrimaryTableRow]:
        with database_errors():
            if limit <= 0:
                return []

            matches = self.cursor.execute(f"SELECT {self.COLUMNS} FROM {self.PRIMARY_TABLE} WHERE (island_id = ? OR island_id IS NULL) AND id != ? ORDER BY score DESC, id ASC", (island_id, parent.id)).fetchall()
            foreign = []
            if cross_probability > 0 and random.random() < cross_probability:
                sources = self.cursor.execute(f"SELECT DISTINCT island_id FROM {self.PRIMARY_TABLE} WHERE island_id != ? AND parent_id IS NOT NULL AND id != ? ORDER BY island_id", (island_id, parent.id)).fetchall()
                if sources:
                    source = random.choice(sources)[0]
                    foreign = self.cursor.execute(f"SELECT {self.COLUMNS} FROM {self.PRIMARY_TABLE} WHERE island_id = ? AND parent_id IS NOT NULL AND id != ? ORDER BY score DESC, id ASC LIMIT 1", (source, parent.id)).fetchall()
                    logging.log(logging.INFO, f"Island {island_id} foreign inspiration {foreign[0][2]} from island {source}")

            local_limit = limit - len(foreign)
            chosen = []
            if matches and local_limit > 0:
                chosen = matches[:1] + random.sample(matches[1:], min(local_limit - 1, len(matches) - 1))
            return [self._read_row(match) for match in chosen + foreign]

    def sample_island(self) -> int:
        with database_errors():
            baseline = self.select("SELECT score FROM evolve WHERE parent_id IS NULL")
            assert_db(len(baseline) == 1, "Island sampling requires one baseline")
            count = read_island_count(self.db)
            owned = {island: (score, size) for island, score, size in self.select("SELECT island_id, MAX(score), COUNT(*) FROM evolve WHERE island_id IS NOT NULL GROUP BY island_id")}
            stats = []
            for island in range(count):
                score, size = owned.get(island, (baseline[0][0], 0))
                stats.append((island, max(baseline[0][0], score), size + 1))
            best = max(row[1] for row in stats)
            quality = [math.exp((math.log(row[1]) - math.log(best)) / config.cfg.softmax_temp) for row in stats]
            total = sum(quality)
            # Counts include the shared baseline, keeping the ratio defined for new islands.
            # Greater population imbalance reduces score bias towards uniform allocation.
            balance = min(row[2] for row in stats) / max(row[2] for row in stats)
            weights = [balance * weight / total + (1 - balance) / len(stats) for weight in quality]
            return random.choices([row[0] for row in stats], weights)[0]

    def weighted_sample(self, island_id: int) -> PrimaryTableRow:
        with database_errors():
            # Get (id, score) pairs
            samples = self.cursor.execute(f"SELECT id, score FROM {self.PRIMARY_TABLE} WHERE (island_id = ? OR island_id IS NULL) ORDER BY id", (island_id,)).fetchall()
            assert_db(bool(samples), f"Island {island_id} has no parent candidates")

            # Sanitize quickly
            bad_ids = [s[0] for s in samples if s[1] <= 0 or not math.isfinite(s[1])] # TODO: Support 0
            assert_db(len(bad_ids) == 0, f"Scores must all be greater than zero. ID(s) that failed that condition: {bad_ids}")

            # Calculate temp
            temp = config.cfg.softmax_temp * min(max((self.iters_since_last_improvement(island_id) + 1) / 3, 1), 8) # Linearly grow the temp multiplier from 1x to 8x

            # Calculate weights
            scores = [
                s[1]
                for s in samples
            ]
            max_score = max(scores)
            weights = [
                math.exp((math.log(score) - math.log(max_score)) / temp)
                for score in scores
            ]

            choice = random.choices(list(range(len(samples))), weights)[0]

            match = self.cursor.execute(f"SELECT {self.COLUMNS} FROM evolve WHERE id = ?", (samples[choice][0],)).fetchone()
            assert_db(match is not None, "Selected attempt is missing")
            return self._read_row(match)
