from error import assert_db, DatabaseException
from task import Task
import config

from typing import Optional, Self
from pathlib import Path
from uuid import uuid7

import sqlite3
import logging
import random
import math

class TableRow():
    @classmethod
    def create_dummy(cls) -> Self:
        raise DatabaseException("Cannot create dummy of TableRow")
    def __len__(self):
        return len(vars(self))
    def __getitem__(self, key):
        if isinstance(key, int):
            return vars(self)[list(vars(self).keys())[key]]
        return getattr(self, str(key))

class PrimaryTableRow(TableRow):
    def __init__(self, name: str, uuid: str, model: Optional[str], task: Optional[Task], parent_id: Optional[int], score: float):
        self.id: Optional[int] = None # SQLite will autofill becuase of AUTOINCREMENT
        self.name = name
        self.uuid = uuid
        self.model = model
        self.task = task.name if task is not None else None
        self.parent_id = parent_id
        self.score = score

    @classmethod
    def create_new_child(cls, parent: PrimaryTableRow, task: Task) -> Self:
        return cls("New Child", uuid7().hex, None, task, parent.id, -1)

    @classmethod
    def create_dummy(cls) -> Self:
        return cls("Dummy", "0", None, None, None, 0)
    
    def __repr__(self) -> str:
        args = [self.name, self.uuid, self.model, self.task, self.parent_id, self.score]
        args = [repr(a) for a in args]
        return f"{type(self).__name__}({", ".join(args)})"

