import argparse
from pathlib import Path

from apipulse import __version__
from apipulse.config import resolve_settings

COMMANDS = ("refresh-inventory", "probe", "render", "serve")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apipulse")
    parser.add_argument("--version", action="version", version=f"apipulse {__version__}")
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sub.add_parser(name).add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    print(f"{args.command}: not implemented")
    settings = resolve_settings(args.data_dir)
    print(f"data_dir: {settings.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
