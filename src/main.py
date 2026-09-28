from .version import version_string
from . import evolve as evolution
from . import dashboard
from . import visualize

import argparse

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evolve code, inspect runs and export lineage graphs.")
    parser.add_argument("--version", action="version", version=version_string)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", parents=[evolution.build_parser(add_help=False)], help="Evolve a repository or run a smoke simulation")
    commands.add_parser("dashboard", parents=[dashboard.build_parser(add_help=False)], help="Serve the lineage dashboard")
    commands.add_parser("visualize", parents=[visualize.build_parser(add_help=False)], help="Export a lineage graph")
    return parser

def main(argv: list[str]|None=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return evolution.execute(args, parser)
    if args.command == "dashboard":
        dashboard.run(args)
    else:
        visualize.run(args)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
