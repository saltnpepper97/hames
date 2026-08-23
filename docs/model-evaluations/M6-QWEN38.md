# M6 Qwen3.8 layered-memory acceptance

Date: 2026-08-23

## Environment

- Hames protocol: v8
- SQLite migration: 7 with FTS5
- Client: Rust REPL
- Gateway: isolated persistent Python gateway on loopback
- Provider profile: `llama_cpp`
- Model: `qwen3.8-27b`, medium reasoning
- State: fresh temporary `HAMES_HOME`

## Exercise

The clean-state doctor gate reported a healthy protocol-v8 backend and available
FTS5. The REPL then queued this explicit capture:

```text
/remember I prefer milestone summaries that lead with the outcome.
```

The first live attempt exposed an incompatibility not represented by the fake
provider: llama.cpp could not compile the nested Pydantic `$defs` tool schema into
its constrained grammar. A self-contained schema removed the references, and a
second refinement bounded and simplified the grammar. The same durable failed job
was retried through `/memory retry`; it completed and materialized this active
global Relationship record:

```text
User prefers milestone summaries to lead with the outcome.
```

The next ordinary turn asked what preference Hames remembered. Qwen answered:

```text
I remember that you prefer milestone summaries to lead with the outcome — state
what was accomplished first, then supporting detail.
```

`/context` showed the exact Relationship memory ID, global visibility, 15-token
estimate, and retrieval score `10.400` among the selected model sources.

## Result

Pass after fixing the live-discovered schema incompatibility. The test exercised a
clean migration, FTS5 diagnostics, durable explicit capture, bounded failure and
manual retry, local Qwen extraction, active retrieval in a later turn, correct
recall, and context-level attribution. The isolated gateway was stopped afterward;
no test record entered the user's normal Hames state.
