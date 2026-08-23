# M01 — Append-Only Event Ledger, Sessions, and Blob Store

## Goal

Create Hames’s historical truth layer.

Every later feature must be able to point to durable events explaining what happened, in what session/branch, under which agent, and because of which earlier event.

M01 is complete when sessions can be created, branched, appended to, replayed, and
inspected through the gateway, CLI, or REPL without requiring a model call.

## Invariants

1. Events are append-only.
2. Event ordering is stable and resumable.
3. Large payloads may live outside SQLite but remain content-addressed and integrity-checked.
4. Session branches preserve ancestry.
5. Events can causally reference earlier events.
6. Schema versions are explicit.
7. Sensitive fields can be redacted before persistence.
8. The ledger API, not direct SQL, is the supported write path.

## Durable schema

### `sessions`

Required fields:

```text
id
parent_session_id nullable
fork_event_id nullable
created_at
closed_at nullable
title nullable
working_directory
agent_id
provider
model
reasoning_effort
status
```

Statuses:

```text
open
closed
cancelled
failed
```

A child session created as a branch records both the parent session and the event from which it forked.

### `events`

Required fields:

```text
id
sequence
session_id
agent_id nullable
type
schema_version
created_at
causation_id nullable
correlation_id nullable
payload_json nullable
blob_hash nullable
payload_hash
redaction_state
```

`sequence` must be monotonically increasing and suitable for SSE resume later.

Use globally unique IDs that can be created without a central service. ULID or UUIDv7 is acceptable.

### SQLite append-only enforcement

Add database triggers that reject `UPDATE` and `DELETE` on the events table.

If an event needs correction, append another event that supersedes or annotates it.

### Core event taxonomy

Implement and document typed payload schemas for:

```text
session.opened
session.closed
session.forked
user.message
assistant.message
runtime.error
runtime.notice
```

Later milestones add more event types through the same registry.

Unknown future event types must remain readable as opaque events rather than corrupting old sessions.

## Event API

Implement a service with:

- create session;
- close session;
- fork session from event;
- append typed event;
- append redacted event;
- list events after a sequence;
- list full branch history;
- reconstruct ancestry;
- get event by ID;
- verify event/blob integrity.

Appending validates the typed payload before persistence.

## Content-addressed blob store

Large event content must be storable under:

```text
~/.hames/blobs/sha256/<prefix>/<hash>
```

Requirements:

- SHA-256 address;
- atomic write through temporary file + rename;
- duplicate content stored once;
- event references exact hash;
- read verifies hash;
- corrupted content produces a typed integrity error;
- garbage collection is not automatic in this milestone because the ledger is append-only and references are durable.

Canonical payload JSON larger than 65,536 bytes is blob-backed by default. Configure
the threshold with `[ledger] blob_threshold_bytes` or
`HAMES_LEDGER__BLOB_THRESHOLD_BYTES`.

## Redaction

Create a persistence redaction layer before events reach the ledger.

At minimum redact:

- provider API keys;
- authorization headers;
- values explicitly marked secret by tool/provider schemas.

A redacted event uses `{"$redacted": true, "reason": "secret"}` and preserves no
secret hash, ciphertext, length, or recovery reference.

Do not attempt magical full secret detection yet. Explicitly tagged secret handling must be reliable.

## Replay

Implement a replay iterator that yields events in historical order for:

- one session;
- a session including inherited parent history up to its fork point;
- events after a sequence number.

Replay is read-only.

## CLI inspection commands

Add:

```bash
hames session new
hames session list
hames session show <session-id>
hames session fork <session-id> --at <event-id>
hames event verify <event-id>
```

The Rust REPL also provides `/session`, `/events [count]`, and
`/fork [event-id-or-sequence]`. Bare `/fork` selects the latest completed assistant
turn and switches the REPL to the new branch.

Output may be plain text/JSON. Provide `--json` for machine-readable inspection.

## Tests

Cover:

- event append ordering under concurrent async writers;
- SQL attempts to update/delete events fail;
- typed payload validation;
- unknown event read compatibility;
- session close behavior;
- branch ancestry and fork cutoff;
- invalid fork target rejection;
- blob deduplication;
- blob corruption detection;
- redaction before persistence;
- event listing after sequence;
- complete replay equality after process restart;
- migration from M00.

Include one property-style or fuzz-like test that appends a generated tree of sessions/events and verifies replay ancestry is consistent.

## Documentation

Add:

```text
docs/architecture/event-ledger.md
```

Document:

- why the ledger is append-only;
- session branch semantics;
- causation vs correlation;
- payload/blob split;
- redaction contract;
- how future modules add event types.

## Commit expectations

Suggested slices:

1. session/event schema and migrations;
2. typed event registry and append API;
3. blob store and integrity;
4. branching/replay;
5. CLI inspection and docs.

Each slice carries its tests.

## Acceptance gate

M01 is complete only when:

- a process can create a session, append messages, fork it, restart, and replay exactly the same branch history;
- events cannot be mutated through normal APIs or direct SQLite update/delete;
- corrupted blobs are detected;
- redacted secret-tagged fields never appear in persisted event payloads;
- concurrent appends preserve a stable sequence;
- CLI inspection works;
- all tests pass;
- the repository is clean;
- annotated tag `m01` is created.
