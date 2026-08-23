# M04 — Context Compiler, Manifests, and Deep Observability

## Goal

Make every model request explainable while Hames is still operated primarily
through the Rust REPL. Rich Ratatui and web interfaces remain later consumers of
the same event and gateway contracts.

## Context compiler

All model calls pass through one deterministic compiler. Sources return attributed
candidates with stable IDs, content hashes, priority, token estimates, visibility,
and truncation behavior. Later memory, Skills, agents, and Scars extend this source
model without bypassing it.

Use explicit budgets for stable instructions, agent identity, recent conversation,
tools, retrieved context, and reserve output. Deterministic compaction preserves
recent turns and replaces older large tool output only with attributable summaries.

## Context manifest

Every model call emits a manifest identifying selected and omitted sources,
ordering, hashes, truncation, estimates, provider/model, reasoning setting, and the
events that contributed content. Exact provider usage remains distinct from token
estimates.

## Observability projection

Derive session timelines, branches, model timing, provider reasoning and answers,
tool and policy activity, context composition, usage, failures, and corrections
from the ledger. Do not create a second analytics truth store.

For terminal-first development, add structured inspection commands and an optional
derived transcript export. A transcript is never the provenance source and its
format is not allowed to constrain the event schema.

## Rich-client boundary

Specify gateway views needed by a future customized Ratatui interface and web UI,
but do not build either in this milestone unless the REPL has already validated the
underlying behavior. No client may import hidden Python runtime state.

## Tests

- deterministic context ordering and budgeting;
- manifest/source hash equality;
- omission and truncation reasons;
- exact provider usage separated from estimates;
- ledger-derived timeline reconstruction after restart;
- reasoning and final-answer channels remain distinct;
- transcript export reproduces durable content without becoming writable state.

## Acceptance gate

M04 is complete when every model request can be reconstructed and explained from
its manifest and ledger events through the gateway or terminal inspection tools.

## Implemented result

M04 is implemented on gateway protocol 5 and database migration 5.

- Context capacity resolves from a profile override, provider model metadata, or
  a conservative 32K fallback. The resolved value and provenance are durable
  session settings and survive settings changes and forks.
- The compiler uses the deterministic `utf8-bytes-div-4-v1` estimator, reserves
  output separately, enforces category limits, preserves the active turn, selects
  older turns newest-first, and records budget omissions and tool-result
  compaction.
- Reasoning remains durable and inspectable. It is replayed only within its
  active tool loop; reasoning from completed runs is an audit-visible omitted
  source rather than input to later user turns.
- Each `context.compiled` event contains attributed source decisions and links to
  a content-addressed canonical provider-request snapshot whose digest is checked
  during inspection.
- Gateway views derive session runs, timelines, context inspection, estimates,
  exact provider usage, and ancestry-aware transcripts directly from ledger
  events. Markdown and JSONL exports explicitly identify themselves as derived.
- The Rust REPL exposes `/inspect`, `/context`, gateway-derived `/usage`, and
  private `/export` files. `hames session export` provides the same formats,
  refuses replacement by default, and requires `--force` to overwrite.

No Ratatui, web UI, memory, named-agent expansion, or Skills behavior was added.
The attributed source contract is the tested foundation those later milestones
will consume.

## Acceptance evidence

- deterministic ordering, budgeting, source hashes, omissions, compaction, and
  typed oversize failures are covered by compiler tests;
- active and completed reasoning replay behavior is covered independently;
- exact request snapshots are hash-verified through the gateway;
- projections reproduce identically after reopening the database;
- the Rust end-to-end test exercises `/usage`, `/inspect`, `/context`, REPL export,
  CLI export, overwrite refusal, and explicit forced replacement;
- the complete Python and Rust formatting, static-analysis, and test suites pass.
