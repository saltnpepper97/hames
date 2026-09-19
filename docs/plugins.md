# Creating Hames plugins

Use a plugin when Hames needs a new capability: a callable tool, extra model
context, or an event observer. Use a Skill when you only need to teach an agent
how to perform a repeatable procedure with capabilities it already has. Use MCP
when an existing external MCP server already provides the integration.

A plugin is a folder with a `plugin.toml` manifest and a worker program. Hames
copies the folder into its private plugin store, keeps it disabled after install,
and runs it outside the trusted gateway process when you explicitly enable it.

## Smallest useful package

```text
project-stats/
├── plugin.toml
└── worker.py
```

```toml
id = "project-stats"
name = "Project Stats"
version = "0.1.0"
api_version = 1
entrypoint = "worker.py"
capabilities = ["tool"]
permissions = ["broker:project_read"]
```

The `id` is also the namespace for every tool exposed by the worker. A worker
tool named `summary` becomes `project-stats.summary` inside Hames.

The available capabilities are:

- `tool`: register model-callable tools.
- `context`: contribute bounded text to context compilation.
- `event`: observe selected durable runtime events.

Only request permissions the worker actually uses:

- `broker:project_read` for `project.read` and `project.list`.
- `broker:project_write` for `project.write`.
- `broker:process_run_scoped` for scoped child processes.
- `broker:network_request` for network requests; policy still defaults to deny.

## Worker protocol

The worker reads one JSON object per line from stdin and writes one JSON object
per line to stdout. It must flush stdout after every response. Hames calls:

- `initialize` once. Return `plugin_id`, `version`, `api_version`, `tools`,
  `context_sources`, and `event_filters`.
- `tool.execute` when the model calls one of the advertised tools. Return
  `summary`, `content`, and optionally `structured`.
- `context.collect` when context is compiled. Return a `sources` array.
- `event.deliver` for each subscribed event. Return an empty result.
- `shutdown` before a normal stop.

Workers cannot directly see the workspace when sandboxing is active. To use an
approved host capability, send a request back to Hames with method
`broker.call`, then wait for its matching response. Every broker request still
passes through manifest permissions, runtime policy, and the event ledger.

The repository's
[`project-stats` fixture](../tests/fixtures/plugins/project-stats/worker.py) is a
complete compact worker showing initialization, a broker call, tool output,
context contribution, event delivery, and shutdown.

## Inspect, install, and test

In the Web UI, open **Plugins**, select **Add plugin**, and choose the package
folder. Hames uploads the folder, validates the manifest and package paths, then
shows its fingerprint and requested permissions before installation. The
gateway-path option is available for local plugin development.

The CLI follows the same lifecycle:

```bash
hames plugin inspect ./project-stats
hames plugin install ./project-stats
hames plugin enable project-stats
hames plugin disable project-stats
```

Install and enable are deliberately separate. Test the worker protocol without
depending on private gateway internals, verify each declared capability, and
confirm denied broker permissions fail safely. Changing a package changes its
fingerprint; permission expansion must be reviewed again.

## Extension boundaries

Hames has two complementary plugin layers. Runtime plugins are isolated worker
packages for untrusted capabilities. Web features are compile-time `WebPlugin`
contributions for routes, sidebars, transcript nodes, composer controls, and
composer actions.
First-party UI is registered through the core WebPlugin rather than hard-coded
into the shell. Loading third-party UI code at runtime is intentionally not
supported yet: isolated runtime plugins may extend agent capability, but cannot
replace the ledger, policy gate, agent loop, context compiler, or trusted Web UI.

The protocol and security details are in
[`architecture/plugins.md`](architecture/plugins.md).
