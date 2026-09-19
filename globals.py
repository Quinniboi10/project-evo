from typing import cast, Callable
from threading import Lock
from pathlib import Path

### CONFIG
BRANCH_BASE = "project-evo"
WORKSPACE_BASE = "workspaces"
TICK_LENGTH = 0
MAX_FIX_ATTEMPTS = 3
### END CONFIG

PROJECT_ROOT = cast(Path, None)
EVAL_FN      = cast(Callable[[Path], tuple[bool, float]], None)
OBJECTIVE    = cast(str, None)

SOFTMAX_TEMP = cast(float, None)

WORKSPACE_LOCKS: dict[Path, Lock] = {}