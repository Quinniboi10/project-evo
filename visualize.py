from version import version_string
from utils import abbreviate_score

from database import Database, PrimaryTableRow

from contextlib import closing

import argparse

import graphviz

def build_parser(add_help: bool=True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string, add_help=add_help,
                    description="MCTS-inspired code improvement using LLMs")
    parser.add_argument("--db", help="Path to a database file to visualize", metavar="PATH", required=True)
    parser.add_argument("-f", "--format", help="Format to export", metavar="TYPE", default="svg")
 
    return parser

def run(args: argparse.Namespace):
    with closing(Database(args.db, PrimaryTableRow, require_exist=True)) as db:

        graph = graphviz.Digraph("Exploration diagram", comment="Project Evo")
        graph.attr(rankdir='LR', concentrate='true')

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

        graph.render('./visualization', cleanup=True, view=False, format=args.format, engine="dot")

if __name__ == "__main__":
    run(build_parser().parse_args())
