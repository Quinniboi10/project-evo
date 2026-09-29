from .database import Database
from .evaluation import EvaluationResult, normalize_result, format_feedback
from .error import assert_eval, EvalError, KillWorkerException
from . import config
from . import git

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Condition

import logging

class IdleEvaluator:
    def __init__(self):
        self.condition = Condition()
        self.waiters: deque[object] = deque()
        self.active = 0
        self.reserved: set[int] = set()
        self.failed: set[int] = set()
        self.stopping = False
        self.failure: Future[None] = Future()
        self.pool = ThreadPoolExecutor(max_workers=config.cfg.evaluation_concurrency)
        for _ in range(config.cfg.evaluation_concurrency):
            self.pool.submit(self._worker)

    def evaluate(self, workspace: Path) -> EvaluationResult:
        # Foreground callers queue before competing with background checks.
        token = object()
        with self.condition:
            self.waiters.append(token)
            self.condition.notify_all()
            self.condition.wait_for(lambda: self.waiters[0] is token and self.active < config.cfg.evaluation_concurrency)
            self.waiters.popleft()
            self.active += 1
            self.condition.notify_all()
        try:
            return normalize_result(config.cfg.eval_fn(workspace))
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()

    def close(self):
        self.stop()
        self.pool.shutdown(wait=True)

    def _worker(self):
        try:
            with closing(Database(config.cfg.db_file, require_exist=True)) as db:
                while True:
                    with self.condition:
                        self.condition.wait_for(lambda: self.stopping or (not self.waiters and self.active < config.cfg.evaluation_concurrency))
                        if self.stopping:
                            return
                        row = db.sample_reevaluation(self.reserved | self.failed)
                        if row is None:
                            # A new attempt may be inserted after its foreground slot is released.
                            self.condition.wait(timeout=1)
                            continue
                        assert row.id is not None
                        self.reserved.add(row.id)
                        self.active += 1
                    try:
                        with git.evaluation_workspace(row) as workspace:
                            result = normalize_result(config.cfg.eval_fn(workspace))
                            assert_eval(result.passed, f"Re-evaluation of {row.uuid} failed: {format_feedback(result.feedback)}")
                        score, count = db.record_evaluation(row.id, result.score)
                        logging.log(logging.INFO, f"Re-evaluated {row.uuid}: sample {result.score:g}, mean {score:g}, count {count}")
                    except KillWorkerException as e:
                        if not config.cfg.gnhf:
                            raise EvalError(f"Re-evaluation of {row.uuid}: {e}") from e
                        with self.condition:
                            self.failed.add(row.id)
                        logging.log(logging.ERROR, f"Re-evaluation of {row.uuid} raised {type(e)}. {e}. Skipping until restart.")
                    finally:
                        with self.condition:
                            self.reserved.remove(row.id)
                            self.active -= 1
                            self.condition.notify_all()
        except BaseException as e:
            with self.condition:
                self.stopping = True
                if not self.failure.done():
                    self.failure.set_exception(e)
                self.condition.notify_all()
