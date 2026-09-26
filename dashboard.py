from main import version_string

from pathlib import Path
from typing import cast
from contextlib import contextmanager

import sqlite3
import json
import argparse
import math

from flask import Flask, jsonify, current_app, abort

app = Flask(__name__, static_folder="dashboard")

DB_FILE = cast(Path, None)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string,
                    description="MCTS-inspired code improvement using LLMs")
    parser.add_argument("--db", help="Path to a database file to visualize", metavar="PATH", required=True)
 
    return parser

def abbreviate_score(score: float) -> str:
    leading_digits = math.floor(math.log10(score)) + 1
    # For scores less than 1, switch to scientific notation
    if leading_digits < 1:
        return f"{score:.3e}"
    # Calculate target precision
    leading_chars = (leading_digits - 1) % 3 + 1
    prec = 4 - leading_chars
    # Sort and return
    # (n digits, suffix)
    abbrs = sorted([
        (12, 'T'),
        (9, 'B'),
        (6, 'M'),
        (3, 'K'),
    ], key=lambda a: a[0], reverse=True)
    for dig, suffix in abbrs:
        if leading_digits > dig:
            return f"{score/10**dig:.{prec}f}{suffix}"

    # Degrade instead of erroring
    return f"{score:.{prec}f}"

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

if __name__ == '__main__':
    args = build_parser().parse_args()
    DB_FILE = Path(args.db).resolve(strict=True)

    app.run()