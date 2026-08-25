# Hames

Hames is a local agent harness under active development. A trusted Python
backend owns the harness and gateway; customized Ratatui and classic Rust clients
share that boundary. The current
milestones provide a bounded coding-agent loop while keeping every side effect
behind deterministic policy and durable provenance.

See [`docs/implementation-plan/README.md`](docs/implementation-plan/README.md)
for the milestone plan.

## Status

M0 through M9 and the M10 Ratatui slice are implemented. In addition to the local
conversation and branching slices, Hames provides
immutable session branching, ancestry-aware replay, typed and integrity-checked
events, irreversible secret redaction, and content-addressed payload blobs. Hames
also has named local-provider profiles, explicit health probes, strict normalized
streams, resumable SSE, and model-specific reasoning levels. Its Python runtime
now executes bounded `read_file`, `list_dir`, `write_file`, `edit_file`, and Bash
tool calls inside an explicitly trusted project or disposable scratch workspace.
Agents can pause their current tool turn with `ask_user`; the clients present up
to three suggested answers plus an always-available one-line custom response,
then return the answer to that same run.
Risky operations require exact one-shot approval in the Rust REPL.
Execution mode is session-owned and gateway-enforced: `/mode manual` confirms
state changes with allow-once/allow-for-session/deny choices, `/mode auto`
confirms only dangerous work, and `/mode plan` permits inspection and tests
without writes.
Durable autonomous goals can span multiple independently bounded runs without
losing foreground responsiveness. `/goal <objective>` starts one; explicit,
evidence-backed `goal_report` results advance or complete it, and a deterministic
three-step stall guard blocks repeated no-progress loops. Foreground chat yields
the goal and runs first, while client exit leaves goal supervision in the gateway.
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
Relationship, Semantic, and Episodic memory now provide durable continuity without
a required project registry. Active records are visibility-filtered and ranked
before entering the context budget, and every selected record remains attributable
in `/context`. Background extraction proposes only bounded durable facts from the
settled turn; notable tool runs also receive deterministic episodic projections.
Users can explicitly capture, review, correct, promote, or forget memories from the
REPL. The model-facing runtime now exposes typed `memory_search`, `memory_add`,
`memory_edit`, and `memory_forget` tools as well: corrections create immutable
superseding records, while forgetting permanently deletes the selected memory
and its retrieval metadata. Manual mode asks before deletion; Auto mode proceeds
without another prompt only when the current user message explicitly requests
memory maintenance.
Procedural memory is now autonomous. Hames records settled workflow signatures,
detects repeated successful multi-step work, drafts and independently evaluates
immutable scoped Skills, and activates passing versions without a proposal inbox.
Only compact relevant catalog entries enter normal context; the model must load a
Skill before its full procedure is supplied. Declared scripts self-test and run in
an offline Bubblewrap sandbox. Pinning, archive, history, quarantine, rollback,
jobs, evidence, and typed ledger events keep evolution inspectable and reversible.
Chat runs can inspect the active catalog and request pin, archive, restore, or
rollback through typed Skill controls. Archive and rollback require approval;
read-only agents receive catalog access without mutation authority.

Self-correction is now evidence-backed. Corrections (`/correct` in the REPL),
conversational correction language, repeated failure signatures, and failing Skill
versions open Scars: durable, inspectable records linking evidence to expected
behavior. A repair router picks the weakest sufficient layer — memory records for
user corrections, the Skill pipeline for procedures, approval-gated context and
policy rules otherwise — evaluates candidates with deterministic replay checks and
optional budgeted model evaluation, then guards future runs until the fix heals or
regresses. Approved context rules are enforced at compile time and approved policy
rules can only add protection.
The chat runtime can list Scars, record and open an explicit correction, and open
or dismiss a visible Scar. Dismissal preserves its audit lifecycle; explicit
`scar_control delete` permanently removes an erroneous or unwanted scar and its
repair projections. Both destructive controls require approval. Rule activation and plugin
installation remain authenticated human control-plane operations rather than
model tools.

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

On the first interactive start, Hames offers to provision private web search.
`target/debug/hames setup` opens that choice directly. Enabling it uses an
already-working rootless Podman or Docker installation to create a digest-pinned,
loopback-only SearXNG container; Hames never installs a container runtime or uses
`sudo`. If no runtime is available, the gateway still starts and reports search
as degraded. Inspect or control it with `target/debug/hames search
status|start|stop|restart|update` and `target/debug/hames doctor`.

The gateway connects to Hames's bundled search server over private stdio using
modern MCP `2026-07-28`. Models receive `web_search` for structured results and
`web_fetch` for bounded readable source text. Both are read-only, visible under
Explore, available in Plan mode, and guarded against local/private fetch targets.
See [`docs/architecture/web-search.md`](docs/architecture/web-search.md).

In a terminal, `hames` opens the transcript-first Ratatui interface. Use
`target/debug/hames tui` to request it explicitly, or `target/debug/hames repl`
and `target/debug/hames --repl` for the classic line-oriented client. Piped and
redirected invocations retain the classic interface automatically.

