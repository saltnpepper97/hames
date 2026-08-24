# M0 — Local Harness Foundation and Conversation Slice

## Goal

Prove Hames's process boundaries and historical truth with the smallest useful
vertical slice: a trusted Python backend, persistent local gateway, capable Rust
REPL, editable default agent capsule, llama.cpp and Ollama streaming, and durable
session provenance.

M0 deliberately has no model-callable tools. It proves the foundation before file
mutation, shell execution, memory, named-agent delegation, or autonomous Skills.

## Required user-visible outcome

After locked dependencies are installed:

```bash
hames --version
hames -V
hames doctor
hames
```

The REPL discovers or starts the persistent gateway, opens a session for the
current directory, streams separate provider reasoning and answer content, records
the run, supports cancellation, and can resume the session after reconnecting.

## Architecture

```text
Rust REPL
   │ versioned HTTP commands + SSE
   ▼
Python gateway / trusted harness
   ├── context assembly
   ├── provider normalization
   └── append-only event ledger
        │
        ├── llama.cpp
        └── Ollama
```

The gateway binds only to `127.0.0.1:7411`. An M0 bearer token stored with mode
`0600` under `~/.hames/runtime/` protects operational endpoints.

## Persistent state and configuration

Use `~/.hames` by default and `HAMES_HOME` for deliberate overrides and tests.
Create missing directories, the database, token, and default `AGENT.md` lazily;
never overwrite user-edited files.

Initial `config.toml` sections are `runtime`, `gateway`, `providers.llama_cpp`,
`providers.ollama`, `logging`, and `repl`. Unknown keys are errors. Empty model
selection means discover models and auto-select only when exactly one is present.

The initial capsule is `~/.hames/agents/default/AGENT.md`. It uses strict YAML
frontmatter and Markdown instructions. Every model request records its content
hash.

## Python foundation

- Python 3.12+ `src/` package and internal `hamesd serve` command.
- Pydantic v2 public/persistence schemas.
- Strict configuration with `HAMES_<SECTION>__<FIELD>` overrides.
- Structured logs with redaction and no environment dumps.
- SQLite WAL, foreign keys, transactional numbered migrations, and checksums.
- `hames doctor` data supplied through a stable JSON diagnostic boundary.

## Core ledger

Create append-only `sessions` and `events` tables. Database triggers reject event
updates and deletes. Global sequence numbers support SSE resume.

M0 durable event types:

```text
session.opened
session.closed
user.message
context.compiled
model.requested
model.response.started
assistant.reasoning
assistant.message
model.usage
model.response.completed
model.response.failed
run.cancelled
runtime.error
```

Streaming deltas are transient. Completed or interrupted assembled reasoning and
answer content are durable. Client disconnect is not cancellation.

## Providers and reasoning capabilities

Define one normalized provider interface with model discovery and these stream
events:

```text
response.started
response.reasoning_delta
response.text_delta
response.usage
response.completed
response.failed
```

llama.cpp uses `/v1/models`, model-specific `/props`, and streamed
`/v1/chat/completions`. Inspect `chat_template_caps` rather than guessing model
features by name. When reasoning effort is advertised, pass the selected level
through `chat_template_kwargs` and record it with the request.

Ollama uses `/api/tags`, `/api/ps`, and streamed `/api/chat`. Map `thinking`,
content, usage, failures, and model metadata into the same internal schemas.

All normal tests use a deterministic fake provider. Live local-provider smoke tests
are explicit and optional.

## Gateway API

```text
GET  /v1/health
GET  /v1/providers
POST /v1/sessions
GET  /v1/sessions
GET  /v1/sessions/{id}
GET  /v1/sessions/{id}/events
POST /v1/sessions/{id}/messages
POST /v1/runs/{run-id}/cancel
GET  /v1/events?session_id=<id>
```

Errors use one typed envelope. A health response exposes build and protocol
versions so the Rust client can reject incompatible gateways.

## Rust REPL

The Rust executable is named `hames`. It supports both `--version` and `-V`, plus:

```text
hames doctor
hames gateway start|stop|status
```

The first REPL command set is:

```text
/help
/new
/clear
/sessions
/resume [session-id]
/provider [provider] [model]
/model
/status
/reasoning off|low|medium|xhigh
/quit
```

Support multiline input, persistent history, separate reasoning/answer display,
Ctrl-C cancellation during a run, idle Ctrl-C input clearing, and Ctrl-D exit.
The first REPL starts the gateway when absent; the gateway survives REPL exit.

## Working-directory contract

A new session records the canonical directory in which `hames` was launched.
There is no registered project object. M0 does not expose file tools, so it does not
create scratch space yet.

Later tool-capable runs may request disposable workcells under
`/tmp/hames/runs/<run-id>/<agent-id>/workspace/` for tests and prototypes. Normal
work remains in the user's current directory.

## Tests

- Hames-home isolation, permissions, first-run creation, non-overwrite, config
  validation, version equality, and secret-free logs.
- Fresh/idempotent migrations, append-only triggers, stable ordering, restart
  replay, context manifests, cancellation, and interrupted output.
- Fixture-backed llama.cpp/Ollama discovery, reasoning, text, usage, malformed
  streams, timeouts, and failures.
- Gateway auth, loopback enforcement, SSE, resume, disconnect behavior, daemon
  persistence, stale PID recovery, and protocol mismatch.
- Rust command parsing, multiline input, history, model selection, reasoning
  controls, cancellation, and connection recovery.
- A process-level fake-provider test from Rust REPL through gateway and database.

## Acceptance gate

M0 is complete only when:

- a fresh first run creates private local state without a setup wizard;
- `hames --version`, `hames -V`, and `hames doctor` succeed;
- the REPL starts or reconnects to a persistent gateway;
- llama.cpp and Ollama adapters pass fixture tests and opt-in live smoke checks;
- one session can stream, cancel, restart, resume, and reconstruct durable output;
- provider-exposed reasoning remains distinct and its selected effort is recorded;
- normal tests make no internet or paid-model calls;
- Ruff, Pyright, pytest, rustfmt, Clippy, Cargo tests, and end-to-end tests pass;
- the working tree is clean before annotated tag `m0` is created.
