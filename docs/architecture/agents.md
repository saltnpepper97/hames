# Named agents

An agent is a portable capsule: one `AGENT.md` in a private directory under the
Hames home. Agents share the runtime, ledger, policy gate, and Skill registry.
They do not get a separate installation, and they cannot grant themselves
authority the surrounding harness already forbids.

```text
~/.hames/agents/<id>/AGENT.md
```

`HAMES_HOME` replaces `~/.hames` in tests and isolated installs. Retiring an
agent moves that directory to `agents/.retired/<id>-<timestamp>/`. Sessions and
events keep the old agent id.

There are no built-in agent *types*. `researcher` is an id, not a kind.

## Create

Creating an agent is almost nothing. No tool picker. No skill picker.

```text
hames agent create
hames agent create --name Researcher
hames agent create --name "Code Reviewer" --authority read_only
hames agent create --from ./AGENT.md
```

The id is derived from the display name at create time, then frozen.

New agents receive an opaque `agent-<UUID>` identifier, independently of their name.
Unnamed agents get a readable `hames-N` display name. An imported capsule with an
explicit `id` retains it for portability and compatibility. IDs never change when
names change. The built-in `default` agent retains its reserved identity.

No slug is required. Names are resolved through the registry; ambiguous duplicate
names require a unique ID. Existing slugs and historical names remain compatibility
aliases for in-flight work. Renaming preserves both the ID and capsule directory.

A new capsule is immediately useful: every tool the surrounding policy already
allows, Skills discoverable through the catalog (not all loaded), default
instructions. Specialization is optional YAML on the same file.

## Customize

Every capsule, including `default`, can change its display name and Markdown
instructions. The terminal UI exposes this with `Ctrl+E` in the Agents sheet;
the CLI can update the name or replace the complete capsule:

```text
hames agent edit default --name Navigator
hames agent edit default --from ./AGENT.md
```

The frontmatter `id` and capsule directory are permanent across renames. Replacing `AGENT.md` is
validated and written atomically, so an invalid replacement leaves the current
capsule untouched. The `default` capsule may be customized but never retired or
deleted.

Web clients can also give an agent a small visual identity. The avatar remains
portable capsule metadata rather than browser-local state: five body shapes
(`circle`, `square`, `triangle`, `cloud`, and `hex`), three eye styles (`dots`,
`visor`, and `pill`), and a six-digit hex color. Older capsules may still carry
an unused `face` field; clients ignore it. Clients derive a stable
fallback for older capsules and write the chosen avatar through the same atomic
agent update path.

The Web Agents contextual sidebar lists every real capsule with its rendered
avatar and routes directly to the selected capsule's breakdown. The bare Agents
route selects the first real capsule instead of inserting an overview step. The
main surface edits the display name, instruction body, tool allow/deny sets,
skill allow/deny sets, pinned skills, and avatar through structured gateway
updates. The stable ID is internal and immutable, and every save still atomically
rewrites the existing `AGENT.md` rather than introducing browser-owned agent
state.

## Tools vs Skills vs plugins

Agents may set `delegation.coordinator_only: true` alongside `delegation.allow: true`.
This limits their own exposed and executable tools to inspection, interaction,
task tracking, and delegation. Worker capabilities are still bounded by the parent's
original tool policy and the worker's policy; the local coordinator restriction is
not inherited as a blanket ban on worker edits. This does not define workflow
stages or change the selected chat agent. Each worker uses its configured model.

| | Tools | Skills | Plugins |
|---|---|---|---|
| What | Physical capability (`shell`, `write_file`, later plugin tool names) | Procedure/knowledge for a kind of work | New capabilities added to the harness |
| Default | All tools the harness already permits | All *visible* Skills may be discovered; none fully loaded until `skill_load` | Not installed |
| AGENT.md | May only subtract | May subtract discovery and/or pin a few catalog entries | Deny plugin tool names the same way as core tools |
| Cannot | Grant write if policy or `read_only` forbids it | Grant a tool a Skill declares | Import into the Hames process |

A Skill's `tools:` field is a declaration of what the procedure expects. It is
not a grant. Policy, trust, the capsule, and one-shot approvals still decide
every invocation.

Core tool ids today:

- Interaction: `ask_user` (always available unless explicitly denied).
- Work: `read_file`, `list_dir`, `write_file`, `edit_file`, `shell`, `spawn_agent`.
- Skills: `skill_load`, `skill_author`, `skill_run`, `skill_catalog`, `skill_control`.
- Memory: `memory_search`, `memory_add`, `memory_edit`, `memory_forget`.
- Evolution: `scar_list`, `scar_record`, `scar_control`.

