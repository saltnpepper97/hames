# M07 — Autonomous Skills and Progressive Disclosure

## Outcome

M07 is implemented on gateway protocol 9 and SQLite migration 8.

Hames now owns procedural memory. It can observe a repeated successful workflow,
draft a reusable Skill with the configured model, validate it, independently
evaluate it, and activate it without asking the user to curate a proposal inbox.
The same machinery can patch an existing Skill. Activation remains safe and
inspectable through immutable versions, evidence, deterministic policy, pinning,
quarantine, and automatic rollback.

This is intentionally different from the earlier proposal-and-approval design.
Hames is meant to be self-sufficient: inspection and override are user controls,
not mandatory steps in its learning loop.

## Persistent shape

All durable Skill state remains below `~/.hames` (or `HAMES_HOME`):

```text
skills/
└── packages/
    └── <skill-id>/
        └── <version>-<content-hash-prefix>/
            ├── SKILL.md
            ├── references/ optional
            └── scripts/ optional
```

The database stores Skill identities, immutable versions, evidence, evaluations,
background jobs, workflow signatures, and usage outcomes. Package directories are
private and content-addressed. Every read verifies the complete package hash, so
out-of-band edits are detected rather than silently trusted.

`SKILL.md` is YAML frontmatter plus Markdown. Metadata includes:

```yaml
id: investigate-rust-regression
name: Investigate Rust Regression
description: Reproduce, narrow, repair, and verify a Rust regression.
version: 2
scope: workspace
tools: [read_file, edit_file, shell]
triggers: [rust regression, failing cargo test]
requires: [project_trusted]
scripts: []
```

Scopes are `global`, `workspace`, and `agent:<id>`. A global ID cannot overlap a
narrower visible ID. Workspace and agent Skills remain unavailable outside their
recorded boundary.

## Autonomous lifecycle

After every settled run Hames records a deterministic workflow signature from:

- the causative user task;
- the ordered tool-result sequence;
- agent and workspace identity;
- the terminal run outcome.

A completed workflow with at least two tool calls becomes authoring evidence when
the configured number of materially similar successful workflows is reached. The
default threshold is two. `skill_author` can also request authoring or correction
from inside the normal agent loop. A successful turn that explicitly corrects a
loaded Skill queues a grounded patch rather than merely remembering the feedback.
A failed declared Skill script immediately
quarantines its version, restores the newest safe predecessor when one exists, and
queues a correction.

The background job is recoverable and follows this pipeline:

1. select the session provider/model unless the Skills configuration overrides it;
2. give the drafter only the goal, exact evidence subset, scope, and current Skill
   when patching;
3. reject undeclared tools, unsafe paths, oversized packages, invisible evidence,
   and tools not grounded in the observed workflow;
4. syntax-check declared scripts and execute their required `--self-test` in an
   offline Bubblewrap sandbox;
5. send the candidate and deterministic report to an independent evaluator call;
6. activate automatically only when validation passes and the evaluation reaches
   the configured score;
7. otherwise preserve the immutable rejected candidate and its report.

Foreground agent requests have priority over background memory extraction and
Skill authoring/evaluation on providers that serialize model access. A daily
background model-call budget prevents unbounded autonomous work. Exhausted jobs
remain visible as `budget_wait` and can be retried after the budget changes or a
new day begins.

There is no `approve proposal` command and no silent authority expansion. A model
may improve procedure, but the runtime still owns tools, policy, trust, and every
side effect.

## Versions and rollback

Active versions are immutable. A patch records its base version. Activation uses
compare-and-swap semantics and rejects a candidate if another correction became
active first. Replaced versions become `superseded` rather than being deleted.

Statuses are:

```text
draft
verified
active
stale
archived
rejected
quarantined
superseded
```

Pinning prevents autonomous activation of a different version. Archive is
reversible. Rollback quarantines the current version and reactivates the newest
eligible predecessor. These controls are overrides for autonomous behavior, not a
required review queue.

## Progressive disclosure

At the beginning of a run, the context compiler receives only compact relevant
catalog records containing identity, description, triggers, tools, scripts, scope,
hash, and relevance score. Full instructions enter context only after the model
calls `skill_load`.

Loaded instructions are explicitly subordinate to the core contract and current
policy. A script is not executed merely because its Skill was loaded; the model
must call `skill_run` with a declared script ID.

Context manifests separately attribute `skill_catalog` and loaded `skill` sources.
The ledger emits `skill.catalogued`, `skill.loaded`, and `skill.executed`, and the
usage projection records catalog, load, execution, settled outcome, tool count,
correction, and last use.

## Script containment

Self-authored executable helpers are allowed, but they do not run as ordinary host
processes. Bubblewrap creates a fresh namespace with:

- networking disabled;
- no real home directory;
- the Skill package read-only at `/skill`;
- the project read-only at `/project` during normal execution;
- only the run scratch directory writable at `/workspace`;
- bounded time and output.

Validation self-tests receive no project mount. If Bubblewrap is unavailable,
script validation or execution is rejected rather than falling back to unsafe host
execution. User deliverables still use normal policy-controlled core tools in the
actual project workspace.

## Interfaces

The Rust REPL provides:

```text
/skills
/skills search <query>
/skills show <id>
/skills history <id>
/skills jobs
/skills author <goal>
/skills correct <id> <change>
/skills retry <job-id>
/skills pin|unpin|archive|restore|rollback <id>
```

The noninteractive Rust CLI mirrors these operations under `hames skill`; a
session ID is explicit because visibility depends on workspace and agent scope.
The protocol exposes the same session-scoped registry, history, jobs, authoring,
retry, and lifecycle-control endpoints for future Ratatui and web clients.

## Provenance events

M07 adds typed events for workflow observation, authoring requests and triggers,
recoverable jobs, drafts, validation, evaluation, activation, supersession,
rejection, quarantine, rollback, catalog/load/execute usage, outcomes, and user
overrides. Model authoring and evaluation calls use the ordinary normalized model
request/response/usage events with an explicit maintenance purpose.

## Verification

Automated coverage includes migration from M6, parsing, scope visibility, package
integrity, stale activation rejection, version history, pin/archive/rollback,
repeated-workflow detection, autonomous fake-provider drafting, independent
rejection, provider priority, gateway lifecycle endpoints, protocol 9, REPL build,
and the existing end-to-end harness suite.

The milestone is complete when the full offline gate passes, a clean isolated
live llama.cpp run proves autonomous creation and later progressive loading, the
result is documented, and the repository is tagged `m07`.
