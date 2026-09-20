from task import Task
import config
import git

from threading import Lock
from pathlib import Path

import subprocess
import logging

router_lock = Lock()
running_processes: dict[str, int] = {}

def _codex(workspace: Path, model_name: str, prompt: str, extra_args: list[str]):
    logging.log(logging.INFO, f"Starting worker in {str(workspace)}")
    logging.log(logging.DEBUG, f"OUTBOUND: {prompt}")

    try:
        result = subprocess.run(
            ["codex", "exec", "--ephemeral", "--cd", str(workspace), "--sandbox", "workspace-write", "-m"] + [model_name] + extra_args + ["-"],
            input=prompt, text=True, capture_output=True, timeout=config.cfg.llm_timeout_sec
        )

        if result.returncode != 0:
            logging.log(logging.ERROR, f"Codex session returned non-zero exit code")
            logging.log(logging.ERROR, f"INBOUND STDERR: {result.stderr}")
            logging.log(logging.ERROR, f"INBOUND STDOUT: {result.stdout}")
            result.check_returncode()
        else:
            logging.log(logging.DEBUG, f"INBOUND STDERR: {result.stderr}")
            logging.log(logging.DEBUG, f"INBOUND STDOUT: {result.stdout}")

        logging.log(logging.INFO, f"Worker finished in {str(workspace)}")
    except subprocess.TimeoutExpired:
        logging.log(logging.WARNING, f"Worker in {str(workspace)} timed out")

    git.autocommit(workspace)

def route_prompt(workspace: Path, prompt: str, task: Task) -> str:
    # Needs to be locked so multiple workers can't add the same thing multiple times when run in parallel (like startup)
    with router_lock:
        if task == Task.EXPLORE:
            model = config.cfg.exploration_model
        else:
            model = config.cfg.improvement_model

        if running_processes.get(model, 0) >= config.cfg.max_concurrency(model):
            model = config.cfg.fallback_model
        
        running_processes[model] = running_processes.get(model, 0) + 1

    adapter = config.cfg.adapter(model)
    try:
        if adapter == "codex":
            _codex(workspace, config.cfg.model_full_name(model), prompt, config.cfg.extra_args(model))
        else:
            raise NotImplementedError(f"Cannot route to model '{model}' because adapter '{adapter}' cannot be found")
    finally:
        with router_lock:
            running_processes[model] -= 1

    return model

def get_attempt_name(workspace: Path) -> str:
    try:
        with open(workspace / "name.txt", "r") as f:
            lines = f.readlines()
            if len(lines) > 0:
                return lines[0].strip()
    except FileNotFoundError:
        pass
    return "FAILED TO FETCH NAME"