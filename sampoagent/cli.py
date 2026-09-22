"""Small, platform-neutral command line interface."""

import argparse
from pathlib import Path

import uvicorn

from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository


def initialize(path: str | Path = "sampoagent.db", *, demo: bool = False) -> None:
    repository = Repository(path)
    repository.initialize()
    if demo:
        repository.load_demo()


def main() -> None:
    parser = argparse.ArgumentParser(prog="sampoagent", description="Local-first job application and career agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init_parser = subcommands.add_parser("init", help="Create the local SQLite database")
    init_parser.add_argument("--database", default="sampoagent.db")
    demo_parser = subcommands.add_parser("demo", help="Load clearly marked synthetic demo data")
    demo_parser.add_argument("--database", default="sampoagent.db")
    run_parser = subcommands.add_parser("run", help="Run on localhost only")
    run_parser.add_argument("--database", default="sampoagent.db")
    run_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.database)
    elif args.command == "demo":
        initialize(args.database, demo=True)
    else:
        uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port)
