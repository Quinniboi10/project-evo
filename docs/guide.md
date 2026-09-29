# Usage guide

[Install Project Evo](../README.md#quickstart), then configure it for your repository. Commands below run from your working directory; build and test commands run from the Project Evo checkout.

## Setup

Requires Git, Python 3.14+, and an authenticated OpenAI Codex (0.155.1+) or OpenCode (2.0.11+) CLI. Real runs use your configured agent account. With uv, Python is managed for you; if its tool directory is missing from `PATH`, run `uv tool update-shell` and restart your terminal.

Run `project-evo init [PATH]` to create an editable config (default: `./config.toml`); existing files are never overwritten. Review providers and models before running. Use `project-evo run -c PATH ...` for a config elsewhere.

Choose a measurable objective and configure your available providers, models and routing. Adapt an [example evaluator](#examples) or write one from scratch to check correctness and measure improvement. Verify the baseline, start with 5 iterations, then compare the baseline and best passing result and review the changes. Agents should ask about objective or provider choices they cannot infer.

## Usage

Use `project-evo -h` for the unified CLI:

```bash
project-evo --help
project-evo run PROJECT EVALUATOR --objective "Improve throughput" -i 20
project-evo dashboard --db RUN.db
project-evo visualize --db RUN.db --format svg
```

## Custom evaluation scripts

Import `EvaluationResult` from `project_evo.evaluation` and return `EvaluationResult(passed, score, feedback="")`, or keep returning `(passed: bool, score: float)`. Scores must be finite and greater than 0. Optional text feedback goes to the next repair or baseline failure error.

For evaluator dependencies, add `--with PACKAGE` to the uv install command, or install them into the same Python environment with pip.

**Evaluators must be nondestructive: they also run on the project root to establish the baseline.**

Set `[general].evaluation_concurrency` to limit simultaneous evaluator calls (the supplied config uses `1`; omitted values default to `concurrency`). Waiting evaluations occupy worker slots. This limit applies within one Evo process; agent commands and other processes can still compete for resources. Within-call repetitions belong in your evaluator; optional idle re-evaluation averages successive calls.

## Building binaries from source

With Python 3.14+, run `python3 -m pip install -r requirements-build.txt`, then `python3 src/makebin.py`. This uses [PyInstaller](https://pyinstaller.org/en/stable/) to bundle Python, project libraries, dashboard assets and sandbox files into `dist/project-evo` (`.exe` on Windows). Build on each target OS/architecture.

Run `./project-evo -h` from `dist/`; an editable `config.toml` is copied alongside the executable without overwriting existing settings. From another directory, pass `run -c PATH/config.toml`. Use `--output-dir PATH` to change the build destination. For evaluator-specific Python dependencies, install them in the build environment and repeat `--collect-all PACKAGE` as needed. Git, Codex/OpenCode, Graphviz's `dot` renderer and evaluator-specific external tools must still be installed separately.

## Wheel installation

Build with `python3 -m pip install build` and `python3 -m build`. Install with `python3 -m pip install dist/project_evo-1.11.0-py3-none-any.whl`, then use `project-evo -h` from any directory. Python 3.14+ and the external tools listed above are still required.

Version 1.11.0 changes imports from `src` to `project_evo`: evaluators should use `from project_evo.evaluation import EvaluationResult`; module commands use `python3 -m project_evo.main`. For checkout development and tests, install with `python3 -m pip install -e .` first.

## Exploration and inspiration

Each attempt starts from one "parent" workspace. Exploration (30%) tries alternative approaches; improvement refines the current implementation.

- Set `[general].inspiration_count` in `config.toml` to limit reference attempts per prompt (`0` to disable). This number must be nonnegative.
- `[general].island_count` controls the number of islands to search; islands share the worker budget and select parents locally. Set `1` for a single population.
- Worker allocation favors higher island best scores using the configured temperature. As island node counts diverge, allocation blends toward uniform; each baseline counts as one node.
- Local references use the best-scoring alternative, then random remaining alternatives without duplicates.
- `cross_island_inspiration_probability` defaults to `0.1`: occasionally one reference slot uses another island's best non-baseline attempt. Set `0` for fully local inspiration.
- Island count is fixed when resuming a database; sharing probability may change. Database schema versions are independent of application versions. Older, newer, or unversioned schemas warn and attempt normal operations; compatibility is not guaranteed and no automatic migration occurs. Use a matching application version or start a new database if incompatible.
- Scores and diffs are included directly in prompts, so agents need no Git access. Each attempt retains one parent.

## Idle re-evaluation

Set `[general].reevaluate_idle = true` to recheck saved attempts while evolution runs.
Queued candidate and repair checks take priority; an already-running recheck finishes.
The least-measured attempts (including the baseline) are selected, with random ties.
Each successful evaluator call contributes equally to the running mean `score` and
increments `evaluation_count`; internal benchmark repetitions are not counted separately.
Recheck failures raise `EvalError`. With `--gnhf`, the revision is skipped until restart;
otherwise the run stops. Failed checks leave its score and count unchanged.

This requires schema 2, which adds `evaluation_count` (initially 1). There is no automatic
migration. Background work stops with evolution. Timing evaluators still need stable
conditions; idle slots do not isolate them from agent builds or power-mode changes.

## Dashboard

Run `project-evo dashboard --db PATH` to open the lineage workbench. Filter populations, replay the global history, or explore the overall tree as it grows.

## Smoke runs

Run `project-evo run --smoke -i 12 --debug` to check worktrees, commits, database storage, routing and logging without querying LLMs. Requires Git and a config from `project-evo init` or the checkout.

- Uses a synthetic project and evaluator; supplied project/evaluator paths are ignored, and the objective is optional
- Keeps your config and run flags; `--db` and `--logfile` use only their filenames inside the temporary directory
- Cycles through success, repair and exhausted retries (use at least 3 iterations); expected failures are skipped normally
- Prints `SMOKE PASSED` after verification and retains the repository, database and logs at the printed location for inspection and manual cleanup

Tests: `python3 -m unittest discover -s src/tests -v`

Dashboard tests: `node src/tests/test_dashboard.js`. Browser regressions: `node src/tests/test_dashboard_browser.js` (requires Playwright and Chromium).

## Examples

- [Command runtime](../examples/command-runtime/README.md) - Optimize toward minimal command runtime
- [Compiled runtime](../examples/compiled-runtime/README.md) - Run initial setup like `make` or `cargo build`, then minimize the runtime of a command
- [Singularity](../examples/singularity/README.md) - The 5.11× flagship result and a move-generation evaluator

## Limitations

Pre-commit git hooks that fail will interfere with the automatic code commits

## Background

This project is inspired by [Chaos](https://github.com/Quinniboi10/Chaos), a high-performance MCTS chess engine, and [AlphaEvolve](https://arxiv.org/pdf/2506.13131), an autonomous code evolution engine. The goal is to combine the self-evolving diffs with the bleeding edge of coding agents. Other evolve projects work to evolve small blocks of code. Project Evo lets coding agents use their native harness and tools to improve the *entire codebase*, it doesn't limit them to simple diff outputting engines.

---

Versions follow `MAJOR.MIDDLE.BUGFIX`: major for huge features, middle for intermediate improvements, and bugfix for fixes. Public releases may skip version numbers.

While all code in this repository is built around LLMs, the core code itself was written entirely by me. After v1.0.0, agents were used to accelerate development.