Both clients start the Python gateway on demand. Exiting either client leaves it
running; use `target/debug/hames gateway status` or
`target/debug/hames gateway stop` to inspect or stop it. A terminal exit also
prints the exact `/resume <session-id>` command needed to continue the
conversation.
Empty sessions are discarded on exit and omitted from `/sessions`, so opening
the client without sending anything does not create resumable clutter.

The TUI keeps the transcript, activity continuity, and an expanding composer on
one screen. The composer grows through eight visible content rows and then
scrolls. Enter sends; Alt+Enter, Shift+Enter, or Ctrl+J inserts a new line.
Agent questions appear in a lower tray above the composer. Use Up/Down and Enter
or click an offered answer to choose it. Press `N` (or click `N add note`) to
attach a one-line note to that answer. `Write something else` is a separate
fourth radio choice that opens a one-line custom answer.
An empty new session opens with a compact, proportionally sampled ASCII mark
derived from `crates/hames-repl/assets/welcome-ascii.txt`. It stays subdued for
about twelve seconds between slow neutral-gray sheen passes and disappears
permanently as soon as composer typing or paste input begins.
Shift+Tab cycles
Manual, Auto, and Plan modes, with a distinct composer border for each. Ctrl+K
opens the command palette, Page Up/Page Down and the mouse wheel scroll the
transcript, and Enter or Space expands a selected Thought. Large pastes become
compact capsules without losing their exact durable content. Recent sessions for
the current directory are offered when the TUI opens. Scrollbar tracks and solid
thumbs support anchored dragging, so grabbing a thumb never teleports the view.
`/themes` switches between the default
custom Hames palette and terminal-native colors. The choice is a global client
preference in `~/.hames/ui.toml`, loaded before the first frame and shared by all
sessions.

The bottom row keeps a right-aligned `[connected]` badge. While idle it shows the
shortcut hints on the left; during work those hints become a compact animated
gray rule with a restrained white sheen, elapsed time, and `Esc interrupt`.
Interrupted reasoning settles as a completed `Thought`, followed by a separate
`Turn interrupted` transcript status.

`/goal <objective>` starts durable autonomous work. Bare `/goal` opens its
supervisor view; `/goal pause`, `/goal resume`, and `/goal cancel` provide direct
controls. `Esc` pauses an active goal step, while ordinary runs retain
`Esc interrupt`. Closing the TUI leaves a running goal with the gateway and prints
the session resume handoff.

Transcript and modal text support native mouse-drag selection inside the TUI.
Releasing the mouse automatically copies the highlighted text through the
terminal clipboard protocol and briefly confirms the copy above the composer.
Clicking or selecting inside a modal never dismisses it; Esc closes deliberately.

Slash commands and the Ctrl+K command palette use an open tray with quiet rules
above and below the choices rather than a traditional bordered box. The active
row uses a subdued gray background; green is reserved for query-matching letters.
The tray shows literal commands: `/new` starts a session while retaining the
current conversation in `/sessions`; `/clear` retires the current conversation
and starts fresh. Bare `/resume` aliases the `/sessions` picker while
`/resume <session-id>` continues directly. `/status` opens session continuity,
and `/gateway` shows service health and active work. Every above-composer picker
shares the palette's open top-and-bottom rules, with its name inset into the top
rule. Centered dialogs use quiet, square corners rather than rounded popup frames.
Their outlines use the lighter neutral gray shared with composer input instead
of a green accent.
While a picker is open, the bottom-left status bar switches to its navigation
and selection shortcuts. In `/sessions`, Ctrl+D arms removal with a red row and
an explicit warning; pressing Ctrl+D again retires that conversation from the
resumable list. Moving to another row or closing the picker cancels the warning.
The refreshed picker stays open after removal, including when Hames replaces the
currently active session, so another conversation can be resumed or removed. A
fresh empty replacement is omitted from that refreshed picker, making the retired
row visibly disappear instead of replacing it with a lookalike entry.
`/new` remains a client-side control during active work and opens the new session
without probing or waiting on the busy model provider. The previous run continues
in its original resumable session.

`/memory` is an active-memory browser: arrow keys wrap through selectable records,
the focused record expands its full summary and value, and Page Up/Page Down
scroll long details. Ctrl+D uses the same two-press, red-row confirmation as
session removal, then permanently deletes the memory without closing the browser.
Deleted memories never appear there.

`/scars` uses the same focused-browser pattern. Each selected Scar expands its
status, severity, scope, detection source, failure signature, diagnosis, expected
behavior, repair reference, evidence count, guard successes, regressions, and
last trigger. Press E to edit the human-facing title, severity, problem, and
expected behavior; Tab moves between fields and Ctrl+S saves. Evidence, trigger
signatures, and repair history remain immutable. Ctrl+D requires a second press
before permanently deleting the Scar and keeps the browser open afterward.

The top-right header shows the durable session title beside the current activity
instead of repeating model controls. The model can set or revise that title with
the safe `session_title_set` tool; `/title <name>` provides the equivalent direct
control. `/model` lists only reachable configured providers, then asks for the
model's supported reasoning setting on a second sheet before applying the change.
Boolean reasoning models get `on`/`off`; models with a declared effort scale get
their named levels plus `default` and `off`.

