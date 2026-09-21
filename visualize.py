from main import version_string

from database import Database, PrimaryTableRow

import argparse
import math

import graphviz

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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string,
                    description="MCTS-inspired code improvement using LLMs")
    parser.add_argument("--db", help="Path to a database file to begin/resume from. Only run this on trusted databases to avoid command injections", metavar="PATH", required=True)
 
    return parser

if __name__ == "__main__":
    args = build_parser().parse_args()
    db = Database(args.db, PrimaryTableRow, require_exist=True)

    graph = graphviz.Digraph("Exploration diagram", comment="Project Evo")
    graph.attr(rankdir='LR')

    id_to_score: dict[int, float] = {}

    best_path: set[int] = set()
    curr = db.select(f"SELECT id, parent_id FROM {db.PRIMARY_TABLE} ORDER BY score DESC LIMIT 1;")[0]

    while curr[1] is not None:
        best_path.add(curr[0])
        curr = db.select(f"SELECT id, parent_id FROM {db.PRIMARY_TABLE} WHERE id = {curr[1]};")[0]

    # https://graphviz.org/doc/info/colors.html
    for id, name, score, parent_id, task in db.select(f"SELECT id, name, score, parent_id, task FROM {db.PRIMARY_TABLE} ORDER BY id;"):
        id_to_score[id] = score
        if parent_id is None:
            color = "cornflowerblue"
        elif id in best_path:
            color = "cyan3"
        elif score > id_to_score[parent_id]:
            color = "green4"
        else:
            color = None
        if color is None:
            kargs = {
                "style": "solid"
            }
        else:
            kargs = {
                "style": "filled",
                "color": color
            }
        graph.node(str(id), f"{name}: {abbreviate_score(score)}", **kargs)
        if parent_id is not None:
            graph.edge(str(parent_id), str(id), label=task)

    graph.render('./visualization', cleanup=True, view=False, format="svg", engine="dot")