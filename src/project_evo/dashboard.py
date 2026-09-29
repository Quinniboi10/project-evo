from .version import version_string
from .database import Database, check_schema_version, read_island_count, decode_references, database_errors
from .error import DatabaseException
from .utils import abbreviate_score

from pathlib import Path
from typing import cast
from contextlib import contextmanager

import sqlite3
import argparse

from flask import Flask, jsonify, current_app, abort

app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent / "dashboard"))

DB_FILE = cast(Path, None)

def build_parser(add_help: bool=True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string, add_help=add_help,
                    description="MCTS-inspired code improvement using LLMs")
    parser.add_argument("--db", help="Path to a database file to visualize", metavar="PATH", required=True)
 
    return parser

@contextmanager
def read_snapshot():
    with database_errors():
        db = sqlite3.connect(DB_FILE.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            check_schema_version(db, DB_FILE)
            yield db
        finally:
            db.close()

def read_metadata(db: sqlite3.Connection) -> list[int]:
    return list(range(read_island_count(db)))

def attempt_metadata(row: sqlite3.Row, islands: list[int]) -> dict:
    return {
        "island_ids": islands if row["island_id"] is None else [row["island_id"]],
        "inspiration_ids": decode_references(row["reference_ids"])
    }

@app.errorhandler(DatabaseException)
def database_error(error: DatabaseException):
    return jsonify({"error": str(error)}), 500

@app.get("/api/row/<int:id>")
def row_data(id: int):
    with read_snapshot() as db:
        match = db.execute(f"SELECT {Database.COLUMNS} FROM evolve WHERE id = ?", (id,)).fetchone()
        if match is None:
            abort(404)
        islands = read_metadata(db)
        row = dict(match)
        del row["island_id"], row["reference_ids"]
        row.update(attempt_metadata(match, islands))
    return jsonify(row)

@app.get("/api/graph")
def graph_data():
    with read_snapshot() as db:
        rows = db.execute("SELECT id, name, score, parent_id, task, island_id, reference_ids FROM evolve ORDER BY id").fetchall()
        islands = read_metadata(db)
        metadata = {row["id"]: attempt_metadata(row, islands) for row in rows}

    nodes: list[dict] = []
    edges: list[dict] = []
    parents = {row["id"]: row["parent_id"] for row in rows}
    depths: dict[int, int] = {}
    for row in rows:
        # Snapshot-local, iterative depth calculation also handles deep trees.
        path = []
        current = row["id"]
        seen = set()
        while current in parents and current not in depths and current not in seen:
            seen.add(current)
            path.append(current)
            current = parents[current]
        depth = depths.get(current, -1)
        for ancestor in reversed(path):
            depth += 1
            depths[ancestor] = depth
        id = row["id"]
        nodes.append({
            "id": id, "name": row["name"], "score_label": abbreviate_score(row["score"]),
            "score": row["score"], "level": depths[id],
            **metadata[id]
        })
        if row["parent_id"] is not None:
            edges.append({"from": row["parent_id"], "to": id, "label": row["task"]})
    return jsonify({"nodes": nodes, "edges": edges, "islands": islands})

@app.get("/")
def dashboard():
    return current_app.send_static_file("index.html")

def run(args: argparse.Namespace):
    global DB_FILE
    DB_FILE = Path(args.db).resolve(strict=True)

    with read_snapshot() as db:
        read_metadata(db)
        db.execute(f"SELECT {Database.COLUMNS} FROM evolve LIMIT 0")
    app.run()

if __name__ == '__main__':
    run(build_parser().parse_args())
