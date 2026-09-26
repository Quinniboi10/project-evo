from error import KillWorkerException, KillPoolException
from database import PrimaryTableRow
import config

from threading import Lock
from pathlib import Path

import subprocess

workspace_locks: dict[Path, Lock] = {}
INSPIRATION_DIFF_LIMIT = 12_000

def inspiration_diff(parent: PrimaryTableRow, reference: PrimaryTableRow) -> str:
    parent_ref = f"refs/heads/{config.cfg.branch_base}/{parent.uuid}"
    reference_ref = f"refs/heads/{config.cfg.branch_base}/{reference.uuid}"
    with workspace_locks.setdefault(config.cfg.project_root, Lock()):
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color", parent_ref, reference_ref, "--", ".", ":(top,exclude)name.txt"],
            cwd=config.cfg.project_root, text=True, encoding="utf-8", errors="replace", capture_output=True, check=True
        )
    diff = result.stdout
    if len(diff) > INSPIRATION_DIFF_LIMIT:
        marker = "[Diff truncated; remaining changes omitted.]\n"
        prefix = diff[:INSPIRATION_DIFF_LIMIT - len(marker)]
        diff = prefix[:prefix.rfind("\n") + 1] + marker
    return diff

def exec_in_workspace(workspace: Path, cmd: str):
    with workspace_locks.setdefault(workspace, Lock()):
        subprocess.run(cmd, cwd=workspace, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def autocommit(workspace: Path):
    try:
        exec_in_workspace(workspace, f"git add . && git commit -m 'Autocommit all changes'")
    except subprocess.CalledProcessError as e:
        if e.returncode == 1: # TODO: more narrow than just checking exit code
            pass
        else:
            raise KillWorkerException(f"{e.cmd} failed")

def bootstrap(target_branch: str):
    exec_in_workspace(config.cfg.project_root, f"git checkout -b \"{target_branch}\"")
    autocommit(config.cfg.project_root)

def ensure_branch_exists(uuid: str):
    name = f"{config.cfg.branch_base}/{uuid}"
    try:
        exec_in_workspace(config.cfg.project_root, f"git rev-parse --verify \"{name}\"")
    except subprocess.CalledProcessError:
        raise KillPoolException(f"Invalid or corrupted database file; Database expects git branch '{name}' but it does not exist.")

def create_new_workspace(parent: PrimaryTableRow, child: PrimaryTableRow) -> Path:
    branch = f"{config.cfg.branch_base}/{child.uuid}"
    path = f"{config.cfg.workspace_base}/{child.uuid}"
    parent_br = f"{config.cfg.branch_base}/{parent.uuid}"

    (config.cfg.project_root / config.cfg.workspace_base).mkdir(exist_ok=True)

    exec_in_workspace(config.cfg.project_root, f"git worktree add -b \"{branch}\" \"{path}\" \"{parent_br}\"")

    return (config.cfg.project_root / path).resolve(strict=True)

def delete_workspace(workspace: Path):
    exec_in_workspace(config.cfg.project_root, f"git worktree remove --force \"{str(workspace)}\"")
