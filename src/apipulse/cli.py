import argparse
from pathlib import Path

from apipulse import __version__
from apipulse.commands import refresh_inventory
from apipulse.config import resolve_settings
from apipulse.prober import probe, to_json

COMMANDS = ("refresh-inventory", "probe", "render", "serve")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apipulse")
    parser.add_argument("--version", action="version", version=f"apipulse {__version__}")
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        command_parser = sub.add_parser(name)
        command_parser.add_argument("--data-dir", type=Path, default=None)
        if name == "refresh-inventory":
            command_parser.add_argument("--source", default="seed")
    probe_one = sub.add_parser("probe-one")
    probe_one.add_argument("url")
    probe_one.add_argument("--timeout-s", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "probe-one":
        print(to_json(probe(args.url, timeout_s=args.timeout_s)))
        return 0
    settings = resolve_settings(args.data_dir)
    if args.command == "refresh-inventory":
        print(refresh_inventory.run(settings, source=args.source))
        return 0
    print(f"{args.command}: not implemented")
    print(f"data_dir: {settings.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
