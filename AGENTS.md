# Repository guidance

## Project and layout

Project Evo uses coding-agent CLIs to improve a target Git repository, evaluates
attempts in isolated worktrees, and records their ancestry and scores in SQLite.
The runtime requires Python 3.14 or newer; dependencies are in `requirements.txt`.

- `src/main.py`: unified CLI entry point and subcommand routing.
- `src/evolve.py`: evolution argument parsing, baseline initialization, worker pool, evaluation and retries.
- `src/config.py` / `config.toml`: configuration loading, validation and provider routing settings.
- `src/llm.py`: Codex/OpenCode subprocess adapters and provider concurrency accounting.
- `src/git.py`: branches, worktrees, automatic commits and workspace locks.
- `src/database.py`: SQLite storage, row objects and weighted parent sampling.
- `src/error.py`, `src/task.py`, `src/prompt.py`: exception hierarchy, task enum and agent prompts.
- `src/dashboard.py` / `src/dashboard/index.html`: Flask API and browser visualization.
- `src/visualize.py`: Graphviz export; requires the Graphviz executable to render.
- `examples/`: evaluator templates and their usage instructions.
- `configs/`: OpenCode configuration and sandbox policy.

## Engineering philosophy

**Make it work first, then make it better.** Apply the user's SpaceX-inspired
approach to planning and implementation:

- Question assumptions and identify the actual behavior needed. Remove unnecessary
  requirements and proposed components before designing or optimizing them.
- Build the smallest complete implementation that demonstrates the requested
  behavior end to end. Minimize structural changes and new abstractions; every
  additional change introduces risk.
- Test the behavior, learn from failures and iterate. Working means demonstrated
  correctness, not merely code that runs or a scaffold with unfinished behavior.
- Once the behavior works, address polish, performance, user experience and release
  readiness in separate, justified increments. Defer speculative generalization,
  packaging and automation until the working implementation establishes a need.
- If performance, usability or delivery is itself the requested behavior, include
  the minimum needed to meet that requirement in the working implementation.
- Keep the existing style, compatibility, cleanup and validation requirements.
  These support a correct implementation and remain part of making it work.

## Required code style

The user's existing code style is the standard for **all new code and changes**.
Follow the nearest comparable code in the module; for new Python modules, use
`src/evolve.py`, `src/config.py`, `src/database.py` and `src/llm.py` as references. Preserve local
variations instead of normalizing the repository. Do not run broad formatting,
import sorting or style-only rewrites unless requested. No formatter or linter
configuration is currently checked in.

- Use four spaces for indentation. Use `snake_case` for Python functions,
  variables and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for
  constants and enum members. Internal helpers use a leading underscore.
- Put project imports first, then standard-library imports, then third-party
  imports, separated into small logical groups. Existing modules commonly put
  standard-library `from ... import ...` statements before plain `import ...`
  statements. Keep the surrounding order; do not impose alphabetical sorting.
- Prefer double-quoted strings and f-strings for interpolation. Preserve nearby
  single quotes where used, including embedded expressions and short literals.
- Keep the compact vertical spacing: generally one blank line between top-level
  definitions and between method bodies. Closely related short accessors may be
  adjacent, as in `Config`. Separate logical steps inside functions with blank
  lines rather than adding boilerplate.
- Keep straightforward signatures, calls and expressions on one line when
  readable, even when they exceed conventional 79/88-character limits. There is
  no established fixed line limit. For multiline calls, follow the local layout:
  related keyword arguments often share a line, and the closing parenthesis may
  sit on its own line. Do not mechanically put every argument on a separate line
  or add trailing commas everywhere.
- Preserve intentional alignment of related assignments and TOML settings, such
  as the blocks in `Config._set_attributes`. Do not align unrelated statements
  or copy accidental spacing inconsistencies.
- Use type hints for meaningful parameters, return values and typed collections,
  matching nearby coverage. Built-in generics such as `list[str]`,
  `tuple[bool, float]` and `dict[Path, Lock]` are standard. Union annotations use
  compact forms such as `str|Path` and `str|None`; existing `Optional[...]` and
  `Self` usage should be preserved. Helpers and constructors often omit `-> None`.
  Typed default spacing varies; match the surrounding signature.