`ask_user` can request a single mutually exclusive choice, a constrained set of
checked choices, or a direct text answer. The runtime pauses the same run until
the active Web, TUI, or classic REPL client records the structured answer.

These are typed controller operations, not generic access to `~/.hames`. Do not
invent names such as `filesystem.write` until they exist as real tool ids. Rule
activation and plugin installation are deliberately absent: those stay in the
authenticated human control plane.

## AGENT.md

Markdown with YAML frontmatter. Unknown keys are rejected. Legacy `provider`
and `model` are inert compatibility fields; execution settings belong to the
session unless an agent `default_model` is used for a new chat, delegation, or plan execution.

```yaml
id: reviewer
name: Code Reviewer
authority: read_only
tools:
  deny:
    - write_file
    - edit_file
    - shell
skills:
  deny:
    - deployment
  pin:
    - testing
delegation:
  allow: true
  allowed_agents:
    - critic
avatar:
  shape: triangle
  eyes: pill
  color: '#0d9488'
```

Reduction-only rules:

- `authority: read_only` is a preset: intersect with `ask_user`, `read_file`, `list_dir`,
  `skill_load`, `memory_search`, `scar_list`, and `skill_catalog`. `standard` is
  the default and is not a grant.
- `tools.allow` empty → all harness tools minus the preset minus `deny`.
- `tools.allow` set → intersection with that list. Still cannot add unknown or
  policy-forbidden tools. `ask_user` remains available as a baseline interaction
  capability unless it appears in `tools.deny`.
- `skills.allow` empty → all Skills already visible for this session/workspace.
- `skills.allow` set → catalog and `skill_load` only those slugs.
- `skills.deny` → never catalog or load.
- `skills.pin` → force those slugs into the catalog prefix as descriptive
  entries. `skill_load` is still required before instructions enter context.
  Pin cannot exceed allow/deny.

## Selection

`/agent` lists capsules. `/agent <id>` uses that capsule for later turns in the
current session and emits `session.agent.changed`. It does not rewrite history.
`/new` starts another chat; `/fork` is the explicit way to carry history.

## Delegation

`spawn_agent` creates a child session with its own capsule, loop limits, and a
task card. The child cannot exceed parent policy or the child's own reductions.
Depth is bounded.


### Autonomous subagents

Agents can delegate without a separate user request. `spawn_agent` defaults to
the current agent when `agent_id` is omitted; an empty `delegation.allowed_agents`
list means the current agent only. An explicit `delegation.allow: false` disables
spawning, and tool allow/deny restrictions still apply. Read-only and Plan agents
can delegate inspection without granting write authority.

Independent `spawn_agent` calls in the same model response run concurrently.
The parent receives each terminal result and remains responsible for integrating
and checking the work. The harness encourages delegation for broad reviews,
many files and separable tasks, while leaving the choice to the model. Assignments
should include the objective, context, constraints, file ownership and expected
result; child sessions receive a task card, not the entire parent conversation.

Children inherit workspace, model and interaction mode, plus a durable snapshot
of the parent's effective tools, skill restrictions and delegation targets. These
permissions can only narrow. Children report approval/input blockers to the parent
and cannot leave background terminals running. Parent cancellation cancels its
children; waiting for them counts against the parent's active time budget. Defaults
are one delegation level, four child runs per parent run and four concurrent child
runs across the gateway. Child results are bounded by the normal tool-result limit.

### Explicit dream maintenance

`/dream` in Web, TUI or REPL requests the same memory, Skill and scar maintenance
that normally waits for idle time. It bypasses that delay, requires a trusted idle
session, and yields to foreground work. The authenticated shared endpoint is
`POST /v1/sessions/{session_id}/dream`; clients receive a `dream_id` and follow the
existing `dream.started`, `dream.completed`, `dream.paused` and `dream.failed` events.

Semantic dream review includes older active facts visible to the session, grouped
by their existing visibility scope. The memory provider can nominate high-confidence
redundancy, supported supersession, explicitly expired facts or transient run clutter.
Age alone never establishes irrelevance. Omitted and uncertain facts remain active;
explicit captures require a supported replacement. Changes retain record provenance
and audited retirement reasons, and apply only while the reviewed records are unchanged.

