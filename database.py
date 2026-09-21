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
        raise NotImplementedError("Cannot create dummy of TableRow")
    def __len__(self):
        return len(vars(self))
    def __getitem__(self, key):
        if isinstance(key, int):
            return vars(self)[list(vars(self).keys())[key]]
        return getattr(self, str(key))

class PrimaryTableRow(TableRow):
    def __init__(self, name: str, uuid: str, model: Optional[str], task: Optional[Task], parent_id: Optional[int], score: float):
        self.id = None # SQLite will autofill becuase of AUTOINCREMENT
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

class Database():
    PRIMARY_TABLE = "evolve"
    
    def __init__(self, file: str|Path, row_type: type, require_exist: bool=False):
        assert issubclass(row_type, TableRow), "Table rows must be derived from the TableRow object"

        if not isinstance(file, Path):
            file = Path(file)

        if require_exist:
            file = file.resolve(strict=True)

        file.parent.mkdir(parents=True, exist_ok=True)

        already_exists = file.exists()

        self.db = sqlite3.connect(file)
        self.cursor = self.db.cursor()

        self.row_type = row_type

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
                    assert db_cols == row_vals, f"Database has {db_cols} columns while {row_vals} columns are expected. Is your database from and old version?"
            except AttributeError:
                raise sqlite3.DatabaseError("Database already exists but row format does not match expectation")
            logging.log(logging.INFO, f"Loaded database at {str(file)}")
        else:
            self._init_table()
            logging.log(logging.INFO, f"Created new database at {str(file)}")

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
    
    def insert(self, new_rows: TableRow|list[TableRow]):
        if isinstance(new_rows, TableRow):
            new_rows = [new_rows]

        if len(new_rows) == 0:
            return

        expected_type = type(new_rows[0])
        for row in new_rows:
            if type(row) != expected_type:
                raise sqlite3.DatabaseError(f"Cannot insert different types of rows into a single table ({type(row)} and {expected_type})")
        
        self.cursor.executemany(f"INSERT INTO {self.PRIMARY_TABLE} VALUES({', '.join(['?'] * len(new_rows[0]))})", new_rows)
        self.db.commit()
    
    def select(self, cmd: str):
        return self.cursor.execute(cmd).fetchall()

    def iters_since_last_improvement(self) -> int:
        best = self.select(f"SELECT id FROM {self.PRIMARY_TABLE} ORDER BY score DESC LIMIT 1;")
        if len(best) == 0:
            return 0
        best = best[0][0]

        last = self.select(f"SELECT id FROM {self.PRIMARY_TABLE} ORDER BY id DESC LIMIT 1;")[0][0]

        return int(last) - int(best)

    def weighted_sample(self) -> TableRow:
        # Get (id, score) pairs
        samples = self.select(f"SELECT id, score FROM {self.PRIMARY_TABLE}")

        # Sanitize quickly
        bad_ids = [s[0] for s in samples if s[1] <= 0] # TODO: Support 0
        assert len(bad_ids) == 0, f"Scores must all be greater than zero. ID(s) that failed that condition: {bad_ids}"

        # Calculate temp
        temp = config.cfg.softmax_temp * min(max((self.iters_since_last_improvement() + 1) / 3, 1), 8) # Linearly grow the temp multiplier from 1x to 8x

        # Calculate weights
        scores = [
            s[1]
            for s in samples
        ]
        max_score = max(scores)
        weights = [
            math.exp(math.log(score / max_score) / temp)
            for score in scores
        ]

        choice = random.choices(list(range(len(samples))), weights)[0]

        match = self.select(f"SELECT * FROM {self.PRIMARY_TABLE} WHERE id = {samples[choice][0]};")
        assert len(match) == 1, "Duplicate IDs"

        match = match[0]

        # Manually construct and assign the member vars
        row = self.row_type.__new__(self.row_type)
        for col, val in zip(self.cursor.description, match):
            setattr(row, col[0], val)

        return row