"""Developer/backend command line entry point."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hames import __version__
from hames.daemon import gateway_status, serve, start, stop
from hames.doctor import run_doctor
from hames.paths import HamesPaths
from hames.search_service import SearchService


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
    search_parser = subcommands.add_parser("search", help="manage private web search")
    search_commands = search_parser.add_subparsers(dest="search_action", required=True)
    setup = search_commands.add_parser("setup", help="persist consent and provision SearXNG")
    setup_choice = setup.add_mutually_exclusive_group(required=True)
    setup_choice.add_argument("--enable", action="store_true")
    setup_choice.add_argument("--disable", action="store_true")
    for action in ("status", "start", "stop", "restart", "update"):
        command = search_commands.add_parser(action)
        command.add_argument("--json", action="store_true", dest="as_json")
    setup.add_argument("--json", action="store_true", dest="as_json")
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
    if args.command == "search":
        service = SearchService(paths)
        if args.search_action == "setup":
            status = service.setup(enabled=bool(args.enable))
        elif args.search_action == "status":
            status = service.status()
        elif args.search_action == "start":
            status = service.ensure_running()
        elif args.search_action == "stop":
            status = service.stop()
        elif args.search_action == "restart":
            status = service.restart()
        elif args.search_action == "update":
            status = service.update()
        else:  # pragma: no cover
            return 2
        if args.as_json:
            print(status.model_dump_json())
        else:
            print(f"search: {status.status}" + (f" ({status.error})" if status.error else ""))
        if args.search_action in {"setup", "stop"}:
            return 0
        return 0 if status.status in {"ready", "disabled"} else 1
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
