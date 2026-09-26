from error import assert_config, assert_eval, KillWorkerException
from prompt import build_prompt, build_run_fix_prompt, build_inspiration_context
from database import Database, PrimaryTableRow
from task import Task
import config
import git
import llm

from concurrent.futures import ThreadPoolExecutor, wait
from uuid import uuid7
from contextlib import closing

import argparse
import logging
import random
import math

from tqdm import tqdm

random.seed(42)

version_string = f"Project Evo 1.3.1"

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
    parser.add_argument("project_path", help="Path to the base project (optional with --smoke)", nargs="?")
    parser.add_argument("eval_file", help="File that provides the function to evaluate a workspace (optional with --smoke)", nargs="?")
    obj_group = parser.add_mutually_exclusive_group()
    obj_group.add_argument("--objective", help="Objective for the LLMs to follow", metavar="str")
    obj_group.add_argument("--objective_file", help="File from which to read the objective", metavar="PATH")
    parser.add_argument("-c", "--config", help="Path to the config.toml", metavar="PATH", default="config.toml")
    parser.add_argument("-i", "--iterations", help="Number of iterations to run", metavar="int", type=int, required=True)
    parser.add_argument("--db", help="Path to a database file to begin/resume from. Only run this on trusted databases to avoid command injections", metavar="PATH", default=None)
    parser.add_argument("--logfile", help="Path to log to", metavar="PATH", default="project-evo.log")
    parser.add_argument("--gnhf", help="Short for \"good night have fun\". Agents will keep working and errors are logged but do not terminate work", action="store_true")
    parser.add_argument("--debug", help="Enable debug-level logging", action="store_true")
    parser.add_argument("--smoke", help="Simulate a protected run in a retained temporary repository without querying LLMs", action="store_true")
 
    return parser

def check_requirements():
    from shutil import which
    assert_config(which("git") is not None, "Git is required for workspaces to isolate code")
    if config.cfg.args.smoke:
        return
    for adapter in set(config.cfg.adapter(m) for m in config.cfg.models):
        assert_config(which(adapter) is not None, f"The current configuration expects the command '{adapter}' but it cannot be found")

def init_db():
    with closing(Database(config.cfg.db_file, PrimaryTableRow)) as db:
        db.init_islands(config.cfg.island_count)
        # If the database is empty, bootstrap
        if len(db.select(f"SELECT id FROM {db.PRIMARY_TABLE} LIMIT 1;")) == 0:
            passed, score = config.cfg.eval_fn(config.cfg.project_root)
            assert_eval(passed, "Baseline failed to pass evaluate()")
            assert_eval(score > 0 and math.isfinite(score), "Evaluation function must be greater than 0 and finite")
            uuid = uuid7().hex
            git.bootstrap(f"{config.cfg.branch_base}/{uuid}")
            db.insert_baseline(PrimaryTableRow("Baseline", uuid, None, None, None, score))
        # Otherwise, verify all the branches still exist
        else:
            for _, uuid in db.select(f"SELECT id, uuid FROM {db.PRIMARY_TABLE};"):
                git.ensure_branch_exists(uuid)

def run_worker(island_id: int):
    # Database connections must be held at the per-thread level (not shared)
    with closing(Database(config.cfg.db_file, PrimaryTableRow)) as db:
        parent = db.weighted_sample(island_id)
        assert type(parent) == PrimaryTableRow

        task = Task.EXPLORE if random.random() < 0.3 else Task.IMPROVE # TODO: smarter exploration

        child = PrimaryTableRow.create_new_child(parent, task)
        workspace = git.create_new_workspace(parent, child)

        try:
            references = db.sample_inspirations(parent, config.cfg.inspiration_count, island_id, config.cfg.cross_island_inspiration_probability)
            logging.log(logging.INFO, f"Island {island_id} attempt {child.uuid}, parent {parent.uuid}, inspirations {[row.uuid for row in references]}")
            inspirations, reference_ids = build_inspiration_context(parent, references)
            prompt = build_prompt(parent, child, inspirations)
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

            assert_eval(score > 0 and math.isfinite(score), "Evaluated scores must be greater than 0 and finitee")

            child.name = llm.get_attempt_name(workspace)

            db.insert_attempt(child, island_id, reference_ids)
        finally:
            git.delete_workspace(workspace)

def select_island() -> int:
    with closing(Database(config.cfg.db_file, PrimaryTableRow)) as db:
        return db.sample_island()

def run_iterations() -> int:
    with ThreadPoolExecutor(max_workers=config.cfg.concurrency) as pool:
        pending = {}
        remaining = config.cfg.iterations

        try:
            with tqdm(total=config.cfg.iterations) as pbar:
                while remaining > 0 or pending:
                    while remaining > 0 and len(pending) < config.cfg.concurrency:
                        island_id = select_island()
                        pending[pool.submit(run_worker, island_id)] = island_id
                        remaining -= 1
                    done, _ = wait(pending, return_when="FIRST_COMPLETED")

                    for future in done:
                        island_id = pending.pop(future)
                        try:
                            future.result()
                            pbar.update(1)
                        except (Exception, KeyboardInterrupt) as e:
                            logging.log(logging.ERROR, f"A worker raised {type(e)}. {e}")
                            if isinstance(e, KillWorkerException) and config.cfg.gnhf:
                                replacement = pool.submit(run_worker, island_id)
                                pending[replacement] = island_id # Keep replacements on the same island
                            else:
                                print(f"A worker raised {type(e)}. Exiting...")
                                logging.log(logging.WARNING, "Cancelling future threads and waiting for pool shutdown")
                                pool.shutdown(wait=True, cancel_futures=True)
                                if isinstance(e, KeyboardInterrupt): # Exiting cleanly on Ctrl+C should not be a "failed" execution
                                    return 0
                                return 1
        except KeyboardInterrupt:
            print("\nCaught keyboard interrupt. Exiting...")
            logging.log(logging.WARNING, "Cancelling future threads and waiting for pool shutdown")
            pool.shutdown(wait=True, cancel_futures=True)
            return 0
    return 0

def run(args: argparse.Namespace) -> int:
    config.cfg = config.Config(args)
    check_requirements()
    init_db()
    return run_iterations()

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.smoke:
        import smoke
        exit_code = smoke.run(args, run)
    else:
        if args.project_path is None or args.eval_file is None or (args.objective is None and args.objective_file is None):
            parser.error("project_path, eval_file and --objective or --objective_file are required without --smoke")
        exit_code = run(args)
    exit(exit_code)
