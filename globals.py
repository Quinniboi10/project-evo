from typing import cast, Callable
from threading import Lock
from pathlib import Path

### CONFIG
BRANCH_BASE = "project-evo"
WORKSPACE_BASE = "workspaces"
TICK_LENGTH = 0
### END CONFIG

PROJECT_ROOT = cast(Path, None)
PROJECT_GOAL = cast(str, None)
EVAL_FN      = cast(Callable, None)
OBJECTIVE    = cast(str, None)

WORKSPACE_LOCKS: dict[Path, Lock] = {}