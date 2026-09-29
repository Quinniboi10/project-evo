from .error import assert_config, assert_eval, KillWorkerException
from .prompt import build_prompt, build_run_fix_prompt, build_inspiration_context
from .database import Database, PrimaryTableRow
from .evaluation import EvaluationResult, normalize_result, format_feedback
from .reevaluation import IdleEvaluator
from .task import Task
from .version import version_string
from . import config
from . import git
from . import llm

from concurrent.futures import Future, ThreadPoolExecutor, wait
from uuid import uuid7
from contextlib import closing
from pathlib import Path

import argparse
import logging
import random

from tqdm import tqdm

random.seed(42)
_idle_evaluator: IdleEvaluator|None = None

def build_parser(add_help: bool=True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
                    prog=version_string, add_help=add_help,
                    description="MCTS-inspired code improvement using LLMs",
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                    epilog="""
  Evaluator:
      The eval_file argument must supply a file that implements the below function signature
      evaluate(path: Path) -> EvaluationResult | tuple[bool, float]
      Import EvaluationResult from project_evo.evaluation; return EvaluationResult(passed, score, feedback="").
      passed means acceptance checks passed; passing scores must be finite and greater than 0.
      Optional feedback supplies diagnostics to repairs and baseline failure errors.
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

def _evaluate(workspace: Path) -> EvaluationResult:
    if _idle_evaluator is not None:
        return _idle_evaluator.evaluate(workspace)
    with config.cfg.evaluation_semaphore:
        return normalize_result(config.cfg.eval_fn(workspace))

def init_db():
    with closing(Database(config.cfg.db_file, island_count=config.cfg.island_count)) as db:
        # If the database is empty, bootstrap
        if len(db.select(f"SELECT id FROM {db.PRIMARY_TABLE} LIMIT 1;")) == 0:
            result = _evaluate(config.cfg.project_root)
            feedback = format_feedback(result.feedback)
            assert_eval(result.passed, "Baseline failed to pass evaluate()" + (f"\n{feedback}" if feedback else ""))
            uuid = uuid7().hex
            git.bootstrap(f"{config.cfg.branch_base}/{uuid}")
            db.insert_baseline(PrimaryTableRow("Baseline", uuid, None, None, None, result.score))
        # Otherwise, verify all the branches still exist
        else:
            for _, uuid in db.select(f"SELECT id, uuid FROM {db.PRIMARY_TABLE};"):
                git.ensure_branch_exists(uuid)

def run_worker(island_id: int):
    # Database connections must be held at the per-thread level (not shared)
    with closing(Database(config.cfg.db_file, island_count=config.cfg.island_count)) as db:
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
            child.model = llm.run_agent(workspace, prompt, task)

            result = _evaluate(workspace)
            fixes = 0
            while not result.passed and fixes < config.cfg.max_fix_attempts:
                fixes += 1
                logging.log(logging.INFO, f"Run UUID {child.uuid} failed verification, retrying ({fixes}/{config.cfg.max_fix_attempts})")
                prompt = build_run_fix_prompt(parent, child, result.feedback)
                llm.run_agent(workspace, prompt, task)
                result = _evaluate(workspace)

            if not result.passed:
                logging.log(logging.WARNING, f"Skipping child UUID {child.uuid} after failing {fixes} attempts to pass")
                return # Do not add the broken child to the database as reference

            child.score = result.score

            child.name = llm.get_attempt_name(workspace)

            db.insert_attempt(child, island_id, reference_ids)
        finally:
            git.delete_workspace(workspace)

def select_island() -> int:
    with closing(Database(config.cfg.db_file, island_count=config.cfg.island_count)) as db:
        return db.sample_island()

def run_iterations() -> int:
    global _idle_evaluator
    pool = ThreadPoolExecutor(max_workers=config.cfg.concurrency)
    pending_workers: dict[Future[None], int] = {}
    iterations_to_submit = config.cfg.iterations

    try:
        if getattr(config.cfg, "reevaluate_idle", False) and iterations_to_submit > 0:
            _idle_evaluator = IdleEvaluator()
        with tqdm(total=config.cfg.iterations) as pbar:
            while iterations_to_submit > 0 or pending_workers:
                while iterations_to_submit > 0 and len(pending_workers) < config.cfg.concurrency:
                    island_id = select_island()
                    pending_workers[pool.submit(run_worker, island_id)] = island_id
                    iterations_to_submit -= 1
                watched = list(pending_workers)
                if _idle_evaluator is not None:
                    watched.append(_idle_evaluator.failure)
                done, _ = wait(watched, return_when="FIRST_COMPLETED")
                if _idle_evaluator is not None and _idle_evaluator.failure in done:
                    _idle_evaluator.failure.result()

                for future in done:
                    island_id = pending_workers.pop(future)
                    try:
                        future.result()
                    except KillWorkerException as e:
                        if not config.cfg.gnhf:
                            raise
                        logging.log(logging.ERROR, f"A worker raised {type(e)}. {e}")
                        pending_workers[pool.submit(run_worker, island_id)] = island_id # Keep replacements on the same island
                    else:
                        pbar.update(1)
        if _idle_evaluator is not None:
            _idle_evaluator.close()
            if _idle_evaluator.failure.done():
                _idle_evaluator.failure.result()
    except (Exception, KeyboardInterrupt) as e:
        if isinstance(e, KeyboardInterrupt):
            print("\nCaught keyboard interrupt. Exiting...")
        else:
            logging.log(logging.ERROR, f"A worker raised {type(e)}. {e}")
            print(f"A worker raised {type(e)}. Exiting...")
        logging.log(logging.WARNING, "Cancelling future threads and waiting for pool shutdown")
        return 0 if isinstance(e, KeyboardInterrupt) else 1
    finally:
        if _idle_evaluator is not None:
            _idle_evaluator.stop()
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        finally:
            if _idle_evaluator is not None:
                _idle_evaluator.close()
                _idle_evaluator = None
    return 0

def run(args: argparse.Namespace) -> int:
    config.cfg = config.Config(args)
    check_requirements()
    init_db()
    return run_iterations()

def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.smoke:
        from . import smoke
        exit_code = smoke.run(args, run)
    else:
        if args.project_path is None or args.eval_file is None or (args.objective is None and args.objective_file is None):
            parser.error("project_path, eval_file and --objective or --objective_file are required without --smoke")
        exit_code = run(args)
    return exit_code

if __name__ == "__main__":
    parser = build_parser()
    exit(execute(parser.parse_args(), parser))
