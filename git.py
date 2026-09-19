from database import PrimaryTableRow
import globals

from threading import Lock
from pathlib import Path

import subprocess

def exec_in_workspace(workspace: Path, cmd: str):
    with globals.WORKSPACE_LOCKS.setdefault(workspace, Lock()):
        subprocess.run(cmd, cwd=workspace, shell=True, check=True)

def autocommit(workspace: Path):
    try:
        exec_in_workspace(workspace, f"git add . && git commit -m 'Autocommit all changes'")
    except subprocess.CalledProcessError as e:
        if e.returncode == 1: # TODO: more narrow than just checking exit code
            pass
        else:
            raise e

def bootstrap(target_branch: str):
    exec_in_workspace(globals.PROJECT_ROOT, f"git checkout -b {target_branch}")
    autocommit(globals.PROJECT_ROOT)

def create_new_workspace(parent: PrimaryTableRow, child: PrimaryTableRow) -> Path:
    branch = f"{globals.BRANCH_BASE}/{child.uuid}"
    path = f"{globals.WORKSPACE_BASE}/{child.uuid}"
    parent_br = f"{globals.BRANCH_BASE}/{parent.uuid}"

    (globals.PROJECT_ROOT / globals.WORKSPACE_BASE).mkdir(exist_ok=True)

    exec_in_workspace(globals.PROJECT_ROOT, f"git worktree add -b {branch} {path} {parent_br}")

    return (globals.PROJECT_ROOT / path).resolve(strict=True)