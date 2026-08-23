# M07 — Skills, Progressive Disclosure, Autonomous Skill Proposals, and Curation

## Goal

Implement procedural memory.

A skill captures a reusable method the agent should follow. Hames can recognize when a repeated workflow, correction, or recovered failure is worth turning into a skill and autonomously draft/test a proposal. Activation remains versioned and controlled.

## Skill package

A skill is a directory:

```text
skills/<skill-id>/
├── SKILL.md
├── references/ optional
├── scripts/ optional
└── tests/ optional
```

`SKILL.md` uses YAML frontmatter plus Markdown.

Required metadata:

```yaml
id: investigate-rust-regression
name: Investigate Rust Regression
description: Diagnose a Rust regression by reproducing, narrowing, patching, and retesting it.
version: 3
scope: project
tools:
  - read_file
  - edit_file
  - shell
```

Optional:

```yaml
triggers:
  - rust regression
  - failing cargo test
requires:
  - project_trusted
```

The body contains the procedure.

## Progressive disclosure

Normal context contains only compact catalog entry:

```text
id
name
description
scope
tool requirements
```

Full body loads only when:

- model explicitly requests through `skill_load`; or
- deterministic selector marks it highly relevant and context compiler includes it under budget.

References/scripts are not loaded with body unless individually requested/executed.

Emit:

```text
skill.catalogued
skill.loaded
skill.executed
```

as appropriate.

## Skill registry and scopes

Support:

```text
global
project:<id>
agent:<id>
```

Duplicate ambiguous IDs are rejected under one documented shadowing/uniqueness rule.

CLI:

```bash
hames skill list
hames skill show <id>
hames skill validate <path-or-id>
hames skill install <path>
hames skill archive <id>
```

## Immutable versions

Durable skill metadata tracks:

```text
skill_id
version
content_hash
status
scope
created_at
created_by
base_version nullable
evidence_event_ids
```

Statuses:

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

An active skill version is immutable.

Editing creates candidate new version.

Compare-and-swap activation requires expected base content hash still matches current active version.

## Script execution

Skill scripts are executable helpers, not instructions.

They must:

- declare required capability;
- execute through same policy gate;
- never receive more authority than current agent;
- have bounded output/time;
- emit tool/skill events.

A skill cannot smuggle unrestricted subprocess execution around `shell`.

## Usage tracking

Track:

- catalog appearances;
- full loads;
- associated tool calls;
- runs in which skill was active;
- successful task outcomes;
- failed task outcomes;
- corrections after use;
- last use;
- patches;
- estimated context cost.

Do not define “success” solely as “model loaded skill.” Use settled run outcome signals.

## Autonomous candidate detector

Run a cheap deterministic detector after a run settles.

Candidate evidence:

```text
similar_workflow_count
successful_multistep_trace
user_correction_followed_by_resolution
non_obvious_error_recovery
tool_call_cost
existing_skill_similarity
future_reuse_score
volatility
security_sensitivity
```

Create candidate when configured rule is satisfied, including:

- materially similar successful workflow occurred at least twice;
- explicit correction led to verified improved workflow;
- non-obvious error was recovered with reusable sequence;
- high-cost workflow has strong project recurrence evidence;
- user explicitly asks Hames to do method consistently.

Reject as skill material when:

- primarily a fact;
- hard safety invariant;
- one-off/transient;
- active skill already covers it;
- workflow did not settle successfully;
- evidence includes secrets that cannot be safely abstracted.

Detector emits `skill.proposal_triggered` with evidence IDs.

## Autonomous drafting

When auto-drafting is enabled, Hames may run a reviewer/drafter model call after trigger.

Drafter receives:

- exact evidence trace subset;
- relevant current skill if patching;
- required tool/policy boundaries;
- no unrelated conversation history.

It outputs:

- proposed `SKILL.md`;
- rationale;
- evidence links;
- acceptance tests;
- new skill vs patch decision.

This is proposal, not activation.

Automatic drafting obeys configured evolution budget. Zero budget leaves proposals undrafted until requested.

## Validation and tests

Validation includes:

- metadata schema;
- unique ID;
- no forbidden paths;
- declared tools exist;
- script policy compatibility;
- references resolve;
- content-size bounds.

Skill tests may include:

1. deterministic script/unit tests;
2. trace assertions;
3. replay cases using fake provider;
4. optional live/model evaluation under explicit budget/approval.

Proposal cannot become `verified` if deterministic validation fails.

## Promotion

CLI:

```bash
hames skill proposal list
hames skill proposal show <id>
hames skill proposal approve <id>
hames skill proposal reject <id>
```

Approval:

1. re-read current base skill;
2. verify base hash;
3. reject stale proposal on mismatch;
4. rerun deterministic validation/tests;
5. atomically activate new version;
6. preserve old version as superseded;
7. emit `skill.promoted`.

No silent global activation.

## Curation

Implement deterministic curation metrics.

A skill can become `stale` when:

- unused for configured period;
- repeatedly loaded but never used;
- poor outcome rate;
- superseded by another skill.

Archival is reversible and does not delete versions.

Automatic merging of skill bodies is not required. Hames may propose consolidation, but promotion uses same proposal path.

## Context and Inspector

Context manifest shows catalog and loaded skills separately.

Inspector adds:

- active catalog;
- version history;
- load/use counts;
- evidence;
- proposal diffs;
- validation/test results;
- status transitions.

## Tests

Cover:

- parsing/validation;
- progressive disclosure;
- scope visibility;
- content hash/versioning;
- stale compare-and-swap rejection;
- usage metrics;
- repeated-success trigger;
- correction trigger;
- false-positive exclusions;
- auto-draft with fake reviewer;
- evolution budget;
- validation failure;
- approval/rejection;
- archive/restore;
- skill script cannot bypass policy;
- migration from M06.

## Manual smoke test

Perform same nontrivial coding workflow twice.

Verify:

1. detector creates candidate;
2. auto-drafter produces skill proposal;
3. proposal references exact runs;
4. proposal inactive before approval;
5. inspect and approve;
6. matching third task sees catalog and progressively loads skill;
7. context inspector shows estimated token contribution.

## Commit expectations

Suggested slices:

1. skill package/parser/registry;
2. catalog/progressive loading;
3. versioning and usage;
4. detector;
5. drafting/proposals;
6. validation/promotion;
7. curation;
8. inspector/docs/e2e.

## Acceptance gate

M07 is complete when Hames can notice reusable workflow on its own, draft grounded skill proposal, validate it, preserve version history, require controlled promotion, and later load active skill only when relevant.

Finish clean and tag `m07`.
