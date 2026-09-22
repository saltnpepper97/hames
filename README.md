<p align="center">
  <img src="docs/assets/hames-icon.png" width="160" alt="Hames horse and harness mark on a rounded sage background">
</p>

<h1 align="center">
  Hames
</h1>

<p align="center">
  A local-first agent harness for the Web and terminal, with durable context, scheduled work, and explicit control.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="docs/implementation-plan/README.md">Roadmap</a>
</p>

> [!NOTE]
> Hames is under active development. This README describes `main`, including the Web UI
> and other changes planned for **v0.2.0**, which is still unreleased. The tagged installer
> currently installs **0.1.0**. See the [changelog](CHANGELOG.md) for what is coming next.

Hames brings coding agents and scheduled tasks into one local workspace. Work in
the browser or terminal, review plans before execution, and follow delegated work
through its own conversations. A shared gateway keeps sessions, context, tools,
and execution history durable across clients, with controls for approvals and
account connections.

## Feature set

- **Web and terminal** — a browser workspace with live chat, agent editing, settings,
  event inspection, and memory, skills, and plugin management; a Ratatui TUI and classic
  REPL share the same gateway and durable sessions.
- **Local and cloud models** — connect llama.cpp, Ollama, OpenAI API, Codex,
  DeepSeek, Z.ai API or Coding Plan, Xiaomi MiMo API or Token Plan, Grok API, and Grok Build. Choose provider,
  model, and reasoning effort, with connection controls in Web and TUI.
- **Plan, build, and review** — approve or revise a plan before execution, delegate
  to agents with their own model defaults, and follow implementation and review chats.
- **Explicit safety modes** — Manual, Auto, and Plan behavior is enforced by the
  gateway, with exact one-shot approval for high-risk operations.
- **Durable sessions and goals** — resume or branch conversations, run autonomous
  goals across bounded turns, recover context as conversations grow, and keep foreground
  chat responsive while work continues.
- **Auditable context and memory** — inspect compiled context, token use, immutable
  events, request snapshots, layered memories, corrections, and Markdown/JSONL exports.
- **Extensible tools** — built-in filesystem and shell tools, private web search,
  external MCP servers, portable agents, delegation, [isolated plugins](docs/plugins.md),
  evolving Skills, and configurable personal or project slash commands.

## Install

Hames currently targets Linux and requires:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Rust 1.85 or newer
- Git

Install the latest stable version tag:

~~~bash
curl -fsSL https://raw.githubusercontent.com/saltnpepper97/hames/main/install.sh | bash
~~~

The installer uses locked dependencies, never invokes <code>sudo</code>, and
installs <code>hames</code> to uv's user tool bin directory (normally
<code>~/.local/bin</code>). It keeps the source and Python environment under
<code>~/.local/share/hames/source</code> so the Rust client can launch its matching
gateway. Running the command again installs the latest stable version tag.
The script is fetched from main, but the installed source comes exclusively from a tag.
Set `HAMES_VERSION=0.1.0` to pin that version; branches and commit hashes are rejected.

To use the unreleased Web UI and current features, review and build from `main`:

~~~bash
git clone https://github.com/saltnpepper97/hames.git
cd hames
HAMES_INSTALL_LOCAL=1 ./install.sh
~~~

Set <code>HAMES_VERSION</code>, <code>HAMES_BIN_DIR</code>, or
<code>HAMES_INSTALL_ROOT</code> to customize a remote installation.
`HAMES_REF` remains a compatibility alias for a tag name. Local source builds
require `HAMES_INSTALL_LOCAL=1`; the default always installs tagged source.

## Quick start

Run the guided setup and verify the environment:

~~~bash
hames setup
hames doctor
~~~

Then choose your interface:

~~~bash
hames web       # Open the Web UI (unreleased v0.2.0 / main)
hames           # Open the terminal UI
hames repl      # Open the classic REPL
~~~

Setup can configure llama.cpp, Ollama, OpenAI, Grok API, Grok Build, or Codex. Hames defaults to a local
llama.cpp endpoint at <code>http://127.0.0.1:8080</code>; the gateway starts on
demand and stays available after the client exits. In Web, use **Settings** to
manage provider connections and **Agents** to configure your agents.