Hames defaults to the `llama_cpp` profile at `http://127.0.0.1:8080`. Model
selection prefers the request, then the profile default, then a sole discovered
model; otherwise the REPL asks. A minimal `~/.hames/config.toml` override looks
like:

```toml
[runtime]
default_provider = "llama_cpp" # or "ollama"
max_model_turns_per_user_message = 100
max_tool_calls_per_run = 99
max_active_seconds_per_run = 1800.0
max_delegation_depth = 1
max_child_runs_per_parent_run = 4

[tools]
shell_timeout_seconds = 120.0
shell_max_timeout_seconds = 600.0

[web]
search_limit = 8
safe_search = "moderate" # off, moderate, or strict
search_timeout_seconds = 20.0
fetch_timeout_seconds = 15.0
fetch_max_bytes = 2097152
fetch_max_chars = 30000

[context]
fallback_window_tokens = 32768
output_reserve_tokens = 16384
stable_instruction_limit_tokens = 8192
agent_identity_limit_tokens = 4096
tool_schema_limit_tokens = 8192
retrieved_context_limit_tokens = 2048

[memory]
enabled = true
automatic_extraction = true
# Blank values reuse the active session provider, model, and reasoning effort.
provider = ""
model = ""
reasoning_effort = ""
max_proposals_per_pass = 4
max_retrieved_records = 8
max_extraction_retries = 2

[skills]
enabled = true
autonomous_authoring = true
auto_activate = true
# Blank values reuse the active session provider, model, and reasoning effort.
provider = ""
model = ""
reasoning_effort = ""
repetition_threshold = 2
task_similarity_threshold = 0.65
evaluator_pass_score = 0.80
max_background_model_calls_per_day = 8
max_job_retries = 2
max_catalog_entries = 12
catalog_budget_tokens = 2048
loaded_budget_tokens = 8192
script_timeout_seconds = 60.0

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
own state. Core shell work uses this policy gate rather than an operating-system
sandbox; self-authored Skill scripts use the narrower Bubblewrap boundary described
below.

Inside the REPL, `/help` lists session, project, trust, provider, model, usage,
inspection, export, status, and reasoning commands. `/reasoning` reports
model-specific choices; `default`, `off`, `on`, and
advertised named levels can be selected. A trailing `\` continues input on the
next line. Ctrl-C during a model run requests cancellation; Ctrl-D or `/quit`
exits the client.

After a completed answer, `/fork` creates a branch and switches to it. `/agent`
lists portable capsules; `/agent <id>` changes the selected agent for the next
turn while preserving historical attribution. `/new` creates a separate fresh
chat and retains the old one in `/sessions`; `/clear` retires the old chat from
the resumable list before beginning a new one. `/events`
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

Memory extraction normally runs after each settled turn. `/remember` marks the
next ordinary message as an explicit durable capture; `/remember <fact>` queues a
capture without sending another chat turn. `/memory` lists active visible records,
while `/memory search`, `show`, `proposals`, `accept`, `reject`, `forget`,
`promote`, `status`, and `retry` provide the review workflow shown by `/help`.
Explicit captures are still structured by the configured model rather than stored
as an untyped transcript fragment. A failed extraction never blocks the completed
chat turn and remains visible as a retryable memory job.

Skill evolution also runs after a settled turn and never delays its completed
answer. `/skills` lists active visible procedures; `search`, `show`, and `history`
inspect them, while `jobs` shows autonomous work. `author` and `correct` enqueue the
same autonomous pipeline explicitly. `pin`, `unpin`, `archive`, `restore`, and
`rollback` are overrides rather than an approval workflow. Script helpers run with
networking disabled, no real home, a read-only project, and only disposable run
scratch writable. Hames also bundles the composable `web-app-debugging`,
`visual-verification`, and `linux-gui-testing` procedures through the same catalog
and progressive-load path. Portable packages are auto-discovered from project
`.agents/skills` and global `~/.agents/skills`; model-only Skills remain visible
in `/skills`, while Skills opting into Hames user invocation also appear as
`/slug` commands. See
[`docs/architecture/skills.md`](docs/architecture/skills.md).

Corrections become tests. `/correct <explanation>` records an explicit correction
linked to the offending event; `/evolution` lists Scars by state (`open`,
`guarded`, `healed`, `regressed`) and shows full lineage — evidence timeline,
repair candidates, evaluations, guard counts, and why the Scar triggered. Scar
events stream live during runs. See
[`docs/architecture/evolution.md`](docs/architecture/evolution.md).

The same ledger is scriptable outside the REPL:

```bash
target/debug/hames session list --json
target/debug/hames session show <session-id>
target/debug/hames session fork <session-id> --at <event-id-or-sequence>
target/debug/hames session export <session-id> --format markdown --output audit.md
target/debug/hames event verify <event-id>
target/debug/hames agent list
target/debug/hames agent create reviewer --authority read-only
target/debug/hames skill list <session-id>
target/debug/hames skill show <session-id> <skill-id>
target/debug/hames skill jobs <session-id>
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