## Model-separated plan execution

A capsule may specify a default model:

```yaml
default_model:
  provider: codex
  model: gpt-5.6-luna
  reasoning_effort: xhigh
```

`spawn_agent` uses the target capsule's selection when present, including the target
model's context window. Otherwise children retain the existing inheritance behavior.
Legacy top-level `provider`/`model` remain inert. A model cannot override the capsule's
selection through tool arguments. Parent authority, workspace, interaction mode, and
all existing delegation restrictions still apply. Read-only parents cannot create
write-capable children merely by choosing another model.

The authenticated plan execution endpoint accepts optional `agent_id` with `strategy:
keep`. It validates the coordinator and declared worker models/efforts before changing
session settings or approving the plan, then runs the selected coordinator in Auto mode.
It rejects delegated sessions, active work, queued turns, missing plans, untrusted
workspaces, and unavailable selections. Existing execution requests remain unchanged.
An explicitly selected execution agent returns unfinished checklist work to the human as a
resumable needs-attention state, rather than automatically repeating an implementation pass.
The same execute action resumes it while preserving the approved plan, completed checklist
items, execution note, and exact failure.

Select an execution agent in the normal chat, or define a personal command that selects
one. See [user-defined commands](../commands.md). No fixed builder/reviewer pipeline is
required; the selected agent follows its instructions and the approved plan.

The coordinator constructs a small durable dependency graph as it delegates: each child call
can name a stable `stage_id` and completed `depends_on` stages. Hames records every attempt,
attaches prerequisite results as evidence, preserves exact child failures, and returns later
child-chat results to the parent graph. The graph describes execution state; stage order and
verdict interpretation remain agent instructions. The runtime also enforces model routing,
permission inheritance, child limits, cancellation, and rejection of invalid dependencies or
selections. The builder and finisher commit verified, scoped changes unless the user forbids it;
they never push without an explicit request. Existing workspace shell policy still applies.
The reviewer has runtime read-only authority (no shell) and uses the bounded `vcs_inspect` tool
for Git status, history, commits, and diffs.
Selecting an agent with the ordinary picker still preserves session model settings;
`default_model` applies to new chats, delegation, and explicitly selected plan execution. The legacy `execution` key remains readable. Agent settings can clear the default; delegated work then inherits the parent model. Explicit new-chat model selections override the default. Selecting an agent loads its default provider, model, reasoning effort, and context window in both new and existing chats. An agent without a default preserves the current selection. Changing the model in the chat afterward overrides it for that chat until another agent with a default is selected. Editing saved defaults does not itself rewrite existing chats.


### Cancelling a delegated worker

Stopping a child chat publishes a stopping update to its parent immediately.
Once the child has a durable terminal event, the parent receives the result
without waiting for post-run memory or skill maintenance. Cancellation is
reported explicitly and instructs the coordinator to wait for user direction,
not automatically restart or re-delegate the cancelled work. Repeated Stop
requests do not interrupt cancellation cleanup. Stopping a parent still cancels
its child runs.

### Names and stable identity

Web and terminal creation ask for a display name, not a slug or identifier. The
registry assigns the opaque ID. Web routes, sessions, memory scope, and saved
delegation permissions use immutable IDs; display surfaces resolve current names.
The API retains the optional legacy `slug` field for existing clients and capsules.

Old names and slugs stay reserved as aliases, so another agent cannot capture an
in-flight handoff. Name resolution fails explicitly for duplicates; callers can
use the permanent ID to disambiguate. Existing IDs are preserved to keep historical
sessions and permissions valid, without rewriting the event ledger.

Model-facing permitted targets and worker reports use current names. The dispatcher
accepts those references and resolves them to IDs before checking permissions or
creating a child. After a rename, an old reference still resolves to the same agent,
and the returned report identifies it by its current name. There are no hardcoded
role-to-agent mappings in the harness. Saved allowlists canonicalize known targets;
unknown references are preserved to support agents defined later.

The default-model editor discovers models automatically after Web authentication;
Refresh forces a new discovery and does not change the saved default.

### Plan handoff after interrupted execution

A replacement worker receives the current approved plan verbatim, including its
execution note, even when the earlier execution failed. This handoff does not by
itself resume or complete the parent plan. Unapproved or superseded plans are not
substituted. A task referring to an approved plan is rejected before creating a
child if no authoritative plan is available. Delegated contexts exclude episodic
reports of earlier tasks; memory never supplies a missing assignment.
