from .error import assert_config
from .evaluation import EvaluationResult

from argparse import Namespace
from pathlib import Path
from threading import BoundedSemaphore

from importlib.machinery import SourceFileLoader
from typing import Callable, cast

import math
import logging
import tomllib
import time

class Config:
    reevaluate_idle: bool = False

    def __init__(self, args: Namespace):
        with open(args.config, "rb") as f:
            self.config = tomllib.load(f)
        self.args = args

        logging.basicConfig(filename=args.logfile, level=logging.DEBUG if args.debug else logging.INFO)

        if args.objective is not None:
            self.objective: str = args.objective
        else:
            with open(args.objective_file) as f:
                self.objective = f.read()

        self._set_attributes()
        self._validate()
        if self.args.smoke:
            self.args.smoke_session.validate(self)
        else:
            self._confirm_providers()
        self.evaluation_semaphore = BoundedSemaphore(self.evaluation_concurrency)

    def _validate(self):
        assert_config(type(self.reevaluate_idle) is bool, "general.reevaluate_idle must be a boolean")
        assert_config(type(self.evaluation_concurrency) is int and self.evaluation_concurrency > 0, "general.evaluation_concurrency must be a positive integer")
        assert_config(self.softmax_temp > 0, "Softmax temperature must be greater than 0")
        assert_config(len(self.objective) > 0, "Objective does not exist")
        assert_config(type(self.inspiration_count) is int and self.inspiration_count >= 0, "general.inspiration_count is required and must be a nonnegative integer")

        assert_config(type(self.island_count) is int and self.island_count > 0, "general.island_count must be a positive integer")
        probability = self.cross_island_inspiration_probability
        assert_config(type(probability) in (int, float) and math.isfinite(probability) and 0 <= probability <= 1, "general.cross_island_inspiration_probability must be finite and between 0 and 1")

        max_concurrency = 0
        for model in self._routing.values():
            max_concurrency += self.max_concurrency(model)
        assert_config(max_concurrency >= self.concurrency, f"Config asks for concurrency of {self.concurrency} but all listed providers only supply {max_concurrency}")
        assert_config(self.max_concurrency(self.fallback_model) >= self.concurrency, f"Fallback model must be able to handle {max_concurrency} concurrent sessions")

    def _confirm_providers(self):
        for job, model in self._routing.items():
            if self.model_full_name(model).startswith("opencode/"):
                logging.log(logging.WARNING, f"{model} is routing through opencode's free model library. Ensuring user is OK with data collection.")
                response = input(f"You are routing {job} through {model} which will likely collect data. Do you accept this risk? (y/n)  ").lower()
                while response != 'y' and response != 'n':
                    response = input(f"Invalid answer, try again. (y/n)  ")
                if response == 'n':
                    exit(1)

        if any(self.adapter(model) == "opencode" for model in self._routing.values()):
            msg = "Please note, OpenCode's sandboxing is less secure than other harnesses like Codex, and subsequently to ensure better privacy, the agent will not be able to make git iteractions (though commits will be made automatically)."
            print(msg)
            logging.log(logging.WARNING, msg)


    def _set_attributes(self):
        self.project_root                                  = Path(self.args.project_path).resolve(strict=True)
        self.eval_fn: Callable[[Path], EvaluationResult|tuple[bool, float]] = SourceFileLoader("_workspace_eval_module", self.args.eval_file).load_module().evaluate
        self.iterations                                    = self.args.iterations
        self.db_file                                       = Path(self.args.db if self.args.db is not None else f"./playground/databases/{round(time.time())}.db")

        self.gnhf: bool = self.args.gnhf

        self.softmax_temp     = float(self.config["general"]["temperature"])
        self.concurrency      = int(self.config["general"]["concurrency"])
        self.evaluation_concurrency = self.config["general"].get("evaluation_concurrency", self.concurrency)
        self.reevaluate_idle = self.config["general"].get("reevaluate_idle", False)
        self.max_fix_attempts = int(self.config["general"]["max_fix_attempts"])
        self.llm_timeout_sec  = int(self.config["general"]["llm_timeout_sec"])
        self.inspiration_count = self.config["general"].get("inspiration_count")

        self.island_count = self.config["general"].get("island_count", 4)
        self.cross_island_inspiration_probability = self.config["general"].get("cross_island_inspiration_probability", 0.1)

        self.branch_base    = self.config["git"]["branch_base"]
        self.workspace_base = self.config["git"]["workspace_base"]

        self._providers = self.config["providers"]
        self._routing   = self.config["routing"]

        self.exploration_model: str = self._routing["exploration"]
        self.improvement_model: str = self._routing["improvement"]
        self.fallback_model: str    = self._routing["fallback"]

        self.models = set((self.exploration_model, self.improvement_model, self.fallback_model))

    def adapter(self, model: str) -> str:
        return self._providers[model]["adapter"]
    def model_full_name(self, model: str) -> str:
        return self._providers[model]["model"]
    def extra_args(self, model: str) -> list[str]:
        try:
            return self._providers[model]["extra_args"]
        except KeyError:
            return []
    def max_concurrency(self, model: str) -> int:
        return self._providers[model]["max_concurrency"]

# Global configuration, set after parsing arguments
cfg = cast(Config, None)
