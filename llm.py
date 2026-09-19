import globals
import git

from pathlib import Path

import subprocess
import logging

def work_via_codex(workspace: Path, prompt: str):
    logging.log(logging.INFO, f"Starting worker in {str(workspace)}")
    logging.log(logging.DEBUG, f"OUTBOUND: {prompt}")

    try:
        result = subprocess.run(
            ["codex", "exec", "--ephemeral", "--cd", str(workspace), "--sandbox", "workspace-write", "-m", "gpt-6-astra", "-"],
            input=prompt, text=True, capture_output=True, timeout=globals.LLM_QUERY_TIMEOUT_SECONDS
        )

        logging.log(logging.DEBUG, f"INBOUND STDERR: {result.stderr}")
        logging.log(logging.DEBUG, f"INBOUND STDOUT: {result.stdout}")
    
        result.check_returncode()

        logging.log(logging.INFO, f"Worker finished in {str(workspace)}")
    except subprocess.TimeoutExpired:
        logging.log(logging.WARNING, f"Worker in {str(workspace)} timed out")

    git.autocommit(workspace)

def get_attempt_name(workspace: Path) -> str:
    try:
        with open(workspace / "name.txt", "r") as f:
            lines = f.readlines()
            if len(lines) > 0:
                return lines[0].strip()
    except FileNotFoundError:
        pass
    return "FAILED TO FETCH NAME"