Useful terminal commands:

~~~text
/model                 choose a reachable provider, model, and reasoning level
/mode auto             switch between manual, auto, and plan
/sessions              resume work in the current directory
/goal <objective>      start durable autonomous work
/context               inspect what entered the model request
/usage                 inspect estimated and provider-reported usage
/memory                browse durable memories
/skills                browse available procedures
/gateway               inspect gateway health and active work
/help                   show the complete command list
~~~

Run <code>hames repl</code> for the classic line-oriented client. Piped or
redirected input selects it automatically. Run <code>hames web</code> for the
local browser interface; it starts or verifies the persistent gateway, requests
a one-time authenticated launch URL, opens it, and exits. Use
<code>hames web --no-open</code> to print the URL instead. The site remains
available for as long as the gateway is running.

## How it works

Hames separates presentation from authority:

1. The **Web UI and terminal clients** present conversations and controls. The
   SolidJS Web UI is served locally; the Rust launcher opens authenticated browser
   sessions and provides the TUI and classic REPL.
2. The **Python gateway** owns sessions, providers, context compilation, tools,
   policy decisions, memory, and background work.
3. The **event ledger** records durable, integrity-checked provenance and
   content-addressed payloads.

All clients talk to the same gateway and session model. Closing a client does not
discard an active goal or background terminal. Reopen a chat from the Web sidebar
or use <code>/sessions</code> in the terminal.

### Interaction modes

| Mode | Behavior |
| --- | --- |
| **Manual** | Confirms state-changing work with allow-once, allow-for-session, or deny. |
| **Auto** | Proceeds with ordinary trusted work and confirms dangerous operations. |
| **Plan** | Allows inspection and tests while preventing writes. |

Trusted workspace roots permit normal project work without repetitive prompts.
Known secrets, credential stores, raw devices, Hames's private state, and
deterministic high-risk shell signatures remain protected.

### Sessions, goals, and agents

Sessions preserve provider, model, reasoning effort, interaction mode, ancestry,
and transcript state. Web provides chat and agent controls in the sidebar and
composer. In the terminal, use <code>/new</code> for a fresh conversation,
<code>/sessions</code> to resume, and <code>/fork</code> to branch after an answer.

<code>/goal &lt;objective&gt;</code> starts independently bounded agent turns under
a durable supervisor. Foreground messages take priority, and explicit
evidence-backed reports advance or complete the goal. Portable
<code>AGENT.md</code> capsules define an agent's role and authority separately
from session settings; delegation creates a bounded child session rather than
silently copying the parent conversation.

### Context, memory, and correction

Web exposes conversation activity in the Events tab, with dedicated pages for
memory, Skills, and Scars. Every model request passes through a deterministic,
budgeted context compiler. In the terminal,
<code>/context</code> explains selected, compacted, and omitted sources and links
them to the exact request snapshot. <code>/inspect</code>, <code>/events</code>,
and <code>/export</code> expose the corresponding activity and provenance.

Relationship, semantic, and episodic memory provide durable continuity.
Background extraction proposes bounded facts after settled turns; explicit
<code>/remember</code> captures, review controls, typed memory tools, immutable
corrections, and permanent forgetting keep that state manageable.

Corrections can open evidence-backed Scars that connect a failure to expected
behavior and a repair. Hames routes the repair to the narrowest suitable layer,
evaluates it, and guards later runs for healing or regression.

### Skills, plugins, search, and MCP

Hames discovers Skills from project <code>.agents/skills</code>, global
<code>~/.agents/skills</code>, and its built-in catalog. Repeated successful
workflows can become evaluated, versioned procedures; Skill scripts run offline
inside Bubblewrap with a read-only project and disposable writable scratch.

Optional private web search runs through a digest-pinned, loopback-only SearXNG
container and a bundled MCP server. Hames can also connect to user-configured
stdio or Streamable HTTP MCP servers:

~~~bash
hames mcp add filesystem --cwd "$PWD" -- npx -y @modelcontextprotocol/server-filesystem "$PWD"
hames mcp inspect filesystem
hames mcp enable filesystem
hames mcp list
~~~

