"""Stdio JSON worker used by protocol tests. Not imported into the gateway."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import json
import sys
import time


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        message = json.loads(raw)
        method = str(message.get("method", ""))
        message_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            send(
                {
                    "id": message_id,
                    "result": {
                        "plugin_id": "project-stats",
                        "version": "0.1.0",
                        "api_version": 1,
                        "tools": [
                            {
                                "name": "summary",
                                "description": "Count project files",
                                "input_schema": {"type": "object"},
                            }
                        ],
                        "context_sources": ["project-stats"],
                        "event_filters": ["tool.completed"],
                    },
                }
            )
        elif method == "tool.execute":
            arguments = params.get("arguments") or {}
            if arguments.get("crash"):
                raise SystemExit(1)
            if arguments.get("sleep"):
                time.sleep(float(arguments["sleep"]))
            if arguments.get("use_broker"):
                send(
                    {
                        "id": "broker-1",
                        "method": "broker.call",
                        "params": {
                            "method": "project.list",
                            "arguments": {"path": "."},
                        },
                    }
                )
                reply = json.loads(sys.stdin.readline())
                send(
                    {
                        "id": message_id,
                        "result": {
                            "summary": "listed",
                            "content": json.dumps(reply.get("result", {})),
                        },
                    }
                )
                continue
            send(
                {
                    "id": message_id,
                    "result": {"summary": "ok", "content": "3 files"},
                }
            )
        elif method == "context.collect":
            send(
                {
                    "id": message_id,
                    "result": {"sources": [{"id": "project-stats", "text": "file count 3"}]},
                }
            )
        elif method == "event.deliver":
            send({"id": message_id, "result": {}})
        elif method == "shutdown":
            send({"id": message_id, "result": {}})
            return
        else:
            send({"id": message_id, "error": f"unknown method: {method}"})


if __name__ == "__main__":
    main()
