"""Small external MCP server used by runtime and gateway tests."""

from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp_types import ToolAnnotations

mcp = MCPServer(name="fixture-mcp", version="1.0.0")


@mcp.tool(
    name="echo",
    description="Echo text and one explicitly forwarded environment value.",
    annotations=ToolAnnotations(read_only_hint=True),
    structured_output=True,
)
def echo(text: str) -> dict[str, str]:
    return {"text": text, "forwarded": os.environ.get("FIXTURE_VALUE", "")}


@mcp.tool(
    name="change",
    description="Fixture tool that is not declared read-only.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def change(value: str) -> str:
    return value


@mcp.resource(
    "fixture://hello",
    name="hello",
    description="A fixture text resource.",
    mime_type="text/plain",
)
def hello() -> str:
    return "hello from an MCP resource"


if __name__ == "__main__":
    mcp.run(transport="stdio")
