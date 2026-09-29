from pathlib import Path
from tempfile import TemporaryDirectory
from importlib.util import find_spec

import argparse
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

def main(argv: list[str]|None=None) -> int:
    parser = argparse.ArgumentParser(description="Bundle Project Evo and its Python dependencies into a single executable for this platform.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist", help="Destination directory (default: repository dist/)")
    parser.add_argument("--collect-all", action="append", default=[], metavar="PACKAGE", help="Also bundle an installed evaluator dependency, including its data and submodules (repeatable)")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 14):
        parser.error("Building Project Evo requires Python 3.14 or newer")
    if find_spec("PyInstaller") is None:
        parser.error("Install build dependencies first: python3 -m pip install -r requirements-build.txt")
    missing = [name for name in ("tqdm", "graphviz", "flask") if find_spec(name) is None]
    if missing:
        parser.error(f"Missing dependencies: {', '.join(missing)}. Install requirements-build.txt first")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="project-evo-build-") as directory:
        build = Path(directory)
        # A package import preserves the CLI's relative imports when frozen.
        entry = build / "project_evo.py"
        entry.write_text(
            "from multiprocessing import freeze_support\n\n"
            "if __name__ == \"__main__\":\n"
            "    freeze_support()\n"
            "    from src.main import main\n"
            "    raise SystemExit(main())\n", encoding="utf-8"
        )
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
            "--name", "project-evo", "--distpath", str(output), "--workpath", str(build / "work"),
            "--specpath", str(build), "--paths", str(ROOT),
            "--add-data", f"{ROOT / 'src' / 'dashboard'}:src/dashboard",
            "--add-data", f"{ROOT / 'configs'}:configs"
        ]
        for package in args.collect_all:
            command.extend(["--collect-all", package])
        command.append(str(entry))
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(build / "cache")})
        if result.returncode != 0:
            return result.returncode

    # Keep configuration editable and preserve it on subsequent builds.
    config = output / "config.toml"
    if not config.exists():
        shutil.copy2(ROOT / "config.toml", config)
    binary = output / ("project-evo.exe" if sys.platform == "win32" else "project-evo")
    print(f"Built: {binary}")
    print(f"Run from {output}, or pass -c {config} to the run command.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
