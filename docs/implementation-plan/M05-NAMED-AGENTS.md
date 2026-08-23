# M05 — Named Agents, Scoped Capabilities, and Child-Agent Delegation

## Goal

Restore and strengthen the defining Hames behavior: the user can create a named agent with a single `AGENT.md`, give it a role, and use it without maintaining a separate harness installation.

Agents share one runtime and one event ledger while receiving explicit capability and future memory scopes.

## Agent storage

```text
~/.hames/agents/<agent-id>/AGENT.md
```

Agent configuration is always stored under `~/.hames`; Hames does not scatter
agent state through user workspaces. A session may attach an agent to its current
working-directory context without relocating the capsule.

## `AGENT.md`

`AGENT.md` is the only required file for an agent.

Use Markdown with YAML frontmatter.

Supported frontmatter:

```yaml
id: researcher
name: Researcher
authority: read_only

tools:
  allow:
    - read_file
    - list_dir
    - shell
  deny:
    - write_file
    - edit_file

delegation:
  allow: true
  allowed_agents:
    - critic
```

Markdown body is the agent’s instructions/purpose.

Unknown frontmatter keys are rejected. Legacy `provider` and `model` fields are
accepted only as inert compatibility input and never select a provider or model.
Those execution settings belong to the session, alongside its workspace and
transcript. Memory and Skills scopes arrive in later milestones.

Agent IDs are stable machine identifiers; display names may change.

## Agent registry

Implement:

```bash
hames agent list
hames agent show <id>
hames agent create <id>
hames agent validate <id>
hames agent delete <id>
```

`create` writes a valid minimal `AGENT.md`, not a database-only record.

Deleting/retiring an agent must not delete old sessions/events. Historical events continue referencing retired agent ID.

## Agent inheritance

Define deterministic precedence:

```text
Hames core constraints
→ global policy
→ project policy
→ AGENT.md
→ per-run user selection
```

An agent may restrict its tool set below global/project policy but cannot broaden policy permissions.

Provider, model, and reasoning settings are session-owned. Selecting an agent
changes only the capsule used for future turns in the current session and emits a
durable `session.agent.changed` event. It is rejected while a run is active, and
never rewrites historical event attribution. `/new` begins a separate chat;
`/clear` clears the display and begins one. `/fork` remains the explicit way to
carry history into a branch before selecting another agent.

## Context integration

`agent.instructions` becomes a stable-prefix context source.

Context manifest shows:

- agent ID;
- exact `AGENT.md` content hash;
- estimated tokens;
- source path;
- project/global origin.

A changed `AGENT.md` affects future model requests but does not rewrite history.

## Agent selection

REPL additions:

```text
/agent
/agent <id>
```

Gateway session creation accepts `agent_id`.

Current agent is explicit in session and all model/tool events.

## Child-agent delegation

Add core tool:

```text
spawn_agent
```

Inputs:

```text
agent_id
task
project_scope
requested_result_format optional
```

Rules:

- child creates a delegation session linked to the parent event (not a replaying branch);
- child has its own loop limits;
- child receives only allowed context;
- child cannot exceed parent policy/capability authority;
- delegation depth is bounded;
- child completion returns one structured result to parent;
- no autonomous agent-to-agent conversation loop;
- parent can cancel child;
- child failures return structured results.

Default maximum depth for v0.1 should be 1 or 2.

## Child context

A child receives:

- core system;
- its own `AGENT.md`;
- task from parent;
- project instructions if scoped;
- explicitly selected relevant parent context summary;
- permitted tool schemas.

Do not clone entire parent conversation by default.

Parent delegation event records what context was passed.

## Per-agent usage accounting

Extend usage projection:

- model tokens by agent;
- model calls by agent;
- tool calls/duration by agent;
- child wall time;
- errors;
- session count.

The gateway exposes a ledger-derived per-agent usage projection. Web Inspector
labels and visualization arrive with the later web milestone.

## Tests

Cover:

- valid/invalid `AGENT.md`;
- project-local agent trust requirement;
- global vs project agent precedence;
- `AGENT.md` hash in manifest;
- denied tool absent from agent schema;
- agent cannot broaden policy;
- session starts with selected agent;
- child branch ancestry;
- child context does not inherit unrelated parent content;
- max delegation depth;
- child cancellation;
- child tool restrictions;
- child result returned to parent;
- usage attributed separately;
- retired agent history remains readable.

## Manual smoke test

Create:

- `coder` agent with write/shell access;
- `reviewer` agent read-only.

Ask `coder` to make a small edit and delegate review to `reviewer`.

Verify:

- reviewer cannot edit;
- reviewer branch visible in inspector;
- parent receives reviewer result;
- token/tool usage separated by agent;
- both agents use same project/session provenance system.

## Commit expectations

Suggested slices:

1. `AGENT.md` parser/registry;
2. agent context/tool/policy selection;
3. REPL/gateway selection;
4. delegation branch runtime;
5. per-agent inspector/accounting.

## Acceptance gate

M05 is complete when named agents are human-editable, scoped, attributable, and capable of bounded delegation without separate installations or uncontrolled shared context.

Finish clean and tag `m05`.
