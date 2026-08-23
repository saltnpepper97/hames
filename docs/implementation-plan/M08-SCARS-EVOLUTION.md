# M08 — Scars, Repair Routing, Evaluation, and Regression Protection

## Goal

Implement Hames’s defining self-correction mechanism.

A **Scar** is a durable record of a meaningful failure or correction linked to evidence, trigger conditions, expected behavior, a chosen repair layer, evaluation, and future regression checks.

The goal is not autonomous self-rewriting. The goal is evidence-backed, inspectable improvement.

## Scar states

```text
candidate
open
repair_proposed
guarded
healed
regressed
dismissed
```

A Scar is append/versioned through events; current state may be materialized for efficient queries.

## Scar record

Required fields:

```text
id
title
scope
status
severity
failure_signature
description
trigger_spec
expected_behavior
evidence_event_ids
repair_layer nullable
repair_reference nullable
created_at
last_triggered_at
successful_guard_count
regression_count
```

Trigger spec may include:

```text
project IDs
agent IDs
intent labels
entity IDs
tool/error signatures
skill IDs
retrieval/context signatures
```

Triggers must remain inspectable; do not hide them in opaque embedding similarity alone.

## Failure/correction inputs

### 1. Explicit correction

Add reliable user pathways.

REPL:

```text
/correct <short explanation>
```

Also add backend support for a future Web “Mark correction” action.

Correction event links to relevant assistant/model/tool event.

### 2. Conversational correction candidate

Implement conservative detector that can mark a user message as likely correction based on:

- explicit contradiction/correction language;
- reference to immediately preceding result;
- optional reviewer-model classification under evolution budget.

Automatic classification creates a candidate, not unquestionable truth.

### 3. Repeated runtime failure

Normalize failure signatures for:

- same tool error;
- repeated provider failure;
- repeated policy rejection caused by same attempted behavior;
- repeated skill-associated task failure.

A configurable recurrence threshold can create a Scar candidate.

### 4. Skill outcome regression

If active skill is repeatedly associated with corrections/failures, create Scar candidate referencing exact skill version.

## Scar creation

A candidate becomes `open` when evidence is sufficient or user confirms it.

Creation process answers:

- What failed?
- Under which conditions?
- What should have happened?
- Is this one-off or reusable correction?
- Which evidence proves diagnosis?

Low-confidence speculative diagnosis remains candidate or is dismissed.

## Repair routing

Hames selects the **weakest sufficient repair layer**.

### Semantic memory

Use when stable fact was missing/wrong.

Repair: memory proposal/supersession.

### Relationship memory

Use when entity linkage/dependency/ownership was wrong.

Repair: relationship proposal.

### Operational memory

Use when active status, blocker, next action, or current phase was lost.

Repair: work-item update/proposal.

### Skill

Use when repeatable procedure was poor.

Repair: new skill or skill patch through M07 proposal path.

### Policy

Use when non-negotiable safety/authority rule was missing.

Repair: declarative policy-rule proposal. Policy proposals cannot reduce protection without explicit user approval.

### Context rule

Use when correct information existed but was not reliably included/retrieved.

Add versioned `context_rules` capable of rules such as:

```text
when project == rootlatch and intent == project_status:
    require source operational.current_milestone
```

Rules reference source types/records, not arbitrary hidden prompt text.

### Capability/plugin

Use when Hames lacked a genuine capability.

At M08 this creates capability requirement proposal. M09 can turn it into isolated plugin proposal.

Do not force every failure into prompt/skill.

## Repair proposal

Every proposal contains:

```text
scar_id
repair_layer
base_version/hash where applicable
proposed diff/change
rationale
evidence
deterministic checks
model-eval cases optional
risk
required approval
```

Repairs cannot silently broaden agent/plugin permissions.

## Replay/evaluation engine

Build evaluator that consumes historical episodes/events and determines whether a proposed repair satisfies explicit checks.

### Deterministic evaluations

Run automatically:

- required memory record available;
- required context source included;
- policy rule blocks/allows intended fixture;
- skill metadata/trace tests pass;
- no unauthorized scope broadening;
- no stale base version.

### Model-based evaluations

Optional and budget-controlled.

Replay case contains:

```text
historical user/task input
reconstructed allowed context
candidate repair
expected rubric
```

Use configured evaluator model or fake evaluator in tests.

Record:

- model/provider;
- prompt/context hashes;
- result;
- usage;
- rubric score;
- evaluator uncertainty.

No hidden paid evaluation.

## Promotion

A repair may auto-promote only if all hold:

- it does not change permissions/policy authority;
- it is within configured low-risk repair class;
- deterministic checks pass;
- user configured auto-promotion for that class.

Default v0.1 posture:

- memory corrections explicitly grounded in direct user correction may auto-promote within same scope;
- skill changes require approval;
- policy changes require approval;
- context rules require approval;
- plugin/capability changes require approval.

All promotion is versioned and emits events.

## Guarding and healing

Once repair is active, Scar becomes `guarded`.

On matching future runs:

- evaluate whether expected behavior held;
- increment success guard count;
- record recurrence if failure returns.

Default healing threshold is configurable, with sensible default of 3 comparable successes.

When threshold met:

```text
guarded → healed
```

Healed Scar remains in regression history but is no longer routinely injected into model context unless trigger requires active guard behavior.

If failure returns:

```text
healed/guarded → regressed
```

and create new repair proposal version.

## Context integration

Add source:

```text
evolution.scar
```

Only inject concise active Scar guard when:

- trigger matches current project/agent/intent;
- repair depends on model behavior rather than deterministic policy/context rule;
- context budget allows.

Prefer enforcing repair in memory/context/policy/skill mechanisms instead of reminding model forever.

## Inspector

Add Evolution views:

- Scar list by state/severity;
- evidence timeline;
- trigger;
- expected behavior;
- repair layer;
- proposed diff;
- evaluation results;
- guard successes;
- regressions;
- healing history;
- “why did this Scar trigger?” explanation.

## Tests

Cover:

- explicit correction linked to prior event;
- conversational candidate detector;
- repeated error signature;
- candidate confirmation/dismissal;
- each repair routing class;
- context rule enforcement;
- deterministic replay;
- fake model evaluator;
- budget blocking live/model eval;
- safe memory auto-promotion rule;
- policy/skill changes requiring approval;
- guarded success count;
- healing threshold;
- regression reopening;
- Scar context injection only on matching triggers;
- no permission broadening;
- migration from M07.

## Manual smoke test

Create controlled failure:

1. establish current project fact;
2. force/observe response using obsolete fact;
3. mark it as correction;
4. confirm Scar created with exact evidence;
5. route repair to semantic/operational memory or context rule;
6. approve/promote as required;
7. replay historical case;
8. repeat matching tasks until Scar becomes healed;
9. inspect full lineage from failure → repair → evaluation → healed state.

## Commit expectations

Suggested slices:

1. Scar schema/state machine;
2. correction/repetition detectors;
3. repair router;
4. context rules/policy proposal integration;
5. replay/evaluator;
6. promotion/guard/healing;
7. inspector/e2e/docs.

## Acceptance gate

M08 is complete only when Hames demonstrates a complete, inspectable improvement cycle:

```text
failure
→ evidence
→ Scar
→ repair proposal
→ evaluation
→ controlled promotion
→ guarded future runs
→ healing or regression
```

A mere “remember user feedback” feature does not satisfy this milestone.

Finish clean and tag `m08`.