class Database[Row: TableRow]():
    PRIMARY_TABLE = "evolve"
    
    def __init__(self, file: str|Path, row_type: type[Row], require_exist: bool=False):
        assert_db(issubclass(row_type, TableRow), "Table rows must be derived from the TableRow object")

        if not isinstance(file, Path):
            file = Path(file)

        if require_exist:
            file = file.resolve(strict=True)

        file.parent.mkdir(parents=True, exist_ok=True)

        already_exists = file.exists()

        self.db = sqlite3.connect(file)
        self.cursor = self.db.cursor()

        self.row_type = row_type
        self.already_exists = already_exists

        if already_exists:
            try:
                query = self.select(f"SELECT * FROM {self.PRIMARY_TABLE} LIMIT 1;")
                if len(query) > 0:
                    # Try to get attributes on a row and make sure the columns match
                    row = self.row_type.create_dummy()
                    for col in self.cursor.description:
                        getattr(row, col[0])
                    db_cols = len(self.cursor.description)
                    row_vals = len(vars(row))
                    assert_db(db_cols == row_vals, f"Database has {db_cols} columns while {row_vals} columns are expected. Is your database from and old version?")
            except AttributeError:
                raise DatabaseException("Database already exists but row format does not match expectation")
            logging.log(logging.INFO, f"Loaded database at {str(file)}")
        else:
            self._init_table()
            logging.log(logging.INFO, f"Created new database at {str(file)}")

    def close(self):
        self.db.close()

    def _init_table(self):
        self.cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.PRIMARY_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                uuid TEXT NOT NULL,
                model TEXT,
                task TEXT,
                parent_id INTEGER,
                score REAL NOT NULL
            );
        """)
    
    def init_islands(self, count: int):
        tables = {row[0] for row in self.select("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "island_settings" not in tables:
            assert_db(not self.already_exists, "Database lacks island metadata; start with a new database")
            with self.db:
                self.cursor.execute("CREATE TABLE island_settings (id INTEGER PRIMARY KEY CHECK (id = 1), island_count INTEGER NOT NULL CHECK (island_count > 0))")
                self.cursor.execute("CREATE TABLE island_membership (island_id INTEGER NOT NULL, attempt_id INTEGER NOT NULL, PRIMARY KEY (island_id, attempt_id))")
                self.cursor.execute("CREATE INDEX membership_attempt ON island_membership (attempt_id)")
                self.cursor.execute("INSERT INTO island_settings VALUES (1, ?)", (count,))
        stored = self.select("SELECT island_count FROM island_settings WHERE id = 1")
        assert_db(stored == [(count,)], "Configured island_count must match the database island count")
        assert_db("island_membership" in tables or not self.already_exists, "Database lacks island membership; start with a new database")

    def insert_baseline(self, row: PrimaryTableRow):
        with self.db:
            assert_db(not self.select(f"SELECT id FROM {self.PRIMARY_TABLE} LIMIT 1"), "Baseline requires an empty database")
            self.cursor.execute(f"INSERT INTO {self.PRIMARY_TABLE} VALUES ({', '.join(['?'] * len(row))})", row)
            attempt_id = self.cursor.lastrowid
            count = self.select("SELECT island_count FROM island_settings WHERE id = 1")[0][0]
            self.cursor.executemany("INSERT INTO island_membership VALUES (?, ?)", [(i, attempt_id) for i in range(count)])
        row.id = attempt_id

    def insert_attempt(self, row: PrimaryTableRow, island_id: int):
        with self.db:
            # The parent membership also validates the destination island.
            parent = self.cursor.execute("SELECT 1 FROM island_membership WHERE island_id = ? AND attempt_id = ?", (island_id, row.parent_id)).fetchone()
            assert_db(parent is not None, "Attempt parent must belong to its island")
            self.cursor.execute(f"INSERT INTO {self.PRIMARY_TABLE} VALUES ({', '.join(['?'] * len(row))})", row)
            attempt_id = self.cursor.lastrowid
            self.cursor.execute("INSERT INTO island_membership VALUES (?, ?)", (island_id, attempt_id))
        row.id = attempt_id

    def insert(self, new_rows: TableRow|list[TableRow]):
        if isinstance(new_rows, TableRow):
            new_rows = [new_rows]

        if len(new_rows) == 0:
            return

        expected_type = type(new_rows[0])
        for row in new_rows:
            if type(row) != expected_type:
                raise DatabaseException(f"Cannot insert different types of rows into a single table ({type(row)} and {expected_type})")
        
        self.cursor.executemany(f"INSERT INTO {self.PRIMARY_TABLE} VALUES({', '.join(['?'] * len(new_rows[0]))})", new_rows)
        self.db.commit()
    
    def select(self, cmd: str):
        return self.cursor.execute(cmd).fetchall()

    def iters_since_last_improvement(self, island_id: int) -> int:
        best = self.cursor.execute(f"SELECT e.id FROM {self.PRIMARY_TABLE} e JOIN island_membership m ON m.attempt_id = e.id WHERE m.island_id = ? ORDER BY e.score DESC, e.id ASC LIMIT 1", (island_id,)).fetchone()
        if best is None:
            return 0
        return self.cursor.execute("SELECT COUNT(*) FROM island_membership WHERE island_id = ? AND attempt_id > ?", (island_id, best[0])).fetchone()[0]

    def sample_inspirations(self, parent: PrimaryTableRow, limit: int, island_id: int, cross_probability: float) -> list[Row]:
        if limit <= 0:
            return []

        matches = self.cursor.execute(f"SELECT e.* FROM {self.PRIMARY_TABLE} e JOIN island_membership m ON m.attempt_id = e.id WHERE m.island_id = ? AND e.id != ? ORDER BY e.score DESC, e.id ASC", (island_id, parent.id)).fetchall()
        columns = [col[0] for col in self.cursor.description]
        foreign = []
        if cross_probability > 0 and random.random() < cross_probability:
            sources = self.cursor.execute(f"SELECT DISTINCT m.island_id FROM island_membership m JOIN {self.PRIMARY_TABLE} e ON e.id = m.attempt_id WHERE m.island_id != ? AND e.parent_id IS NOT NULL AND e.id != ? ORDER BY m.island_id", (island_id, parent.id)).fetchall()
            if sources:
                source = random.choice(sources)[0]
                foreign = self.cursor.execute(f"SELECT e.* FROM {self.PRIMARY_TABLE} e JOIN island_membership m ON m.attempt_id = e.id WHERE m.island_id = ? AND e.parent_id IS NOT NULL AND e.id != ? ORDER BY e.score DESC, e.id ASC LIMIT 1", (source, parent.id)).fetchall()
                logging.log(logging.INFO, f"Island {island_id} foreign inspiration {foreign[0][2]} from island {source}")

        local_limit = limit - len(foreign)
        chosen = []
        if matches and local_limit > 0:
            chosen = matches[:1] + random.sample(matches[1:], min(local_limit - 1, len(matches) - 1))
        rows = []
        for match in chosen + foreign:
            row = self.row_type.__new__(self.row_type)
            for col, val in zip(columns, match):
                setattr(row, col, val)
            rows.append(row)
        return rows

    def sample_island(self) -> int:
        stats = self.select(f"SELECT m.island_id, MAX(e.score), COUNT(*) FROM island_membership m JOIN {self.PRIMARY_TABLE} e ON e.id = m.attempt_id GROUP BY m.island_id ORDER BY m.island_id")
        assert_db(bool(stats), "No islands available for sampling")
        best = max(row[1] for row in stats)
        quality = [math.exp((math.log(row[1]) - math.log(best)) / config.cfg.softmax_temp) for row in stats]
        total = sum(quality)
        # Counts include the shared baseline, keeping the ratio defined for new islands.
        # Greater population imbalance reduces score bias towards uniform allocation.
        balance = min(row[2] for row in stats) / max(row[2] for row in stats)
        weights = [balance * weight / total + (1 - balance) / len(stats) for weight in quality]
        return random.choices([row[0] for row in stats], weights)[0]

    def weighted_sample(self, island_id: int) -> Row:
        # Get (id, score) pairs
        samples = self.cursor.execute(f"SELECT e.id, e.score FROM {self.PRIMARY_TABLE} e JOIN island_membership m ON m.attempt_id = e.id WHERE m.island_id = ? ORDER BY e.id", (island_id,)).fetchall()
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

        match = self.select(f"SELECT * FROM {self.PRIMARY_TABLE} WHERE id = {samples[choice][0]};")
        assert_db(len(match) == 1, "Duplicate IDs")

        match = match[0]

        # Manually construct and assign the member vars
        row = self.row_type.__new__(self.row_type)
        for col, val in zip(self.cursor.description, match):
            setattr(row, col[0], val)

        return row
