"""Isolated project-stats worker. Speaks newline JSON; host I/O via broker.call."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import json
import sys


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def broker_call(method: str, arguments: dict[str, object]) -> dict[str, object]:
    send(
        {
            "id": "broker-1",
            "method": "broker.call",
            "params": {"method": method, "arguments": arguments},
        }
    )
    reply = json.loads(sys.stdin.readline())
    result = reply.get("result")
    return result if isinstance(result, dict) else {}


def count_files(listed: dict[str, object]) -> int:
    structured = listed.get("structured_data")
    if isinstance(structured, dict):
        entries = structured.get("entries")
        if isinstance(entries, list):
            return sum(
                1 for item in entries if isinstance(item, dict) and item.get("type") == "file"
            )
    content = str(listed.get("content") or "")
    return sum(1 for line in content.splitlines() if line.startswith("file\t"))


def main() -> None:
    for raw in sys.stdin:
        message = json.loads(raw)
        method = str(message.get("method", ""))
        message_id = message.get("id")
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
                                "description": (
                                    "Count files in the project root via the capability broker."
                                ),
                                "input_schema": {"type": "object"},
                            }
                        ],
                        "context_sources": ["project-stats"],
                        "event_filters": ["tool.completed"],
                    },
                }
            )
        elif method == "tool.execute":
            listed = broker_call("project.list", {"path": "."})
            total = count_files(listed)
            send(
                {
                    "id": message_id,
                    "result": {
                        "summary": f"{total} files",
                        "content": f"project root contains {total} files",
                        "structured": {"files": total},
                    },
                }
            )
        elif method == "context.collect":
            listed = broker_call("project.list", {"path": "."})
            total = count_files(listed)
            send(
                {
                    "id": message_id,
                    "result": {"sources": [{"id": "project-stats", "text": f"file count {total}"}]},
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