- Write short comments that explain intent, constraints or stages of an
  algorithm. Inline comments commonly use one space before `#`, as in
  `return False, 0 # Explanation`. Preserve the simple `# TODO: ...` convention.
  Use concise docstrings where useful, following the exception descriptions in
  `src/error.py`; do not add templated docstrings to every function.
- Favor direct functions, explicit control flow and small classes. Extend the
  existing organization rather than introducing frameworks or abstraction layers
  for small changes. Use `Path` for filesystem paths, context managers for locks
  and files, and `try/finally` for required cleanup.
- Match existing logging with `logging.log(logging.LEVEL, message)` and f-strings.
  Use the exception types and assertion helpers in `src/error.py` for configuration,
  evaluation and database failures so worker/pool behavior remains consistent.
- In `src/dashboard/index.html`, preserve the existing plain HTML/CSS/JavaScript
  structure: four-space indentation, camelCase JavaScript names, `const`/`let`,
  semicolons, double-quoted strings and template literals. Match existing compact
  CSS rules and expanded blocks. Do not introduce a frontend build system for
  routine changes. Its style is separate from the handwritten Python core.

Style matching does not require reproducing typos, bugs or unsafe behavior.
Keep diffs short and change scope tight. Change only what is needed for the
requested behavior and its necessary tests and documentation. Preserve unrelated
behavior; avoid incidental refactors, cleanup, formatting changes or added features.

Always keep `README.md` edits short and concise, matching the rest of the file.
Include essential usage and configuration details; avoid implementation walkthroughs.

## Behavioral constraints

- Evaluators implement `evaluate(workspace: Path) -> tuple[bool, float]`; successful
  scores must be finite and greater than zero, and higher is better. Evaluation
  also runs on the target repository root to establish the baseline, so it must
  be nondestructive.
- SQLite connections belong to the calling thread. Preserve workspace locking,
  provider concurrency accounting and cleanup when changing worker behavior.
- Row attribute order is significant: `TableRow` exposes attributes positionally,
  and inserts rely on their order matching the SQLite table columns. Treat row
  or schema changes as compatibility changes for existing databases.
- Preserve CLI arguments, output contracts, configuration keys and database
  compatibility unless the task requires changes; update relevant documentation
  and examples when those interfaces change.
- Increment `version_string` in `src/version.py` when changing functionality: bump the
  last component for bug fixes, middle component for intermediate features or
  improvements, and major component only for huge features or fundamental project
  advances (think `torch.compile`). A compatibility break alone does not require a
  major bump; document migration requirements separately. Reset lower components
  when bumping a higher one. Bump once per coherent change, not per file or edit.
  Documentation-only and test-only changes do not require a bump.
- `playground/`, `.venv/`, logs, caches and generated visualizations are local
  artifacts, not source. Do not modify or commit them as part of routine changes.

## Validation

The smoke tests use standard-library unittest. Run them with
`python3 -m unittest discover -s tests -v` in a Python 3.14+ environment.
Choose focused checks for the behavior being changed and report what was run.

- Changed Python files, including tests, must have no Pyright errors or warnings.
  Run Pyright against those files with Python 3.14+ and the project's dependency
  environment; passing runtime tests alone is not sufficient. Fix typing issues
  instead of adding blanket suppressions. In tests, keep explicit `Mock` references
  for mock-only attributes such as `call_count` and `assert_called_once_with`.
  Report the checked files and results; if Pyright cannot run, state that limitation.
- Install dependencies with `python3 -m pip install -r requirements.txt` when
  needed, using a Python 3.14+ environment.
- `python3 -m src.main -h`, `python3 -m src.dashboard -h` and
  `python3 -m src.visualize -h` are useful CLI smoke checks with dependencies present.
- For syntax checks, use Python 3.14+; older interpreters are not a compatibility
  target. For behavior checks involving storage or Git, use temporary databases
  and disposable repositories.
- Full evolution runs launch external agents and create branches, commits and
  worktrees in the target project. Use them only when needed for the task and
  with an appropriate disposable target; they are not routine smoke tests.
- `python3 -m src.main run --smoke -i 12 --debug` exercises real Git and SQLite with
  simulated agents and evaluation. It retains artifacts in a printed temporary
  directory and never uses the supplied project or evaluator.
- Verify dashboard changes in the browser when layout or interaction changes.
  Example evaluators need their documented target programs and configuration;
  they are not standalone tests of Project Evo.
