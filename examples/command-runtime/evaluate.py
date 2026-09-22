# Runs a single command and rewards lower time per run across N runs

from pathlib import Path
import subprocess
import time

# List of the main command and its arguments, to be evaluated
TARGET_COMMAND: list[str] = ["./example", "arg1", "arg2", "arg3"]
# Optionally, also provide a constant value to be passed to the standard input of the program
TARGET_STDIN: str|None = None

TARGET_SEC = 5 # How long to repeat runs for, seconds
CMD_TIMEOUT = 600 # A generous 10 minute timeout

def run_and_time_cmd(executable: list[str], cwd: Path, stdin: str|None = None) -> float:
    start = time.monotonic()
    subprocess.run(
        executable, cwd=cwd, input=stdin, text=True,
        capture_output=True, check=True, timeout=CMD_TIMEOUT
    )
    return time.monotonic() - start

def evaluate(workspace: Path) -> tuple[bool, float]:
    try:
        end_time = time.monotonic() + TARGET_SEC

        times = []
        while time.monotonic() < end_time:
            times.append(run_and_time_cmd(TARGET_COMMAND, workspace, TARGET_STDIN))

        return True, len(times) / sum(times) # Convert to runs/s to keep higher is better
    except BaseException:
        return False, 0