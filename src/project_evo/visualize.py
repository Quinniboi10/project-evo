from .version import version_string
from .utils import abbreviate_score

from .database import Database
from .error import assert_db

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
    with closing(Database(args.db, require_exist=True)) as db:

        graph = graphviz.Digraph("Exploration diagram", comment="Project Evo")
        graph.attr(rankdir='LR', concentrate='true')

        rows = db.select(f"SELECT id, name, score, parent_id, task FROM {db.PRIMARY_TABLE} ORDER BY id;")
        id_to_score: dict[int, float] = {id: score for id, _, score, _, _ in rows}
        parents = {id: parent_id for id, _, _, parent_id, _ in rows}
        assert_db(all(parent is None or parent in parents for parent in parents.values()), "Lineage references a missing parent")

        best_path: set[int] = set()
        if rows:
            current = max(rows, key=lambda row: row[2])[0]
            while parents[current] is not None:
                assert_db(current not in best_path, "Lineage contains a cycle")
                best_path.add(current)
                current = parents[current]

        # https://graphviz.org/doc/info/colors.html
        for id, name, score, parent_id, task in rows:
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
