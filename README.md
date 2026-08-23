# Hames

Hames is a local agent harness under active development. A trusted Python
backend owns the harness and gateway; a Rust REPL is the first client. The current
milestones provide a bounded coding-agent loop while keeping every side effect
behind deterministic policy and durable provenance.

See [`docs/implementation-plan/README.md`](docs/implementation-plan/README.md)
for the milestone plan.

## Status

M5 is implemented. In addition to the local conversation and branching slices, Hames provides
immutable session branching, ancestry-aware replay, typed and integrity-checked
events, irreversible secret redaction, and content-addressed payload blobs. Hames
also has named local-provider profiles, explicit health probes, strict normalized
streams, resumable SSE, and model-specific reasoning levels. Its Python runtime
now executes bounded `read_file`, `list_dir`, `write_file`, `edit_file`, and Bash
tool calls inside an explicitly trusted project or disposable scratch workspace.
Risky operations require exact one-shot approval in the Rust REPL.
Every provider request now passes through a deterministic, budgeted context
compiler. Attributed manifests record selected, compacted, and omitted sources,
while content-addressed request snapshots make the exact normalized input
reconstructable. Ledger-derived REPL inspection and Markdown/JSONL audit exports
show reasoning, answers, tools, policy, context decisions, failures, and usage.
Portable `AGENT.md` capsules now separate an agent's role and authority from a
session's provider, model, reasoning level, workspace, and transcript. The REPL
can list agents with `/agent` and select one for subsequent turns with
`/agent <id>`.
Capsules may narrow tool authority and permit only named child agents. Delegation
creates a separate, bounded child session with an explicit task card and selected
evidence; it never silently copies the parent conversation.

## Quick start

Install the locked Python environment and build the Rust client:

```bash
uv sync --locked
cargo build --locked
```

Run the development binary from the repository:

```bash
target/debug/hames -V
target/debug/hames doctor
target/debug/hames
```

The REPL starts the Python gateway on demand. Exiting the REPL leaves it running;
use `target/debug/hames gateway status` or `target/debug/hames gateway stop` to
inspect or stop it.

Hames defaults to the `llama_cpp` profile at `http://127.0.0.1:8080`. Model
selection prefers the request, then the profile default, then a sole discovered
model; otherwise the REPL asks. A minimal `~/.hames/config.toml` override looks
like:

```toml
[runtime]
default_provider = "llama_cpp" # or "ollama"
max_model_turns_per_user_message = 24
max_tool_calls_per_run = 96
max_active_seconds_per_run = 1800.0
max_delegation_depth = 1
max_child_runs_per_parent_run = 4

[tools]
shell_timeout_seconds = 120.0
shell_max_timeout_seconds = 600.0

[context]
fallback_window_tokens = 32768
output_reserve_tokens = 4096
stable_instruction_limit_tokens = 8192
agent_identity_limit_tokens = 4096
tool_schema_limit_tokens = 8192
retrieved_context_limit_tokens = 0

[providers.llama_cpp]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8080"
model = "qwen3.8-27b"
reasoning_effort = "medium"
supported_reasoning_efforts = ["low", "medium", "xhigh"]
# Optional; otherwise provider discovery and then the 32K fallback are used.
context_window_tokens = 131072

[providers.ollama]
adapter = "ollama"
base_url = "http://127.0.0.1:11434"
```

Profile names are arbitrary, and multiple profiles may use the same adapter.
The legacy profile names infer their adapter when it is omitted.

Every setting can be overridden with a nested environment name such as
`HAMES_PROVIDERS__LLAMA_CPP__MODEL=qwen3.8-27b`. `HAMES_HOME` relocates all
persistent state from its private default at `~/.hames`; it is particularly useful
for isolated tests.

If `~/.hames/config.toml` is from the pre-rewrite `schema_version` format, Hames
recognizes it without rewriting it. The active `llamacpp`/Ollama provider and its
endpoint, model, reasoning effort, and timeout are translated in memory. Other
legacy settings remain preserved but inactive until their corresponding milestone
is implemented; `hames doctor` reports when this compatibility mode is active.

The first time a session uses an exact canonical directory, the REPL asks whether
to trust and remember it. Trust is persisted in `~/.hames/hames.db`; `/trust`
shows the current grant and `/trust revoke` removes it. Trusted roots allow normal
file changes and ordinary shell work without repetitive prompts. Hames still asks
for exact one-shot confirmation for deterministic high-risk signatures and denies
known secrets, credential stores, raw-device operations, and generic access to its
own state. This policy gate is not an operating-system sandbox.

Inside the REPL, `/help` lists session, project, trust, provider, model, usage,
inspection, export, status, and reasoning commands. `/reasoning` reports
model-specific choices; `default`, `off`, `on`, and
advertised named levels can be selected. A trailing `\` continues input on the
next line. Ctrl-C during a model run requests cancellation; Ctrl-D or `/quit`
exits the client.

After a completed answer, `/fork` creates a branch and switches to it. `/agent`
lists portable capsules; `/agent <id>` changes the selected agent for the next
turn while preserving historical attribution. `/new` creates a separate fresh
chat; `/clear` also clears the display before beginning one. `/events`
shows the effective inherited history and `/session` shows the current ancestry.
Tool requests and results are printed concisely. Scratch work requested by the
model lives under `/tmp/hames/runs/<run-id>/<agent-id>/workspace` for that run and
is removed at its terminal event.

`/inspect [run-id]` shows the ledger-derived activity timeline for the latest or
specified run. `/context [context-event-id]` explains its context window, token
estimate, source selection, compaction, omissions, and exact request hash.
`/usage` keeps compiler estimates distinct from provider-reported token counts.
`/export <path> [markdown|jsonl]` writes a private derived audit transcript and
refuses to overwrite an existing file.
The same ledger is scriptable outside the REPL:

```bash
target/debug/hames session list --json
target/debug/hames session show <session-id>
target/debug/hames session fork <session-id> --at <event-id-or-sequence>
target/debug/hames session export <session-id> --format markdown --output audit.md
target/debug/hames event verify <event-id>
target/debug/hames agent list
target/debug/hames agent create reviewer --authority read-only
```

Use `--force` explicitly when replacing an existing noninteractive export. Audit
transcripts are convenient views, not provenance authorities; `hames.db` and its
content-addressed blobs remain the durable source of truth.

## Development

Install locked dependencies and run the current checks with:

```bash
uv sync --locked
cargo build --locked
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The backend diagnostic command is also available as `uv run hamesd doctor`. The
user-facing `hames` executable is built from `crates/hames-repl`.

The extracted and refined implementation plan lives in
[`docs/implementation-plan/`](docs/implementation-plan/). The original planning
archive remains untouched at the repository root.

## License

Hames is available under the MIT License.
