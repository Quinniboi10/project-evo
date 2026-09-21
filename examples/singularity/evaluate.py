# Build Singularity, check nodes, and average NPS across N runs

# Runs D6 on kiwipete with cross-process concurrency of 1

from pathlib import Path
import subprocess
import re

import filelock

RUNS = 3
CMD_TIMEOUT = 60
SUMMARY = re.compile(r"^(\d+) nodes (\d+) nps\s*$", re.MULTILINE)

def run_cmd(executable: list[str], cwd: Path, stdin: str|None = None):
    result = subprocess.run(
        executable, cwd=cwd, input=stdin, text=True,
        capture_output=True, check=True, timeout=CMD_TIMEOUT
    )
    return result

def evaluate(workspace: Path) -> tuple[bool, float]:
    try:
        with filelock.FileLock("/tmp/project-evo-singularity.lock"):
            workspace = Path(workspace).resolve(strict=True)
            try:
                run_cmd(["make", "-j"], workspace) # It's OK to do this since build files are gitignore-d and won't pollute the workspace, and also cleaned at the end

                runs: list[tuple[str, str]] = []
                for _ in range(RUNS):
                    result = run_cmd(["./Singularity"], workspace, "position kiwipete\nbulk 6\nquit\n")
                    counts = SUMMARY.findall(result.stdout)[0]
                    runs.append(counts)

                for nodes, _ in runs:
                    if int(nodes) != 8031647685:
                        raise RuntimeError("Invalid bench")

                return True, sum(int(nps) for _, nps in runs) / RUNS
            finally:
                run_cmd(["make", "clean"], workspace)
    except BaseException:
        return False, 0