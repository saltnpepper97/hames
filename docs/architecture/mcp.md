# External MCP servers

Hames's gateway is the sole MCP client host. The Rust clients manage a durable
registry through authenticated gateway endpoints; provider adapters never open
MCP transports directly. Registry entries live in `~/.hames/hames.db`, and only
enabled entries are connected when the gateway starts.

## Management

The control surface is:

```text
hames mcp list [--json]
hames mcp inspect <id> [--json]
hames mcp add <id> [--cwd PATH] [--env TARGET=SOURCE_ENV] -- <command> [args...]
hames mcp add <id> --url URL [--header HEADER=SOURCE_ENV]
hames mcp enable <id> [--json]
hames mcp disable <id> [--json]
hames mcp remove <id> [--json]
```

Adding a server never enables it. `inspect` makes a temporary connection for a
disabled entry and refreshes a connected entry. Disable and remove reject a busy
server rather than terminating an active tool call. The TUI's `/mcp` command
shows the configured servers, status, transport, tools, resources, and errors.

## Transports and credentials

Stdio servers receive a small baseline environment plus explicit mappings. An
entry such as `--env API_TOKEN=MY_TOKEN` stores `MY_TOKEN` and resolves its value
from the gateway process environment when connecting. Streamable HTTP headers use
the same reference scheme. Literal credential-bearing URLs are rejected, and API
responses expose reference names but not resolved values.

The configured program or remote endpoint is trusted code with the authority of
the gateway user. Hames does not sandbox external MCP servers. Review a server
before enabling it and narrow its filesystem, network, and credential access at
the operating-system or container boundary when needed.

## Model tools and policy

Advertised server tools are namespaced as `mcp__<server>__<tool>` so they cannot
shadow built-in tools. A tool is treated as read-only only when its MCP annotations
explicitly set `readOnlyHint` and do not set `destructiveHint`. Such tools may run
in Plan mode. Every other external MCP tool is denied in Plan mode and requires
confirmation in Manual and Auto modes. A failed tool call is never replayed
automatically; Hames reconnects the transport only for later work.

Resources are available through the built-in read-only `mcp_resource_list` and
`mcp_resource_read` tools. Large text and binary results use Hames's normal bounded
tool-output and content-addressed blob handling.

## Live status

Connection changes, server logs, capability changes, tool progress, and failures
are transient `runtime.notice` events. They render in the notification area above
the input bar rather than becoming transcript messages. Global server events are
broadcast to connected sessions; per-call progress is scoped to the originating
session. Repeated log and progress updates are coalesced to protect the UI.

Enabled servers stay connected. When a transport fails, Hames marks it degraded,
reports the error, and attempts a fresh connection for subsequent work without
replaying the failed call. Tool/resource list-change notifications refresh the
cached capability view.
