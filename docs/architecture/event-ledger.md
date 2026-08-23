# Event ledger architecture

The Hames ledger is the durable explanation of what happened. Runtime modules may
cache or stream transient state, but anything that affects conversation history or
future behavior must eventually be attributable to an append-only event.

## Sessions and branches

A root session records the canonical directory, agent, provider, model, and
reasoning selection. A branch is another session with an immutable
`parent_session_id` and `fork_event_id`; parent events are never copied.

Effective replay recursively reads the parent through the fork event, then appends
the branch's local events. A nested branch applies every ancestor cutoff in turn.
The global SQLite sequence is the stable ordering and SSE resume cursor. UUID4 IDs
provide identity but do not define order.

Bare `/fork` selects the latest completed `assistant.message`. An explicit event ID
or sequence may select any event visible in effective history. Provider settings on
the new branch are reconstructed from `session.opened` and
`session.settings.changed` events through that cutoff.

## Causation and correlation

`causation_id` points to the event that directly caused another event. For example,
a model request is caused by its context compilation. `correlation_id` groups all
events in one logical activity, normally a run or branch creation. Neither field is
used as a substitute for session ancestry.

## Agent runs and tool history

Since protocol v4, the run is the terminal unit. One user message causes
`run.started`; each provider cycle has its own context, request, response, usage,
assistant output, and optional `model.tool_call` events. Tool calls then produce
`tool.requested`, policy, optional approval, execution, and result events. Only
`run.completed`, `run.failed`, or `run.cancelled` ends the logical operation.

Context replay reconstructs assistant tool calls and linked tool-result messages,
so a provider continuation and a resumed or forked session see the same evidence.
Multiple calls from one response retain provider order and execute sequentially.
Approval records are mutable control-plane materializations, but every requested
and resolved transition is also preserved in the append-only event history.

## Memory projections

Protocol v8 adds layered-memory materializations without changing the ledger's
authority. Memory proposals, review transitions, extraction jobs, retrieval sets,
and episode projections all have typed events. The SQLite memory tables provide
efficient state and FTS5 retrieval, while provenance event IDs connect each record
back to durable evidence. See [memory.md](memory.md) for visibility and retrieval
rules.

## Typed payloads and forward reads

Known event types have strict Pydantic payload schemas and are validated before
append. The write API rejects unknown types. Readers intentionally preserve unknown
future event types as opaque JSON so an older inspector can traverse newer history
without treating it as database corruption.

Events cannot be updated or deleted, including through direct SQL. A correction is
another event; later milestones will add explicit supersession semantics.

## Inline payloads and blobs

Canonical compact JSON at or below `ledger.blob_threshold_bytes` is stored inline.
Larger JSON is stored once at:

```text
~/.hames/blobs/sha256/<first-two-hex>/<sha256>
```

Blob writes use a private temporary file and atomic rename. Files use mode `0600`
and directories use `0700`. The database stores exactly one of inline JSON or a
blob address plus the payload hash. Blob reads always recompute SHA-256; missing,
corrupt, or mismatched content produces a typed integrity error.

Verification covers the canonical event payload and referenced blob. It does not
claim that the entire SQLite database is cryptographically tamper-proof.

## Redaction contract

Redaction runs after type validation and before serialization, hashing, logging, or
blob storage. Explicit JSON-pointer paths and deterministic authorization/API-key
field names are replaced with:

```json
{"$redacted": true, "reason": "secret"}
```

Hames persists no secret hash, length, ciphertext, or recovery reference in M1.
Free-form text is not heuristically scanned; callers must explicitly identify
secrets that are not in recognized fields. A redacted event records
`redaction_state = "redacted"`.

## Migration and recovery

Protocol v2 performs the M0-to-M1 schema change inside one `BEGIN IMMEDIATE`
transaction. Existing sequences and payloads are copied and hashed before the old
table is dropped; any SQL failure rolls the transaction back. The migration is
one-way because protocol-v1 writers do not supply payload hashes. Hames restarts an
owned older gateway before migration and refuses to stop or replace an unrelated
process occupying the configured port.

Migration 4 adds exact canonical trusted-root grants and one-shot approval
materializations. Trust lives under `~/.hames` and applies across sessions using
the same canonical directory. Approval hashes bind the tool name, arguments, run,
session, agent, and working directory; a changed or previously resolved request
cannot reuse the decision.

Garbage collection is intentionally absent in M1. A failed database append can
leave an unreferenced content-addressed blob, which is safe and can be handled by a
future explicit maintenance command.
