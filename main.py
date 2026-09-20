from database import Database, PrimaryTableRow
from prompt import build_prompt, build_run_fix_prompt
import globals
import git
import llm

from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.machinery import SourceFileLoader
from pathlib import Path
from uuid import uuid7

import argparse
import logging
import random
import time

from tqdm import tqdm

random.seed(42)

version_string = f"Project Evo 0.0.1"

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string,
                    description="MCTS-inspired code improvement using LLMs",
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                    epilog="""
  Evaluator:
      The eval_file argument must supply a file that implements the below function signature
      evaluate(path: Path) -> bool, float
      where the boolean represents if the test succeeded, and the float represents the score (greater than 0) of the workspace
      *** EVALUATE SHOULD BE NONDESTRICTUVE AS IT WILL BE CALLED ON THE ROOT DIRECTORY ***
 """)
    parser.add_argument("project_path", help="Path to the base project", metavar="PATH")
    parser.add_argument("eval_file", help="File that provides the function to evaluate a workspace (see below)", metavar="PATH")
    parser.add_argument("objective", help="Objective for the LLMs to optimize towards", metavar="str")
    parser.add_argument("-i", "--iterations", help="Number of iterations to run", metavar="int", type=int, required=True)
    parser.add_argument("-t", "--temp", help="Softmax temperature to use when selecting what to explore", metavar="float", type=float, default=0.05)
    parser.add_argument("-c", "--concurrency", help="Number of concurrent threads to run", metavar="int", type=int, default=5)
    parser.add_argument("--db", help="Path to a database file to begin/resume from. Only run this on trusted databases to avoid command injections", metavar="PATH", default=None)
    parser.add_argument("--debug", help="Enable debug-level logging", action="store_true")
 
    return parser

def parse_args() -> argparse.Namespace:
    global db

    args = build_parser().parse_args()

    logging.basicConfig(filename="project-evo.log", level=logging.DEBUG if args.debug else logging.INFO)

    globals.DB_FILE = Path(args.db) if args.db is not None else f"./playground/databases/{round(time.time())}.db"

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
    db = Database(globals.DB_FILE, PrimaryTableRow)
    # If the database is empty, bootstrap
    if len(db.select(f"SELECT id FROM {db.PRIMARY_TABLE} LIMIT 1;")) == 0:
        passed, score = globals.EVAL_FN(globals.PROJECT_ROOT)
        assert passed, "Baseline failed to pass evaluate()"
        uuid = uuid7().hex
        db.insert(PrimaryTableRow("Baseline", uuid, None, score))
        git.bootstrap(f"{globals.BRANCH_BASE}/{uuid}")
    # Otherwise, verify all the branches still exist
    else:
        for _, uuid in db.select(f"SELECT id, uuid FROM {db.PRIMARY_TABLE};"):
            git.ensure_branch_exists(uuid)

def run_worker():
    # Database connections must be held at the per-thread level (not shared)
    db = Database(globals.DB_FILE, PrimaryTableRow)
    parent = db.weighted_sample()
    assert type(parent) == PrimaryTableRow

    child = PrimaryTableRow.create_new_child(parent)
    workspace = git.create_new_workspace(parent, child)
    
    prompt = build_prompt(parent, child, random.random() < 0.3)
    llm.work_via_codex(workspace, prompt)

    passed, score = globals.EVAL_FN(workspace)
    fixes = 0
    while not passed and fixes < globals.MAX_FIX_ATTEMPTS:
        fixes += 1
        logging.log(logging.INFO, f"Run UUID {child.uuid} failed verification, retrying ({fixes}/{globals.MAX_FIX_ATTEMPTS})")
        prompt = build_run_fix_prompt(parent, child)
        llm.work_via_codex(workspace, prompt)
        passed, score = globals.EVAL_FN(workspace)

    if not passed:
        logging.log(logging.WARNING, f"Skipping child UUID {child.uuid} after failing {fixes} attempts to pass")
        return # Do not add the broken child to the database as reference

    child.score = score
    child.name = llm.get_attempt_name(workspace)

    db.insert(child)

def run_iterations(iters: int, concurrency: int):
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(run_worker)
            for _ in range(iters)
        ]

        for future in tqdm(as_completed(futures), total=len(futures), dynamic_ncols=True):
            try:
                result = future.result()
            except BaseException as e:
                logging.log(logging.ERROR, f"A worker raised {type(e)}. {e}")
                print(f"A worker raised {type(e)}. Exiting...")
                logging.log(logging.WARNING, "Cancelling future threads and waiting for pool shutdown")
                pool.shutdown(wait=True, cancel_futures=True)
                break

if __name__ == "__main__":
    args = parse_args()
    check_requirements()
    init_db()
    run_iterations(args.iterations, args.concurrency)