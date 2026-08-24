# Scars and Self-Correction

Scars are Hames's durable record of meaningful failures and corrections. A Scar
links evidence, trigger conditions, expected behavior, a chosen repair layer,
evaluation, and future regression checks. The goal is autonomous, inspectable
improvement without unbounded self-rewriting or silent authority expansion.

## Scar lifecycle

```text
failure or correction
  -> detection (explicit, conversational, repeated failure, skill regression)
  -> candidate -> open
  -> repair candidate (weakest sufficient layer)
  -> evaluation (deterministic + optional budgeted model)
  -> promotion or rejection
  -> guarded future runs
  -> healed after comparable successes, or regressed on recurrence
```

Every state change is an append-only `scar.*` ledger event; the SQLite rows in
`scars`, `scar_repairs`, and `scar_evidence` are projections of those events.

## Detection inputs

1. **Explicit correction** — `/correct <explanation>` in the REPL or
   `POST /v1/sessions/{id}/correct`. Links the offending event when supplied.
   Always opens a high-severity Scar.
2. **Conversational correction** — deterministic contradiction language in a
   completed run's user message. Optional reviewer-model classification
   (`[evolution].reviewer_model_enabled`) catches messages without markers;
   it is budget-gated and off by default.
3. **Repeated runtime failures** — normalized failure signatures
   (`tool:<name>:<summary>`, `provider:<code>`, `policy:<reason>`) counted per
   workspace; `[evolution].recurrence_threshold` opens a Scar.
4. **Skill outcome regression** — a Skill version repeatedly associated with
   failed or corrected runs references the exact immutable version.

Repeat detections trigger the existing Scar. Repeating a correction after its
repair was promoted regresses the Scar instead.

## Repair routing

`plan_repair` selects the weakest sufficient layer:

| Signal | Layer | Execution |
|---|---|---|
| User preference correction | relationship memory | memory record, auto-promoted |
| Factual correction | semantic memory | memory record grounded in user wording |
| Poor repeatable procedure | skill | M07 patch pipeline |
| Missing capability | capability requirement | recorded for M09 plugins |
| Opaque repeated failure | none autonomously | user directs via explicit layer |

Memory repairs grounded in direct user correction execute immediately and move
the Scar to `guarded`. Policy-rule, context-rule, and capability repairs are
stored as proposals; they never execute or promote without approval.

## Rules

Approved repairs can become declarative rules (migration 10):

- **Context rules** require source types in compiled context for matching
  workspace/agent conditions. Enforcement is deterministic: compilation raises
  `context_rule_violation` when a required source is missing.
- **Policy rules** are additive shell-command deny/confirm regexes evaluated by
  the policy gate after built-in tables. They can only add protection.

Rules are inert until activated through the authenticated gateway; activation
is the approval act and is itself a ledger event.

## Evaluation

Every repair candidate runs deterministic checks: evidence availability,
required memory records, policy-rule fixtures (`must_block`/`must_allow`),
non-stale skill base versions, and no unauthorized scope broadening. Optional
model evaluation uses purpose-tagged `evolution_evaluation` calls under the
daily budget with recorded verdicts. Passing authority-changing repairs stay
`pending_approval`; only low-risk classes auto-promote.

## Guarding and healing

Completed matching runs count guard successes toward
`[evolution].healing_threshold` (default 3); reaching it heals the Scar.
A returning failure signature flips guarded/healed Scars to `regressed`,
increments the regression count, and automatically proposes a new repair
candidate version routed through evaluation again.

Guarded scars whose repair depends on model behavior are injected into
compiled context as the compact `evolution.scar` source only when their trigger
matches, bounded by `max_active_context_scars` and the scar token budget.
Deterministic mechanisms (memory, rules) are preferred over reminding the model
forever.

## Inspection

`GET /v1/sessions/{id}/scars/{scar_id}/inspection` derives the full lineage
read-only from the ledger: evidence timeline, state transitions, repair
candidates with decisions, evaluations with reports, guard counts, and a plain
explanation of why the Scar triggered. The REPL exposes `/correct` and
`/evolution [list|show|open|guarded|healed|regressed]`.

Chat runs use typed controller tools rather than generic state-file access:
`scar_list` inspects visible records, `scar_record` records and opens an explicit
correction, and `scar_control` opens a candidate or dismisses a visible Scar.
Dismissal is approval-gated. Healing, regression, repair promotion, rule
activation, and plugin installation remain owned by evaluation or the human
control plane; the model cannot declare those outcomes for itself.
