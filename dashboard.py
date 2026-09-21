from database import Database, PrimaryTableRow
from main import version_string

from pathlib import Path
from typing import cast

import argparse
import math

from flask import Flask, jsonify, current_app

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

node_depth_cache: dict[int, int] = {}
def calculate_node_depth(db: Database, id: int) -> int:
    if id in node_depth_cache:
        return node_depth_cache[id]

    parent_id = db.select(f"SELECT parent_id FROM {db.PRIMARY_TABLE} WHERE id = {id};")[0][0]

    if parent_id is None:
        node_depth_cache[id] = 0
    else:
        node_depth_cache[id] = calculate_node_depth(db, parent_id) + 1

    return node_depth_cache[id]

@app.get("/api/graph")
def graph_data():
    # Each call needs its own thread-local database connection
    db = Database(DB_FILE, PrimaryTableRow, True)

    nodes: list[dict] = []
    edges: list[dict] = []

    # https://graphviz.org/doc/info/colors.html
    for id, name, score, parent_id, task in db.select(f"SELECT id, name, score, parent_id, task FROM {db.PRIMARY_TABLE} ORDER BY id;"):
        nodes.append({
            "id": id,
            "name": name,
            "score_label": abbreviate_score(score),
            "score": score,
            "level": calculate_node_depth(db, id)
        })
        if parent_id is not None:
            edges.append({
                "from": parent_id,
                "to": id,
                "label": task
            })

    return jsonify({
        "nodes": nodes,
        "edges": edges,
    })

@app.get("/")
def dashboard():
    return current_app.send_static_file("index.html")

if __name__ == '__main__':
    args = build_parser().parse_args()
    DB_FILE = Path(args.db).resolve(strict=True)

    app.run()