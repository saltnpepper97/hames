# M06 — Relationship, Semantic, and Episodic Memory

## Goal

Give Hames durable continuity without treating every conversation fragment as
memory or forcing work into a formal project registry.

The three memory layers are:

1. **Relationship:** useful knowledge about the user, including communication
   preferences, working patterns, and explicitly supported personal context.
2. **Semantic:** durable known facts learned through interaction.
3. **Episodic:** compact provenance-backed accounts of what happened, including
   actions, outcomes, failures, and lessons.

Procedural knowledge belongs to Skills in M07.

## Flexible anchors and visibility

Memory records may use flexible anchors such as user, agent, canonical workspace
path, repository identity, topic, entity, or session. An anchor is not a required
project object and does not create a project-management workflow.

Every record has explicit visibility: global, agent-private, workspace-anchored,
or session/team. Promotion to broader visibility is an attributable operation.

## Relationship memory

Represent user preferences and entity relationships with confidence, temporal
validity, provenance event IDs, and supersession. Do not infer sensitive personal
attributes merely because they appeared in conversation.

Useful examples include the user's preferred writing voice or a known relationship
between people and systems. Transient mood, unsupported inference, and secrets are
not durable relationship memory.

## Semantic memory

Represent facts as typed assertions with subject, predicate, value, confidence,
importance, anchors, validity, provenance, and active/superseded/retracted state.
A correction appends a replacement and supersedes the earlier assertion.

Conversation presence alone does not make a fact durable. Explicit user facts may
be accepted under policy; model-inferred facts default to proposals.

## Episodic memory

Project settled runs from the event ledger into compact episodes containing the
request, participating agents, working-directory anchors, important actions,
outcome, failures, corrections, and evidence links. The ledger remains the full
historical source; episodic memory is its retrieval-oriented summary.

If a model generates an episode summary, that is an explicit attributable model
call. Deterministic summaries are preferred when sufficient.

## Retrieval and context

Apply visibility filtering before ranking. Combine direct anchor/entity matches,
FTS5, confidence, importance, and appropriate recency. Optional embeddings may be
added without making external vector infrastructure mandatory.

Context manifests identify each retrieved record, layer, anchor, score, provenance,
and token estimate. Superseded or inaccessible records cannot enter context.

## Events and operations

Support proposal, acceptance, rejection, supersession, retraction, retrieval, and
episode projection through typed versioned boundaries. Every mutation and retrieval
emits an event tied to its cause.

## Tests

- relationship/semantic/episodic classification;
- visibility isolation and flexible-anchor matching;
- correction and supersession;
- provenance requirements;
- deterministic episode projection and restart consistency;
- retrieval without embeddings;
- inaccessible and superseded records excluded before ranking;
- context manifests explain included memory.

## Acceptance gate

M06 is complete when the three layers provide useful, correctable continuity across
sessions without a mandatory project registry, raw-transcript embedding, scope
leakage, or hidden provenance.

## Implemented result

Completed on 2026-08-23 with gateway protocol v8 and SQLite migration 7.

- Relationship and Semantic records are extracted in recoverable background jobs
  from bounded current-turn evidence. Explicit user and successful-tool facts may
  auto-activate under confidence and importance thresholds; inference remains a
  proposal.
- Episodic records are deterministic, idempotent projections of notable runs.
  Routine conversation is deliberately skipped.
- Global, agent-private, exact-workspace, and session-team visibility is enforced
  before FTS5 ranking. Flexible record anchors do not create a project registry.
- Active retrieval is bounded by record count and tokens. The selected set is held
  stable across one tool loop, emitted as `memory.retrieved`, and fully attributed
  in the context manifest.
- `/remember` and `/memory` expose capture, search, proposal review, correction,
  promotion, forgetting, job status, and retry through the Rust REPL and protocol.
- Memory mutation, retrieval, extraction provider work, and episode projection are
  typed ledger events. Secret-like standalone captures are rejected before append,
  and no rejected candidate is materialized.

The full model trace remains in the event ledger and audit views; M6 does not add a
separate "dream" executor or permit memory jobs to run tools. Procedures remain
reserved for Skills in M7. See
[`../architecture/memory.md`](../architecture/memory.md) for the implemented
invariants.
