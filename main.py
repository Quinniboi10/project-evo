from database import Database, PrimaryTableRow
from prompt import build_prompt, build_run_fix_prompt
from task import Task
import config
import git
import llm

from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid7

import argparse
import logging
import random

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
    parser.add_argument("project_path", help="Path to the base project")
    parser.add_argument("eval_file", help="File that provides the function to evaluate a workspace (see below)")
    obj_group = parser.add_mutually_exclusive_group(required=True)
    obj_group.add_argument("--objective", help="Objective for the LLMs to follow", metavar="str")
    obj_group.add_argument("--objective_file", help="File from which to read the objective", metavar="PATH")
    parser.add_argument("-c", "--config", help="Path to the config.toml", metavar="PATH", default="config.toml")
    parser.add_argument("-i", "--iterations", help="Number of iterations to run", metavar="int", type=int, required=True)
    parser.add_argument("--db", help="Path to a database file to begin/resume from. Only run this on trusted databases to avoid command injections", metavar="PATH", default=None)
    parser.add_argument("--logfile", help="Path to log to", metavar="PATH", default="project-evo.log")
    parser.add_argument("--debug", help="Enable debug-level logging", action="store_true")
 
    return parser

def check_requirements():
    from shutil import which
    assert which("git") is not None, "Git is required for workspaces to isolate code"

def init_db():
    db = Database(config.cfg.db_file, PrimaryTableRow)
    # If the database is empty, bootstrap
    if len(db.select(f"SELECT id FROM {db.PRIMARY_TABLE} LIMIT 1;")) == 0:
        passed, score = config.cfg.eval_fn(config.cfg.project_root)
        assert passed, "Baseline failed to pass evaluate()"
        uuid = uuid7().hex
        db.insert(PrimaryTableRow("Baseline", uuid, None, None, None, score))
        git.bootstrap(f"{config.cfg.branch_base}/{uuid}")
    # Otherwise, verify all the branches still exist
    else:
        for _, uuid in db.select(f"SELECT id, uuid FROM {db.PRIMARY_TABLE};"):
            git.ensure_branch_exists(uuid)

def run_worker():
    # Database connections must be held at the per-thread level (not shared)
    db = Database(config.cfg.db_file, PrimaryTableRow)
    parent = db.weighted_sample()
    assert type(parent) == PrimaryTableRow

    task = Task.EXPLORE if random.random() < 0.3 else Task.IMPROVE # TODO: smarter exploration

    child = PrimaryTableRow.create_new_child(parent, task)
    workspace = git.create_new_workspace(parent, child)

    try:
        prompt = build_prompt(parent, child)
        child.model = llm.route_prompt(workspace, prompt, task)

        passed, score = config.cfg.eval_fn(workspace)
        fixes = 0
        while not passed and fixes < config.cfg.max_fix_attempts:
            fixes += 1
            logging.log(logging.INFO, f"Run UUID {child.uuid} failed verification, retrying ({fixes}/{config.cfg.max_fix_attempts})")
            prompt = build_run_fix_prompt(parent, child)
            llm.route_prompt(workspace, prompt, task)
            passed, score = config.cfg.eval_fn(workspace)

        if not passed:
            logging.log(logging.WARNING, f"Skipping child UUID {child.uuid} after failing {fixes} attempts to pass")
            return # Do not add the broken child to the database as reference

        child.score = score
        child.name = llm.get_attempt_name(workspace)

        db.insert(child)
    finally:
        git.delete_workspace(workspace)

def run_iterations():
    with ThreadPoolExecutor(max_workers=config.cfg.concurrency) as pool:
        futures = [
            pool.submit(run_worker)
            for _ in range(config.cfg.iterations)
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
    config.cfg = config.Config(build_parser().parse_args())
    check_requirements()
    init_db()
    run_iterations()