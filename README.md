# Project Evo
> Give your codebase a measure and get a coffee. Project Evo's got you covered.

### Real applications
In 1 hour, Project Evo took [Singularity](https://github.com/Quinniboi10/Singularity) (a sophisticated chess move generator) from 355 million moves/sec to 1,407 million moves/sec, a **<u>296%</u>** increase in performance

### Significance
This project is inspired by [Chaos](https://github.com/Quinniboi10/Chaos), a high-performance MCTS chess engine, and [AlphaEvolve](https://arxiv.org/pdf/2506.13131), an autonomous code evolution engine. The goal is to combine the self-evolving diffs with the bleeding edge of coding agents. Other evolve projects work to evolve small blocks of code. Project Evo lets coding agents use their native harness and tools to improve the *entire codebase*, it doesn't limit them to simple diff outputting engines.

### Quickstart
Install the following:  
At least Python 3.14, OpenAI Codex 0.155.1 or OpenCode 2.0.11  
Then run `pip3 install -r requirements.txt`

That's it! Check out the [usage](README.md#usage) to see how things are put together, or take a look at some of the [example scripts](README.md#examples)

### Usage
Use `python3 main.py -h` for the unified CLI:

```bash
python3 main.py run PROJECT EVALUATOR --objective "Improve throughput" -i 20
python3 main.py dashboard --db RUN.db
python3 main.py visualize --db RUN.db --format svg
```

Run `python3 main.py run -h` for evolution options. The individual commands remain available through `evolve.py`, `dashboard.py` and `visualize.py`.

Arguments
- `project_path` - the path to the project to be optimized. This path should be the base of a git repository (required for proper function of workspaces)
- `eval_file` - the path to the python file which provides the evaluate function*, used for validating and scoring workspaces
- `--objective <STR>` OR `--objective_file <PATH>` - The instructions or file containing instructions for the LLMs to follow
- `-c --config <PATH>` - The TOML config file to use (default: `config.toml`)
- `-i --iterations <INT>` - The number of iterations to run with one iteration being a single improvement/exploration attempt
- `--db <PATH>` - The database file to either load or resume work from
- `--logfile <PATH>` - the .log file to which all log data will be written
- `--gnhf` - Short for "good night have fun", agents will keep working and errors are logged but do not terminate work
- `--debug` - Enables writing of DEBUG level logs - Please note that debug logs include all input/output from every query sent to or from a LLM, making the log file grow very quickly
- `--smoke` - Run a protected simulation in a retained temporary repository without querying LLMs (see below)

\*The eval_file argument must supply a file that implements the below function signature  
`evaluate(path: Path) -> bool, float`  
where the boolean represents if the test succeeded, and the float represents the score (greater than 0) of the workspace  
***EVALUATE SHOULD BE NONDESTRICTUVE AS IT WILL BE CALLED ON THE PROJECT ROOT DIRECTORY TO ESTABLISH A BASELINE***

### Exploration and inspiration

Each attempt starts from one "parent" workspace. Exploration (30%) tries alternative approaches; improvement refines the current implementation.

- Set `[general].inspiration_count` in `config.toml` to limit reference attempts per prompt (`0` to disable). This number must be nonnegative.
- `[general].island_count` controls the number of islands to search; islands share the worker budget and select parents locally. Set `1` for a single population.
- Worker allocation favors higher island best scores using the configured temperature. As island node counts diverge, allocation blends toward uniform; each baseline counts as one node.
- Local references use the best-scoring alternative, then random remaining alternatives without duplicates.
- `cross_island_inspiration_probability` defaults to `0.1`: occasionally one reference slot uses another island's best non-baseline attempt. Set `0` for fully local inspiration.
- Island count is fixed when resuming a database; sharing probability may change. Pre-island databases cannot resume evolution; start a new database.
- Scores and diffs are included directly in prompts, so agents need no Git access. Each attempt retains one parent.

### Dashboard

Run `python3 main.py dashboard --db PATH` to open the lineage workbench. Filter populations, replay the global history, or explore the overall tree as it grows.

### Smoke runs

Run `python3 main.py run --smoke -i 12 --debug` to check worktrees, commits, database storage, routing and logging without querying LLMs. Requires Git.

- Uses a synthetic project and evaluator; supplied project/evaluator paths are ignored, and the objective is optional
- Keeps your config and run flags; `--db` and `--logfile` use only their filenames inside the temporary directory
- Cycles through success, repair and exhausted retries (use at least 3 iterations); expected failures are skipped normally
- Prints `SMOKE PASSED` after verification and retains the repository, database and logs at the printed location for inspection and manual cleanup

Tests: `python3 -m unittest discover -s tests -v`

Dashboard tests: `node tests/test_dashboard.js`. Browser regressions: `node tests/test_dashboard_browser.js` (requires Playwright and Chromium).

### Examples
- [Command runtime](examples/command-runtime/README.md) - Optimize toward minimal command runtime
- [Compiled runtime](examples/compiled-runtime/README.md) - Run initial setup like `make` or `cargo build`, then minimize the runtime of a command
- [Singularity](examples/singularity/README.md) - The custom evaluation code used for the 3.96x performance increase mentioned [above](README.md#real-applications)

### Limitations
Pre-commit git hooks that fail will interfere with the automatic code commits

---
Versions follow `MAJOR.MIDDLE.BUGFIX`: major for huge features, middle for intermediate improvements, and bugfix for fixes. Public releases may skip version numbers.

While all code in this repository is built around LLMs, the core code itself was written entirely by me. After v1.0.0, agents were used to accelerate development.
