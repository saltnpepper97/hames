<p align="center">
  <a href="https://github.com/saltnpepper97/hames">
    <img src="docs/assets/hames.png" width="280" alt="Hames horse and harness mark">
  </a>
</p>

<h1 align="center">
  <a href="https://github.com/saltnpepper97/hames">Hames</a>
</h1>

<p align="center">
  A local-first agent harness with durable context, explicit control, and a terminal-native interface.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="docs/implementation-plan/README.md">Roadmap</a>
</p>

> [!NOTE]
> Hames is under active development. M0–M9 and the Ratatui portion of M10 are
> implemented; interfaces and storage formats may still change.

Hames is a local coding-agent runtime built around one principle: capable agents
should remain inspectable and under your control. A trusted Python gateway owns
execution, policy, context, and provenance, while a fast Rust client keeps active
work responsive in the terminal.

## Feature set

- **Local-first runtime** — use llama.cpp, Ollama, OpenAI, Codex, or named custom
  provider profiles without moving Hames's durable state out of your home directory.
- **Terminal-native workflow** — a transcript-first Ratatui UI, classic REPL,
  mouse selection, themes, session pickers, background terminals, and live usage.
- **Explicit safety modes** — Manual, Auto, and Plan behavior is enforced by the
  gateway, with exact one-shot approval for high-risk operations.
- **Durable sessions and goals** — resume or branch conversations, run autonomous
  goals across bounded turns, and keep foreground chat responsive while work continues.
- **Auditable context and memory** — inspect compiled context, token use, immutable
  events, request snapshots, layered memories, corrections, and Markdown/JSONL exports.
- **Extensible tools** — built-in filesystem and shell tools, private web search,
  external MCP servers, portable agents, delegation, plugins, and evolving Skills.

## Install

Hames currently targets Linux and requires:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Rust 1.85 or newer
- Git

Install the latest <code>main</code> build:

~~~bash
curl -fsSL https://raw.githubusercontent.com/saltnpepper97/hames/main/install.sh | bash
~~~

The installer uses locked dependencies, never invokes <code>sudo</code>, and
installs <code>hames</code> to uv's user tool bin directory (normally
<code>~/.local/bin</code>). It keeps the source and Python environment under
<code>~/.local/share/hames/source</code> so the Rust client can launch its matching
gateway. Running the command again performs a fast-forward update.

To review the installer first, or build from your own checkout:

~~~bash
git clone https://github.com/saltnpepper97/hames.git
cd hames
./install.sh
~~~

Set <code>HAMES_REF</code>, <code>HAMES_BIN_DIR</code>, or
<code>HAMES_INSTALL_ROOT</code> to customize a remote installation.

## Quick start

Run the guided setup, verify the environment, and open the TUI:

~~~bash
hames setup
hames doctor
hames
~~~

Setup can configure llama.cpp, Ollama, OpenAI, or Codex. Hames defaults to a local
llama.cpp endpoint at <code>http://127.0.0.1:8080</code>; the gateway starts on
demand and stays available after the client exits.

Useful commands:

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
redirected input selects it automatically.

## How it works

Hames separates presentation from authority:

1. The **Rust client** renders the TUI or classic REPL and sends typed requests.
2. The **Python gateway** owns sessions, providers, context compilation, tools,
   policy decisions, memory, and background work.
3. The **event ledger** records durable, integrity-checked provenance and
   content-addressed payloads.

Both clients talk to the same gateway and session model. Closing the TUI does not
discard an active goal or background terminal; <code>/sessions</code> restores
the durable conversation explicitly.

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
and transcript state. Use <code>/new</code> for a fresh conversation,
<code>/sessions</code> to resume, and <code>/fork</code> to branch after an answer.

<code>/goal &lt;objective&gt;</code> starts independently bounded agent turns under
a durable supervisor. Foreground messages take priority, and explicit
evidence-backed reports advance or complete the goal. Portable
<code>AGENT.md</code> capsules define an agent's role and authority separately
from session settings; delegation creates a bounded child session rather than
silently copying the parent conversation.

### Context, memory, and correction

Every model request passes through a deterministic, budgeted context compiler.
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

The backend diagnostic command is also available as
<code>uv run hamesd doctor</code>. The user-facing executable is built by
<code>crates/hames-repl</code>.

## Project status

The implementation plan is the source of truth for completed and upcoming
milestones:

- [Implementation plan](docs/implementation-plan/README.md)
- [Architecture notes](docs/architecture/)
- [Model evaluations](docs/model-evaluations/)
- [GitHub repository](https://github.com/saltnpepper97/hames)

The original planning archive remains untouched at the repository root.

## License

Hames is available under the [MIT License](LICENSE).
