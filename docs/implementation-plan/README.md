# Hames Rewrite — Working Implementation Plan

This directory is the authoritative build plan for Hames. M0 through M9, the
[M10 Ratatui slice](M10-TUI.md), and its pre-web terminal hardening closure are
implemented; the M10 web control surface is the next execution phase. Later milestones preserve the design
inventory but remain subject to refinement as the harness proves its concepts.

Hames is a proper local agent harness built around a small trusted Python kernel,
an append-only event ledger, named agents, layered memory, progressively disclosed
Skills, evidence-backed self-correction, optional isolated plugins, and first-class
observability.

## Product direction

1. **Python owns the harness.** The gateway, agent loop, policy, providers,
   persistence, context construction, tools, memory, and evolution live in Python.
2. **Clients use one gateway.** The Rust REPL, Ratatui interface, web UI, and
   possible desktop application use the same versioned HTTP/SSE boundary.
3. **The REPL came first.** The plain Rust REPL proved the internals. Ratatui now
   presents those same gateway concepts without becoming a second runtime.
4. **Local models come first.** llama.cpp and Ollama are first-class M0 providers.
   Hosted OpenAI API, Codex/ChatGPT, and other services come later.
5. **One historical truth.** Material model input, provider activity, tools,
   approvals, memory, Skills, agents, corrections, and evaluations emit events.
6. **Agents work where invoked.** A session records the current directory as a
   loose work context; Hames does not require a registered project object.
7. **Scratch work is disposable.** Later tool-capable agents may prototype under
   `/tmp/hames/runs/<run-id>/<agent-id>/workspace/`, but user deliverables belong
   in the actual working directory.
8. **Memory has three layers.** Relationship memory covers the user, Semantic
   memory covers known facts, and Episodic memory covers what happened.
9. **Skills are procedural knowledge.** A Skill is not a plugin or permission.
   Hames may autonomously draft, test, independently evaluate, activate, correct,
   quarantine, and roll back Skills. Immutable versions, evidence, policy, pinning,
   and inspection control that evolution without requiring a proposal inbox.
10. **Corrections become tests.** Scars connect failures and corrections to
    evidence, repairs, and regression checks.

## Target stack

- Python 3.12+ with `uv`, FastAPI, uvicorn, Pydantic v2, httpx, and SQLite.
- Stable Rust with Tokio for the `hames` REPL client.
- HTTP commands and Server-Sent Events between every client and the gateway.
- SQLite in WAL mode plus content-addressed files for large immutable payloads.
- pytest, pytest-asyncio, Ruff, Pyright, rustfmt, Clippy, and Cargo tests.
- Linux as the initial host target.
- A heavily customized Ratatui client for the terminal; web and desktop stacks
  are not yet selected.

Do not substitute a large orchestration framework for the runtime. Hames owns its
loop and its invariants.

## Repository and runtime shape

```text
hames/
├── pyproject.toml
├── uv.lock
├── Cargo.toml
├── Cargo.lock
├── src/hames/
├── crates/hames-repl/
├── tests/
└── docs/
```

Persistent state defaults to:

```text
~/.hames/
├── config.toml
├── hames.db
├── agents/
├── blobs/
├── skills/
├── logs/
└── runtime/
```

`HAMES_HOME` overrides this root for tests and deliberate isolated installations.
Directories are created lazily and user files are never silently overwritten.

## Milestone direction

| Milestone | Outcome |
|---|---|
| [M0](M00-BOOTSTRAP.md) | Python gateway, Rust REPL, local providers, core provenance, streaming, cancellation, and diagnostics form a working local conversation slice. |
| [M1](M01-EVENT-LEDGER.md) | The core ledger grows into complete replay, branching, blobs, redaction, and provenance infrastructure. |
| [M2](M02-GATEWAY-PROVIDERS.md) | Provider and gateway contracts deepen without leaking provider wire formats into clients. |
| [M3](M03-AGENT-RUNTIME-REPL.md) | Tools, policy, approvals, scratch workcells, and the complete single-agent loop make Hames useful for real work. |
| [M4](M04-CONTEXT-WEB-INSPECTOR.md) | Deterministic context manifests, exact request snapshots, usage projections, REPL inspection, and audit exports make model calls explainable. |
| [M5](M05-NAMED-AGENTS.md) | Human-readable agent capsules, capability separation, and bounded delegation mature. |
| [M6](M06-LAYERED-MEMORY.md) | Relationship, Semantic, and Episodic memories become scoped, correctable, and observable. |
| [M7](M07-SKILLS.md) | Portable Skills gain autonomous evidence-backed authoring, evaluation, versioning, progressive disclosure, and rollback. |
| [M8](M08-SCARS-EVOLUTION.md) | Scars provide evidence-backed repair routing and regression protection. |
| [M9](M09-PLUGINS.md) | Optional isolated plugins add genuine capabilities without bypassing policy. |
| [M10](M10-WEB-CONTROL.md) | The [Ratatui slice](M10-TUI.md) presents the proven gateway behavior; the web control surface remains next. |
| [M11](M11-HARDENING-RELEASE.md) | Security, recovery, packaging, documentation, and release gates produce v0.1.0. |

## Mandatory execution rule

Read [AGENT-INSTRUCTIONS.md](AGENT-INSTRUCTIONS.md) before implementation. Commit
continuously in coherent, tested slices. A milestone is complete only on a clean,
passing commit and is tagged only after every acceptance criterion passes.
