from database import Database, PrimaryTableRow
from prompt import build_prompt
import globals
import git
import llm

from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import cast
from uuid import uuid7

import argparse
import logging
import random
import time

random.seed(42)

version_string = f"Project Evo 0.0.1"

db = cast(Database, None)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string,
                    description="MCTS-inspired code improvement using LLMs")
    parser.add_argument("project_path", help="Path to the base project", metavar="PATH")
    parser.add_argument("eval_file", help="File that provides evaluate(path: Path) -> float which is used to grade workspaces *** SHOULD BE NONDESTRICTUVE AS IT WILL BE CALLED ON THE ROOT DIRECTORY ***", metavar="PATH")
    parser.add_argument("objective", help="Objective for the LLMs to optimize towards", metavar="str")
    parser.add_argument("-i", "--iterations", help="Number of iterations to run", metavar="int", type=int, default=None)
    parser.add_argument("-t", "--temp", help="Softmax temperature to use when selecting what to explore", metavar="float", type=float, default=0.05)
    parser.add_argument("--db", help="Path to a database file to begin/resume from. Only run this on trusted databases to avoid command injections", metavar="PATH", default=None)
    parser.add_argument("--debug", help="Enable debug-level logging", action="store_true")
 
    return parser

def parse_args() -> argparse.Namespace:
    global db

    args = build_parser().parse_args()

    logging.basicConfig(filename="project-evo.log", level=logging.DEBUG if args.debug else logging.INFO)

    file = Path(args.db) if args.db is not None else f"./playground/databases/{round(time.time())}.db"
    db = Database(file, PrimaryTableRow)

    globals.PROJECT_ROOT = Path(args.project_path).resolve(strict=True)
    globals.EVAL_FN      = SourceFileLoader("_workspace_eval_module", args.eval_file).load_module().evaluate
    globals.OBJECTIVE    = args.objective
    globals.SOFTMAX_TEMP = args.temp

    assert globals.SOFTMAX_TEMP > 0, "Softmax temp must be greater than 0"

    return args

def check_requirements():
    from shutil import which
    assert which("git") is not None, "Git is required for workspaces to isolate code"

def init_db():
    # If the database is empty, bootstrap
    if len(db.select(f"SELECT id FROM {db.PRIMARY_TABLE} LIMIT 1;")) == 0:
        uuid = uuid7().hex
        db.insert(PrimaryTableRow("Baseline", uuid, None, globals.EVAL_FN(globals.PROJECT_ROOT)))
        git.bootstrap(f"{globals.BRANCH_BASE}/{uuid}")
    # Otherwise, verify all the branches still exist
    else:
        for _, uuid in db.select(f"SELECT id, uuid FROM {db.PRIMARY_TABLE};"):
            git.ensure_branch_exists(uuid)

# TODO: Make this run in a different thread
def tick():
    parent = db.weighted_sample()
    assert type(parent) == PrimaryTableRow

    child = PrimaryTableRow.create_new_child(parent)
    workspace = git.create_new_workspace(parent, child)
    
    prompt = build_prompt(parent, child, random.random() < 0.3)
    child.name = llm.work_via_codex(workspace, prompt)

    child.score = globals.EVAL_FN(workspace) # TODO: verification

    db.insert(child)

def run_ticks(iters: int | None):
    i = 0

    while iters is None or i < iters:
        tick()
        time.sleep(globals.TICK_LENGTH)
        i += 1

if __name__ == "__main__":
    args = parse_args()
    check_requirements()
    init_db()
    try:
        run_ticks(args.iterations)
    except BaseException as e:
        logging.log(logging.ERROR, f"{str(e).split(' ', 1)[0]} raised {type(e)}. {e}")
        print(f"{str(e).split(' ', 1)[0]} raised {type(e)}. Exiting...")