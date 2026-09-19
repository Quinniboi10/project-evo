from __future__ import annotations

from pathlib import Path
from typing import Optional
from uuid import uuid7

import sqlite3
import logging
import random

class TableRow():
    def __len__(self):
        return len(vars(self))
    def __getitem__(self, key):
        if isinstance(key, int):
            return vars(self)[list(vars(self).keys())[key]]
        return getattr(self, str(key))

class PrimaryTableRow(TableRow):
    def __init__(self, name: str, uuid: str, parent_id: Optional[int], score: int):
        self.id = None # SQLite will autofill becuase of AUTOINCREMENT
        self.name = name
        self.uuid = uuid
        self.parent_id = parent_id
        self.score = score

    @classmethod
    def create_new_child(cls, parent: PrimaryTableRow):
        return cls("New Child", uuid7().hex, parent.id, -1)
    
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name}, {self.uuid}, {self.parent_id}, {self.score})"

class Database():
    PRIMARY_TABLE = "evolve"
    
    def __init__(self, file: str|Path, row_type: type):
        assert issubclass(row_type, TableRow), "Table rows must be derived from the TableRow object"

        if not isinstance(file, Path):
            file = Path(file)

        file.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(file)
        self.cursor = self.db.cursor()

        self.row_type = row_type

        self._init_table()

        logging.log(logging.INFO, f"Created new database at {file}")

    def __del__(self):
        self.db.commit()
        self.cursor.close()
        self.db.close()

    def _init_table(self):
        self.cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.PRIMARY_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                uuid TEXT NOT NULL,
                parent_id INTEGER,
                score INTEGER NOT NULL
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

    def weighted_sample(self) -> TableRow:
        # Get (id, score) pairs
        samples = self.select(f"SELECT id, score FROM {self.PRIMARY_TABLE}")

        # Sanitize quickly
        bad_ids = [s[0] for s in samples if s[1] <= 0] # TODO: Support 0
        assert len(bad_ids) == 0, f"Scores must all be greater than zero. ID(s) that failed that condition: {bad_ids}"

        # Convert to cumulative scores
        for i in range(1, len(samples)):
            samples[i] = samples[i][0], samples[i - 1][1] + samples[i][1]

        # Random sample
        target = random.randint(1, samples[-1][1])
        for s in samples:
            if target <= s[1]:
                match = self.select(f"SELECT * FROM {self.PRIMARY_TABLE} WHERE id = {s[0]};")
                assert len(match) == 1, "Duplicate IDs"

                match = match[0]
                
                # Manually construct and assign the member vars
                row = self.row_type.__new__(self.row_type)
                for col, val in zip(self.cursor.description, match):
                    setattr(row, col[0], val)

                return row

        raise KeyError("Dude what?")