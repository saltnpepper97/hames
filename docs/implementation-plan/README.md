# Hames Rewrite — Implementation Plan to v0.1.0

This directory is the authoritative build plan for the Hames rewrite.

Hames is a personal agent harness built around a small trusted kernel, an append-only event ledger, named agents, layered memory, progressively disclosed skills, evidence-backed self-correction, isolated plugins, and first-class observability.

The plan deliberately starts with a **bare REPL**, not a rich TUI or polished web application. The **Web Inspector** arrives early because it is a development instrument: Hames should be inspectable while Hames itself is being built. A rich terminal UI is explicitly **not** part of v0.1.0; the REPL is the terminal client for this release.

## Product principles

1. **Simple kernel, powerful composition.** The trusted core owns only invariants that must remain understandable: events, sessions, context compilation, model execution, capability registration, and policy enforcement.
2. **Everything important is reconstructable.** If something materially influenced a model call, Hames must be able to explain where it came from.
3. **One source of historical truth.** Sessions, model calls, tool calls, approvals, memory retrieval, skill use, agents, corrections, and evaluations all emit into the event ledger.
4. **Memory is layered, scoped, and provenance-backed.** Hames does not dump conversation fragments into a vector database and call that memory.
5. **Skills are procedural memory.** They are not plugins and they are not policy.
6. **The model may propose durable improvements; promotion remains controlled.** Hames can autonomously identify and draft skills, memory corrections, and other repairs, but durable authority changes are versioned, tested, inspectable, and promoted through policy.
7. **Corrections become tests.** The signature feature of Hames is **Scars**: recurring or important failures become durable repair records tied to evidence and regression checks.
8. **Plugins cannot route around the policy gate.** Third-party or agent-authored plugin code runs outside the trusted process.
9. **Observability is a product feature.** Token usage, context composition, model calls, tools, memory retrieval, branches, failures, and evolution are visible in the same system.
10. **Every milestone is shippable.** No milestone is considered complete because a skeleton exists. Its behavior, tests, migrations, failure handling, documentation, and integration must all pass its gate.

## Target stack

The plan assumes:

- **Python 3.12+** for the Hames controller/runtime.
- `uv` for project and environment management.
- `FastAPI` + `uvicorn` for the local gateway.
- HTTP commands plus **Server-Sent Events (SSE)** for event streaming.
- SQLite in WAL mode for durable structured state.
- FTS5 for lexical retrieval.
- Content-addressed files for large immutable payloads.
- Pydantic v2 for public and persistence boundary schemas.
- `httpx` for model/provider transport.
- `pytest`, `pytest-asyncio`, and temporary isolated state roots for tests.
- **TypeScript + React + Vite** for the web interface.
- Linux as the v0.1.0 host target.
- `bubblewrap` for isolated untrusted/plugin workers in the plugin milestone.

Do not substitute a large orchestration framework for the runtime. Hames should own its loop.

## Repository shape at v0.1.0

```text
hames/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── hames/
│       ├── core/
│       ├── gateway/
│       ├── providers/
│       ├── tools/
│       ├── policy/
│       ├── agents/
│       ├── memory/
│       ├── skills/
│       ├── evolution/
│       ├── plugins/
│       ├── cli/
│       └── web/
├── web/
│   ├── package.json
│   └── src/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
└── docs/
```

Runtime state follows XDG:

```text
$XDG_CONFIG_HOME/hames/
├── config.toml
├── agents/
├── skills/
├── plugins/
└── projects/

$XDG_STATE_HOME/hames/
├── hames.db
├── blobs/
├── plugin-workers/
├── proposals/
└── logs/

$XDG_CACHE_HOME/hames/
└── ...
```

Project-local configuration lives in `.hames/` only when a project needs local instructions, policy, agents, or skills.

## Milestone order

| Milestone | Outcome |
|---|---|
| [M00](M00-BOOTSTRAP.md) | Repository, development contract, configuration, XDG state, migrations, tests, and immediate Git discipline exist. |
| [M01](M01-EVENT-LEDGER.md) | Hames has an append-only event ledger, session tree, blob store, replay API, and durable provenance base. |
| [M02](M02-GATEWAY-PROVIDERS.md) | Local gateway, SSE transport, model provider abstraction, real streaming, cancellation, usage, and provider error normalization work. |
| [M03](M03-AGENT-RUNTIME-REPL.md) | A usable single-agent Hames exists with core tools, policy enforcement, approvals, and a bare streaming REPL. |
| [M04](M04-CONTEXT-WEB-INSPECTOR.md) | Context compilation is explicit and inspectable; the first read-only web inspector visualizes real runs and usage. |
| [M05](M05-NAMED-AGENTS.md) | Named `AGENT.md` agents, scoped capabilities, child-agent branches, and per-agent accounting are complete. |
| [M06](M06-LAYERED-MEMORY.md) | Semantic, relationship, operational, and episodic memory are durable, scoped, searchable, provenance-backed, and observable. |
| [M07](M07-SKILLS.md) | Portable skills, progressive disclosure, usage tracking, versioning, autonomous skill proposals, testing, and promotion are complete. |
| [M08](M08-SCARS-EVOLUTION.md) | Hames detects corrections/failures, creates Scars, routes repairs, evaluates them, and guards against regressions. |
| [M09](M09-PLUGINS.md) | A narrow isolated plugin system adds capabilities without giving plugins unrestricted access to the controller. |
| [M10](M10-WEB-CONTROL.md) | The web inspector becomes a complete Hames control surface for chat, agents, memory, skills, Scars, plugins, approvals, and settings. |
| [M11](M11-HARDENING-RELEASE.md) | Security, backup/export, migrations, packaging, documentation, end-to-end tests, and release gates produce Hames v0.1.0. |

## Mandatory execution rule

Read [AGENT-INSTRUCTIONS.md](AGENT-INSTRUCTIONS.md) before implementation. It is part of the plan, not optional guidance.

The implementation agent must:

- initialize Git before implementation code exists;
- commit continuously in coherent slices;
- run the relevant test suite before each commit;
- never hide a broken state inside a later “fix everything” commit;
- leave each milestone on a clean, passing commit;
- tag a milestone only after every acceptance criterion in that file passes.

## Definition of v0.1.0

Hames v0.1.0 is complete when a user can:

1. Install and initialize Hames on Linux.
2. Configure an OpenAI-compatible model endpoint or Anthropic provider.
3. Start Hames and chat through the bare REPL or web UI.
4. Allow Hames to read, edit, and run commands within a trusted project under explicit policy.
5. Inspect exactly what happened during a run, including branch structure, tools, policy decisions, provider usage, and approximate context composition.
6. Create named agents with a single `AGENT.md`, assign scoped tools/policy/memory, and delegate bounded child work.
7. Use layered shared/private memory with provenance and corrections.
8. Use skills loaded progressively rather than permanently occupying context.
9. Let Hames detect repeatable workflows and draft skill proposals automatically.
10. Mark or detect meaningful corrections, create Scars, propose repairs, evaluate them, and track recurrence.
11. Install isolated plugins with explicit capabilities, while agent-authored plugins remain proposals until approved.
12. Operate the same system from the Web Control interface.
13. Export, back up, restore, and migrate state without losing provenance.
14. Run the complete test suite from a clean checkout with no external paid model dependency.

Anything not required by those outcomes is not allowed to delay v0.1.0.
