"""Developer/backend command line entry point."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hames import __version__
from hames.daemon import gateway_status, serve, start, stop
from hames.doctor import run_doctor
from hames.paths import HamesPaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hamesd")
    parser.add_argument("-V", "--version", action="version", version=f"hamesd {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor = subcommands.add_parser("doctor", help="check the local Hames environment")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    subcommands.add_parser("serve", help="run the gateway in the foreground")
    start_parser = subcommands.add_parser("start", help="start the persistent gateway")
    start_parser.add_argument("--json", action="store_true", dest="as_json")
    stop_parser = subcommands.add_parser("stop", help="stop the persistent gateway")
    stop_parser.add_argument("--json", action="store_true", dest="as_json")
    status_parser = subcommands.add_parser("status", help="inspect the persistent gateway")
    status_parser.add_argument("--json", action="store_true", dest="as_json")
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
    paths = HamesPaths.resolve()
    if args.command == "serve":
        serve(paths)
        return 0
    if args.command == "start":
        status = start(paths)
    elif args.command == "stop":
        status = stop(paths)
    elif args.command == "status":
        status = gateway_status(paths)
    else:
        return 2
    if args.as_json:
        print(status.to_json())
    else:
        print(f"gateway: {'healthy' if status.healthy else 'stopped'} ({status.url})")
    return 0 if status.healthy or args.command == "stop" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
