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
Run `python3 main.py -h` to see the help menu

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

\*The eval_file argument must supply a file that implements the below function signature  
`evaluate(path: Path) -> bool, float`  
where the boolean represents if the test succeeded, and the float represents the score (greater than 0) of the workspace  
***EVALUATE SHOULD BE NONDESTRICTUVE AS IT WILL BE CALLED ON THE PROJECT ROOT DIRECTORY TO ESTABLISH A BASELINE***

### Examples
- [Command runtime](examples/command-runtime/README.md) - Optimize toward minimal command runtime
- [Compiled runtime](examples/compiled-runtime/README.md) - Run initial setup like `make` or `cargo build`, then minimize the runtime of a command
- [Singularity](examples/singularity/README.md) - The custom evaluation code used for the 3.96x performance increase mentioned [above](README.md#real-applications)

---
While all code in this repository is built around LLMs, the core code itself was written entirely by me. LLMs did chip in for the HTML end of the dashboard since I cannot effectively write HTML.
