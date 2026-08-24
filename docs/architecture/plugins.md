# Isolated plugins

Plugins add capabilities Hames does not ship. Skills add procedures. Agent
capsules subtract authority. Those three stay separate.

Plugin code never imports into the trusted Python kernel. For v0.1 every
third-party and agent-authored plugin runs as a subprocess worker. Host files
and network exist only through the CapabilityBroker, which still uses the
existing policy gate.

```text
~/.hames/plugins/
├── installed/<plugin-id>/<version>-<fingerprint>/
├── env/<plugin-id>/<fingerprint>/
└── proposals/<proposal-id>/
```

## Tools vs Skills vs plugins

| | Tools | Skills | Plugins |
|---|---|---|---|
| What | Physical capability | How to do a kind of work | New capabilities |
| Default | All harness-permitted tools | Visible catalog, load on demand | Not installed |
| AGENT.md | May only subtract | May subtract or pin catalog entries | Deny plugin tool ids the same way |

Plugin tool ids are `{plugin-id}.{tool}`, for example `project-stats.summary`.
Core tools never contain `.`. `read_only` agents receive no plugin tools.
Standard agents receive every *enabled* plugin tool, then `tools.allow` /
`tools.deny`.

## Worker

Newline-delimited JSON on stdin/stdout. Handshake advertises tools, context
sources, and event filters. Controller methods: `initialize`, `tool.execute`,
`context.collect`, `event.deliver`, `shutdown`. The worker may call
`broker.call`. A crashed worker fails that tool call and does not take down
the gateway.

## Capability broker

The worker has no project mount and no network namespace. It asks the
controller for:

- `project.read` / `project.list` (`broker:project_read`)
- `project.write` (`broker:project_write`)
- `process.run_scoped` (`broker:process_run_scoped`)
- `network.request` (`broker:network_request`, default deny)

Manifest permission is necessary, not sufficient. Each call records
`plugin.broker.*` and `policy.*` events.

## Isolation

Linux workers launch under bubblewrap: unshare all namespaces, read-only
package, tmpfs scratch, no `$HOME`, no Hames state, no project tree. Missing
`bwrap` refuses untrusted/agent-authored workers. A user-installed plugin may
run unsandboxed only when `[plugins].allow_unsandboxed_user_plugins` is true
and enable warns.

## Lifecycle

`inspect` → `install` (disabled) → `enable` (worker + tools) → `disable` /
`remove`. Install never enables. Agent-authored proposals live under
`proposals/` and cannot self-install. Permission expansion on upgrade needs a
new approval.

Do not replace the ledger, policy gate, agent loop, or context compiler.
