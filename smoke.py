from error import assert_config
import config
import llm

from argparse import Namespace
from pathlib import Path
from threading import Lock
from typing import Callable
from contextlib import contextmanager, closing

import subprocess
import tempfile
import sqlite3
import logging
import json
import os
import re

@contextmanager
def isolated_git():
    # Git subprocesses in the existing core inherit this environment.
    previous = {key: value for key, value in os.environ.items() if key.startswith("GIT_")}
    for key in previous:
        del os.environ[key]
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("GIT_"):
                del os.environ[key]
        os.environ.update(previous)

class SmokeSession:
    def __init__(self, args: Namespace):
        self.root = Path(tempfile.mkdtemp(prefix="project-evo-smoke-")).resolve()
        print(f"Smoke artifacts: {self.root}", flush=True)
        assert_config(not any(c in str(self.root) for c in '\"$`\\\n\r'), "Smoke temporary path contains unsafe shell characters")
        self.project = self.root / "project"
        self.project.mkdir()
        self.attempts: dict[Path, dict] = {}
        self.lock = Lock()

        for directory in ("database", "logs", "hooks", "template"):
            (self.root / directory).mkdir()
        args.project_path = str(self.project)
        args.db = str(self.root / "database" / self.filename(args.db, "smoke.db"))
        args.logfile = str(self.root / "logs" / self.filename(args.logfile, "project-evo.log"))
        if args.objective is None and args.objective_file is None:
            args.objective = "Improve the synthetic smoke score"

        # This evaluator only reads synthetic state; supplied evaluator code is never loaded.
        evaluator = self.root / "evaluate.py"
        evaluator.write_text('''import json

def evaluate(workspace):
    state = json.loads((workspace / "state.json").read_text())
    return state["passed"], state["score"]
''')
        args.eval_file = str(evaluator)
        args.smoke_session = self
        print(f"Smoke database: {args.db}\nSmoke log: {args.logfile}", flush=True)

        self.git("init", f"--template={self.root / 'template'}", "--initial-branch=smoke")
        for key, value in {
            "user.name": "Project Evo Smoke",
            "user.email": "smoke@example.invalid",
            "commit.gpgsign": "false",
            "core.hooksPath": str(self.root / "hooks"),
        }.items():
            self.git("config", "--local", key, value)
        (self.project / "state.json").write_text(json.dumps({"passed": True, "score": 1.0}))

    def filename(self, value: str|None, default: str) -> str:
        name = Path(value).name if value else default
        assert_config(name not in ("", ".", ".."), "Smoke output paths must have a filename")
        return name

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.project, text=True, capture_output=True, check=True).stdout

    def validate(self, cfg: config.Config):
        assert_config(cfg.iterations >= 0, "Smoke iterations must be nonnegative")
        assert_config(cfg.concurrency > 0, "Smoke concurrency must be positive")
        assert_config(cfg.max_fix_attempts >= 0, "Smoke max_fix_attempts must be nonnegative")
        for value in (cfg.branch_base, cfg.workspace_base):
            assert_config(isinstance(value, str) and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) for part in value.split("/")), "Smoke Git settings must use safe relative path components")
        result = subprocess.run(["git", "check-ref-format", "--branch", f"{cfg.branch_base}/smoke"], cwd=self.project, capture_output=True)
        assert_config(result.returncode == 0, "Invalid smoke branch_base")
        workspace = self.project / cfg.workspace_base
        assert_config(workspace.resolve().is_relative_to(self.project), "Smoke workspaces must stay inside the temporary project")
        workspace.mkdir(parents=True, exist_ok=True)
        (self.project / ".gitignore").write_text(f"/{cfg.workspace_base}/\n")
        logging.log(logging.INFO, f"SMOKE: protected synthetic run in {self.root}")

    def query(self, workspace: Path, model_name: str, prompt: str, extra_args: list[str]) -> subprocess.CompletedProcess:
        assert_config(workspace.resolve().is_relative_to(self.project), "Smoke query escaped its temporary project")
        with self.lock:
            if workspace not in self.attempts:
                state = json.loads((workspace / "state.json").read_text())
                self.attempts[workspace] = {"scenario": len(self.attempts) % 3, "calls": 0, "score": state["score"] + 1}
            attempt = self.attempts[workspace]
            attempt["calls"] += 1
            scenario = ("success", "repair", "failure")[attempt["scenario"]]
            passed = scenario == "success" or (scenario == "repair" and attempt["calls"] > 1)
            state = {"passed": passed, "score": attempt["score"], "scenario": scenario, "calls": attempt["calls"]}
        (workspace / "state.json").write_text(json.dumps(state))
        (workspace / "name.txt").write_text(f"Smoke {scenario} {workspace.name}\n")
        output = f"SMOKE: {scenario}, call {state['calls']}, passed={passed}"
        logging.log(logging.INFO, output)
        return subprocess.CompletedProcess(["smoke", model_name], 0, stdout=output, stderr="")

    def verify(self):
        cfg = config.cfg
        with closing(sqlite3.connect(cfg.db_file)) as db:
            rows = db.execute("SELECT uuid, name, score, parent_id FROM evolve").fetchall()
            uuids = dict(db.execute("SELECT id, uuid FROM evolve").fetchall())
            members = db.execute("SELECT island_id, attempt_id FROM island_membership").fetchall()
            baseline_id = db.execute("SELECT id FROM evolve WHERE parent_id IS NULL").fetchone()[0]
            assert_config({island for island, attempt in members if attempt == baseline_id} == set(range(cfg.island_count)), "Smoke baseline must seed every island")
            for attempt_id, parent_id in db.execute("SELECT id, parent_id FROM evolve WHERE parent_id IS NOT NULL"):
                islands = [island for island, attempt in members if attempt == attempt_id]
                assert_config(len(islands) == 1, "Smoke child must belong to exactly one island")
                assert_config(parent_id in uuids, "Smoke database references a missing parent")
                assert_config((islands[0], parent_id) in members, "Smoke parent belongs to a different island")
        expected = {path.name: attempt for path, attempt in self.attempts.items() if attempt["scenario"] == 0 or (attempt["scenario"] == 1 and cfg.max_fix_attempts > 0)}
        assert_config(len(self.attempts) == cfg.iterations, "Smoke did not execute all iterations")
        assert_config(len(rows) == len(expected) + 1, "Smoke database row count does not match successful attempts")
        assert_config({uuid for uuid, _, _, parent in rows if parent is not None} == set(expected), "Smoke database recorded unexpected attempts")
        for uuid, name, score, parent in rows:
            branch = f"{cfg.branch_base}/{uuid}"
            state = json.loads(self.git("show", f"{branch}:state.json"))
            assert_config(state["passed"] and state["score"] == score, "Smoke commit does not match its database score")
            if parent is not None:
                assert_config(self.git("show", f"{branch}:name.txt").strip() == name, "Smoke name was not committed")
                assert_config(parent in uuids, "Smoke database references a missing parent")
                # Each simulated query commits once, including repair attempts.
                ancestor = self.git("rev-parse", f"{branch}~{expected[uuid]['calls']}").strip()
                parent_commit = self.git("rev-parse", f"{cfg.branch_base}/{uuids[parent]}").strip()
                assert_config(ancestor == parent_commit, "Smoke commit ancestry does not match its database parent")
        for path, attempt in self.attempts.items():
            expected_calls = 1 if attempt["scenario"] == 0 else 1 + (min(1, cfg.max_fix_attempts) if attempt["scenario"] == 1 else cfg.max_fix_attempts)
            assert_config(attempt["calls"] == expected_calls, "Smoke retry count does not match configuration")
            state = json.loads(self.git("show", f"{cfg.branch_base}/{path.name}:state.json"))
            assert_config(state["calls"] == expected_calls and state["score"] == attempt["score"], "Smoke attempt changes were not committed")
        worktrees = self.git("worktree", "list", "--porcelain")
        assert_config(sum(line.startswith("worktree ") for line in worktrees.splitlines()) == 1, "Smoke worktrees were not removed")
        assert_config(not any(path.exists() for path in self.attempts), "Smoke workspace directories remain")
        assert_config(all(count == 0 for count in llm.running_processes.values()), "Smoke provider counters were not released")
        for handler in logging.getLogger().handlers:
            handler.flush()
        log = Path(cfg.args.logfile).read_text()
        assert_config("SMOKE: protected synthetic run" in log, "Smoke startup log is missing")
        if self.attempts:
            assert_config("Worker finished" in log, "Smoke worker logs are missing")
            if cfg.args.debug:
                assert_config("OUTBOUND:" in log and "INBOUND STDOUT: SMOKE:" in log, "Smoke debug I/O logs are missing")
        message = f"SMOKE PASSED: {len(self.attempts)} attempts, {len(expected)} successful, {len(self.attempts) - len(expected)} skipped; commits, database, logging and worktree cleanup verified"
        logging.log(logging.INFO, message)
        print(message)

def run(args: Namespace, run_core: Callable[[Namespace], int]) -> int:
    with isolated_git():
        session = None
        try:
            session = SmokeSession(args)
            exit_code = run_core(args)
            if exit_code == 0:
                session.verify()
            return exit_code
        except Exception as e:
            logging.log(logging.ERROR, f"SMOKE FAILED: {e}")
            print(f"SMOKE FAILED: {e}")
            return 1
        finally:
            logging.shutdown()
            if session is not None:
                print(f"Smoke artifacts retained at: {session.root}", flush=True)
