"""Developer/backend command line entry point."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hames import __version__
from hames.doctor import run_doctor
from hames.paths import HamesPaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hamesd")
    parser.add_argument("-V", "--version", action="version", version=f"hamesd {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor = subcommands.add_parser("doctor", help="check the local Hames environment")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        report = run_doctor(HamesPaths.resolve())
        if args.as_json:
            print(json.dumps(report.model_dump(), sort_keys=True))
        else:
            for key, value in report.model_dump().items():
                print(f"{key}: {value}")
        return 0 if report.healthy else 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