Configured servers begin disabled. Environment and header mappings store only the
source variable name, never its secret value. See
[MCP architecture](docs/architecture/mcp.md),
[web search](docs/architecture/web-search.md), and
[Skills](docs/architecture/skills.md).

## Configuration

For DeepSeek, Z.ai, and Xiaomi MiMo API/subscription setup, see [cloud provider connections](docs/cloud-providers.md).

State is private by default under <code>~/.hames</code>. A minimal
<code>~/.hames/config.toml</code> looks like:

~~~toml
[runtime]
default_provider = "llama_cpp"
default_model = "qwen3.8-27b"
default_reasoning_effort = "medium"
default_interaction_mode = "auto"

[providers.llama_cpp]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8080"
model = "qwen3.8-27b"
reasoning_effort = "medium"
supported_reasoning_efforts = ["low", "medium", "xhigh"]
context_window_tokens = 131072
~~~

Profile names are arbitrary, and multiple profiles may use the same adapter.
Nested environment variables override settings, for example
<code>HAMES_RUNTIME__DEFAULT_MODEL=qwen3.8-27b</code>. Set
<code>HAMES_HOME</code> to relocate all persistent state.

Fresh sessions use current runtime defaults. Resumed sessions restore their own
durable provider, model, reasoning effort, agent, workspace, and mode.

## Service

The client starts the gateway when needed. To control it directly:

~~~bash
hames gateway status
hames gateway stop
hames gateway restart
~~~

An optional systemd user unit is available at
[contrib/systemd/hames.service](contrib/systemd/hames.service). Installing or
enabling it is intentionally left to the user.

## Development

Install locked dependencies and run Hames from the checkout:

~~~bash
uv sync --locked
cargo build --locked
target/debug/hames doctor
target/debug/hames
~~~

Run the complete checks:

~~~bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
~~~

The web source lives under <code>web/</code> and uses Node 22+ with pnpm. Its
production output is committed under <code>src/hames/web_dist/</code> and
packaged with the gateway, so end-user installations do not require Node:

~~~bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web check
pnpm --dir web test
pnpm --dir web build
cargo run -p hames-repl -- web --no-open
~~~

The backend diagnostic command is also available as
<code>uv run hamesd doctor</code>. The user-facing executable is built by
<code>crates/hames-repl</code>.

## Project status

The implementation plan is the source of truth for completed and upcoming
milestones:

- [Implementation plan](docs/implementation-plan/README.md)
- [Architecture notes](docs/architecture/)
- [Create a Hames plugin](docs/plugins.md)
- [Model evaluations](docs/model-evaluations/)
- [GitHub repository](https://github.com/saltnpepper97/hames)

See [Unreleased changes](CHANGELOG.md) and [custom command configuration](docs/commands.md) for current capabilities.

## License

Hames is available under the [MIT License](LICENSE).

Custom slash commands: see [configuration and maintenance](docs/commands.md).

Select a coordinator with the chat agent picker and describe your task directly. Its configured agents can handle delegated work, with activity and results recorded in the normal conversation transcript.

### Long-running agent work

Each run defaults to a 30-minute active-work budget. This includes model and
foreground tool execution; waiting for delegated workers or human input does
not use the coordinator's budget. Every worker has its own budget.

For long builds and team workflows, set the following in `~/.hames/config.toml`
under the existing `[runtime]` section, then restart the gateway once it is idle:

```toml
[runtime]
max_active_seconds_per_run = 0
```

Zero disables the active-time cutoff for both coordinators and workers. Positive
values retain a cutoff in seconds. Model-turn and tool-call limits, individual
tool/provider timeouts, and user cancellation still apply. Running tasks keep
the budget they started with; this setting takes effect after gateway reload.

### Steering and stopping a team

Stop affects only the selected agent. Its existing workers continue running and
return their results to the parent chat. Stopping a worker reports the
interruption to its parent without stopping siblings or automatically restarting
the worker. Steer replaces only that agent's current turn; if a worker is
steered, its parent waits for the replacement turn's result.

The `agent_control` tool lets a lead inspect and wait for existing assignments.
Stopping a named worker or the whole team requires an explicit user instruction;
the lead must quote that instruction from its current user turn. Stop/Steer on
the lead itself never authorizes a team stop. Gateway shutdown still ends all
running work.
