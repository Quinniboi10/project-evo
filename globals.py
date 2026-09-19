from typing import cast, Callable
from threading import Lock
from pathlib import Path

### CONFIG
BRANCH_BASE = "project-evo"
WORKSPACE_BASE = "workspaces"
MAX_FIX_ATTEMPTS = 3
LLM_QUERY_TIMEOUT_SECONDS = 3*60*60 # 3 hours
### END CONFIG

DB_FILE = cast(Path, None)

PROJECT_ROOT = cast(Path, None)
EVAL_FN      = cast(Callable[[Path], tuple[bool, float]], None)
OBJECTIVE    = cast(str, None)

SOFTMAX_TEMP = cast(float, None)

WORKSPACE_LOCKS: dict[Path, Lock] = {}