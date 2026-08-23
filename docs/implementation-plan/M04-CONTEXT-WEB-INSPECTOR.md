# M04 — Context Compiler, Manifests, and Deep Observability

## Goal

Make every model request explainable while Hames is still operated primarily
through the Rust REPL. Rich Ratatui and web interfaces remain later consumers of
the same event and gateway contracts.

## Context compiler

All model calls pass through one deterministic compiler. Sources return attributed
candidates with stable IDs, content hashes, priority, token estimates, visibility,
and truncation behavior. Later memory, Flows, agents, and Scars extend this source
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
