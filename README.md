# Project Evo
> Give your coding agent a benchmark. Let it improve your codebase.

Project Evo runs Codex or OpenCode across your whole repository, tests each attempt, and tracks the best changes for you to review.

**5.11× throughput on [Singularity](https://github.com/Quinniboi10/Singularity).** Singularity run 3 went from **403 million to 2.058 billion nodes/sec** in recorded chess move-generation benchmarks. [See the results](examples/singularity/README.md#singularity-3).

### Quickstart

With [uv](https://docs.astral.sh/uv/getting-started/installation/) (handles Python 3.14 for you):

```bash
uv tool install --python 3.14 git+https://github.com/Quinniboi10/project-evo.git
```

Or with pip in a Python 3.14+ virtual environment:

```bash
python3 -m pip install git+https://github.com/Quinniboi10/project-evo.git
```

Requires Git. Real runs need an authenticated Codex or OpenCode CLI.

### Then tell your agent

Open your repository in your coding agent and paste:

```text
Read https://github.com/Quinniboi10/project-evo and follow docs/guide.md.
Set up Project Evo for this repository. Help me choose a benchmark,
configure my agent, and create an evaluator that checks correctness.
Start with 5 iterations and show me the best changes and measured improvement.
```

[Usage guide](docs/guide.md) · [Example evaluators](docs/guide.md#examples) · [Dashboard](docs/guide.md#dashboard)
