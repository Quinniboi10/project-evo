from argparse import Namespace
from pathlib import Path

from importlib.machinery import SourceFileLoader
from typing import Callable, cast

import logging
import tomllib
import time

class Config:
    def __init__(self, args: Namespace):
        with open(args.config, "rb") as f:
            self.config = tomllib.load(f)
        self.args = args

        logging.basicConfig(filename=args.logfile, level=logging.DEBUG if args.debug else logging.INFO)

        if args.objective is not None:
            self._objective_str: str = args.objective
        else:
            with open(args.objective_file) as f:
                self._objective_str = f.read()

        self._set_attributes()
        self._sanitize_inputs()

    def _sanitize_inputs(self):
        assert self.softmax_temp > 0, "Softmax temperature must be greater than 0"
        assert len(self.objective) > 0, "Objective does not exist"

    def _set_attributes(self):
        self.project_root                                  = Path(self.args.project_path).resolve(strict=True)
        self.eval_fn: Callable[[Path], tuple[bool, float]] = SourceFileLoader("_workspace_eval_module", self.args.eval_file).load_module().evaluate
        self.objective                                     = self._objective_str
        self.iterations                                    = self.args.iterations
        self.db_file                                       = Path(self.args.db if self.args.db is not None else f"./playground/databases/{round(time.time())}.db")

        self.softmax_temp     = float(self.config["general"]["temperature"])
        self.concurrency      = int(self.config["general"]["concurrency"])
        self.max_fix_attempts = int(self.config["general"]["max_fix_attempts"])
        self.llm_timeout_sec  = int(self.config["general"]["llm_timeout_sec"])

        self.branch_base    = self.config["git"]["branch_base"]
        self.workspace_base = self.config["git"]["workspace_base"]

# Global configuration, set after parsing arguments
cfg = cast(Config, None)