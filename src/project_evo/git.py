from .error import KillWorkerException, KillPoolException
from .database import PrimaryTableRow
from . import config

from contextlib import contextmanager
from uuid import uuid7
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

def exec_in_workspace(workspace: Path, cmd: list[str]):
    with workspace_locks.setdefault(workspace, Lock()):
        subprocess.run(cmd, cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def autocommit(workspace: Path):
    try:
        with workspace_locks.setdefault(workspace, Lock()):
            subprocess.run(["git", "add", "."], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Only a successful index comparison means there is nothing to commit.
            diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--exit-code", "--no-ext-diff"], cwd=workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if diff.returncode == 0:
                return
            if diff.returncode != 1:
                diff.check_returncode()
            subprocess.run(["git", "commit", "-m", "Autocommit all changes"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise KillWorkerException(f"{e.cmd} failed") from e

def bootstrap(target_branch: str):
    exec_in_workspace(config.cfg.project_root, ["git", "checkout", "-b", target_branch])
    autocommit(config.cfg.project_root)

def ensure_branch_exists(uuid: str):
    name = f"{config.cfg.branch_base}/{uuid}"
    try:
        exec_in_workspace(config.cfg.project_root, ["git", "rev-parse", "--verify", f"refs/heads/{name}"])
    except subprocess.CalledProcessError:
        raise KillPoolException(f"Invalid or corrupted database file; Database expects git branch '{name}' but it does not exist.")

def create_new_workspace(parent: PrimaryTableRow, child: PrimaryTableRow) -> Path:
    branch = f"{config.cfg.branch_base}/{child.uuid}"
    path = (config.cfg.project_root / config.cfg.workspace_base / child.uuid).resolve()
    parent_br = f"refs/heads/{config.cfg.branch_base}/{parent.uuid}"

    path.parent.mkdir(parents=True, exist_ok=True)

    exec_in_workspace(config.cfg.project_root, ["git", "worktree", "add", "-b", branch, "--", str(path), parent_br])

    return path.resolve(strict=True)

@contextmanager
def evaluation_workspace(row: PrimaryTableRow):
    path = config.cfg.project_root / config.cfg.workspace_base / f"reevaluate-{uuid7().hex}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with workspace_locks.setdefault(config.cfg.project_root, Lock()):
        subprocess.run(["git", "worktree", "add", "--detach", str(path), f"refs/heads/{config.cfg.branch_base}/{row.uuid}"],
                       cwd=config.cfg.project_root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield path
    finally:
        delete_workspace(path)

def delete_workspace(workspace: Path):
    exec_in_workspace(config.cfg.project_root, ["git", "worktree", "remove", "--force", "--", str(workspace)])
