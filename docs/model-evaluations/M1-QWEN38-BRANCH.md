# M1 Qwen3.8 branch evaluation

Date: 2026-08-23

This live local-provider smoke test used an isolated `HAMES_HOME`, the protocol-v2
Rust REPL and Python gateway, llama.cpp, and `qwen3.8-27b` with low reasoning effort.

## Procedure and result

1. A root session received a prompt containing the marker `ALPHA` and completed a
   normal reasoning/answer run.
2. Bare `/fork` created a child at the completed `assistant.message` and switched
   the REPL to it.
3. The child was asked for the marker from the inherited conversation and answered
   exactly `ALPHA`.
4. `/session` reported the parent and fork event; `/events` displayed the effective
   history with stable global sequences.
5. The isolated ledger contained two sessions, nineteen events, and exactly one
   `session.forked` event after clean shutdown.

Separately, the real M0 database migrated from schema 2 to schema 3 while preserving
its existing session and nine events. A migrated assistant event passed payload
integrity verification, and the owned protocol-v1 gateway was automatically replaced
by protocol v2.
