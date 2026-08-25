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

| Request | Id | Name |
|---|---|---|
| `--name Researcher` | `researcher` | `Researcher` |
| `--name "Code Reviewer"` | `code-reviewer` | `Code Reviewer` |
| `--name Researcher` when `researcher` exists | `researcher-2` | `Researcher` |
| no name | `hames-1` | `hames-1` |
| `--from` with frontmatter `id` / `name` | honor `id` if present, else slug `name` | honor `name` if present, else the id |

Slug: lowercase `[a-z][a-z0-9-]{0,62}`, spaces and punctuation become `-`. Empty
slugs fall back to `hames-N`. Changing `name:` later does not rename the
directory; ledger rows already point at the id.

A new capsule is immediately useful: every tool the surrounding policy already
allows, Skills discoverable through the catalog (not all loaded), default
instructions. Specialization is optional YAML on the same file.

## Tools vs Skills vs plugins

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

These are typed controller operations, not generic access to `~/.hames`. Do not
invent names such as `filesystem.write` until they exist as real tool ids. Rule
activation and plugin installation are deliberately absent: those stay in the
authenticated human control plane.

## AGENT.md

Markdown with YAML frontmatter. Unknown keys are rejected. Legacy `provider`
and `model` are inert compatibility fields; execution settings belong to the
session.

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
