# Layered memory architecture

Hames memory is a durable, retrieval-oriented projection backed by the event
ledger. It is not hidden model state, a raw transcript embedding, or a required
project registry. SQLite migration 7 stores records, flexible anchors,
provenance links, FTS5 search materialization, and recoverable extraction jobs.

## Layers

- **Relationship** records stable user preferences and supported relationships.
- **Semantic** records durable known facts.
- **Episodic** records what happened in a notable run.

Procedures do not belong in these layers. Reusable procedures become Skills in
M7.

Relationship and Semantic candidates are structured by a bounded background model
call after a turn settles. The extractor receives the current user event, completed
assistant message, and bounded tool-result summaries—not hidden reasoning or an
unbounded transcript. It must call `submit_memory_candidates` with typed records
and visible evidence event IDs. Inferred facts remain proposals; high-confidence,
high-importance explicit user facts and successfully established tool facts may be
activated automatically. Explicit `/remember` captures are activated after the
same validation and extraction path.

Episodic records are deterministic projections of notable runs. Routine chat does
not create an episode. Tool activity, failures, cancellations, delegation outcomes,
and memory corrections make a run notable. Re-projecting the same run returns the
existing episode, so restart bookkeeping cannot duplicate it.

## Scope and visibility

Records use one of four visibility modes:

| Visibility | Visible to |
|---|---|
| `global` | every local session |
| `agent_private` | sessions using the owning agent |
| `workspace` | sessions in the exact canonical working directory |
| `session_team` | the root session and its branch/delegation lineage |

Every visibility receives a deterministic scope anchor. Records may also carry
flexible anchors such as a component, entity, topic, repository identity, or user.
An anchor improves retrieval and attribution; it does not register a project.
Promotion creates an active replacement at the requested visibility and
supersedes the prior record. There is no in-place broadening of authority.

## State and correction

A record moves through `proposed`, `active`, `rejected`, `superseded`, or
`retracted`. Acceptance, rejection, supersession, retraction, and promotion all
emit typed events. Records cannot be deleted from SQLite. `/memory forget` means
retraction: the record remains auditable but can no longer enter model context.

Every candidate requires provenance visible from the creating session. Secret-like
summaries or values, unsupported provenance, low-confidence candidates, and weak
non-user candidates are rejected before materialization. Model-generated episodes
are not accepted; episodic projection belongs to the deterministic runtime path.

## Retrieval and context

Visibility and active-state filtering happen before ranking. Ranking combines an
exact scope-anchor match, FTS5 relevance, importance, confidence, and recency, then
applies both a record count and token budget. No embedding service is required.
The selected set is fixed for the run and reused across tool-loop model turns.

Retrieved memory is inserted into the model request as untrusted data, not as
instructions. `memory.retrieved` records selected and omitted IDs, scores, token
estimates, and provenance. The context manifest repeats the record ID, layer,
visibility, anchors, retrieval score, and provenance so `/context` and later rich
clients can explain exactly what influenced a request.

## Background jobs and provider scheduling

Extraction jobs are durable and bounded. A gateway restart moves interrupted
`running` jobs back to `pending`; configured retries are finite, and a failed job
is available through `/memory status` and `/memory retry`. Chat completion does not
wait for extraction.

The active provider is wrapped by one async serialization lock. Foreground agent
turns and maintenance extraction therefore cannot interleave streams against a
local llama.cpp or Ollama server. A memory-specific provider, model, and reasoning
effort can be configured; blank values inherit the session selection.
