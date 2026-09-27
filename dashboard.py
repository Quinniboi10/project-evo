from version import version_string
from utils import abbreviate_score

from pathlib import Path
from typing import cast
from contextlib import contextmanager

import sqlite3
import json
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
    db = sqlite3.connect(DB_FILE.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()

def read_metadata(db: sqlite3.Connection) -> tuple[list[int], dict[int, list[int]], dict[int, list[int]]]:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    islands: list[int] = []
    memberships: dict[int, list[int]] = {}
    inspirations: dict[int, list[int]] = {}
    if {"island_settings", "island_membership"} <= tables:
        settings = db.execute("SELECT island_count FROM island_settings WHERE id = 1").fetchone()
        if settings is not None:
            islands = list(range(settings[0]))
        for island_id, attempt_id in db.execute("SELECT island_id, attempt_id FROM island_membership ORDER BY island_id"):
            memberships.setdefault(attempt_id, []).append(island_id)
    if "attempt_inspirations" in tables:
        for attempt_id, reference_ids in db.execute("SELECT attempt_id, reference_ids FROM attempt_inspirations"):
            inspirations[attempt_id] = json.loads(reference_ids)
    return islands, memberships, inspirations

@app.get("/api/row/<int:id>")
def row_data(id: int):
    with read_snapshot() as db:
        match = db.execute("SELECT * FROM evolve WHERE id = ?", (id,)).fetchone()
        if match is None:
            abort(404)
        _, memberships, inspirations = read_metadata(db)
        row = dict(match)
        row.update(island_ids=memberships.get(id, []), inspiration_ids=inspirations.get(id))
    return jsonify(row)

@app.get("/api/graph")
def graph_data():
    with read_snapshot() as db:
        rows = db.execute("SELECT id, name, score, parent_id, task FROM evolve ORDER BY id").fetchall()
        islands, memberships, inspirations = read_metadata(db)

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
            "island_ids": memberships.get(id, []), "inspiration_ids": inspirations.get(id)
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

    app.run()

if __name__ == '__main__':
    run(build_parser().parse_args())
