# Durable autonomous goals

Hames goals are gateway-owned supervisors for work that may require more than one
ordinary agent run. A goal belongs to one session, is reconstructed entirely from
typed `goal.*` ledger events, and survives client disconnects and gateway restarts.
There is at most one current goal per session.

## Bounded steps, unbounded objective

The goal itself has no arbitrary step, wall-time, or token ceiling. Every step is
still an ordinary bounded run with the existing 24 model-turn, 96 tool-call, and
30-minute active-time limits. When a step settles, the supervisor starts another
only after recording progress. Three equivalent no-progress results activate the
deterministic stall guard and move the goal to `blocked`.

Completion is explicit. The model uses the typed `goal_report` tool with a status,
summary, and concrete evidence. An ordinary assistant answer cannot silently mark
the objective achieved. The durable lifecycle is `running`, `yielded`, `paused`,
`achieved`, `blocked`, or `cancelled`; blocked and paused goals may be resumed.

## Foreground continuity

A new user message always wins. If a goal step is active, the gateway places the
message at the front of the session queue, records `goal.yielded`, cancels the
current bounded step, serves the foreground turn, and resumes the goal only after
foreground work and queued messages settle. This behavior is runtime-owned and is
identical for the TUI, classic REPL, and future clients.

In the TUI, pressing `Esc` twice during a goal step means pause, not a transient
cancellation that would immediately restart. `/goal resume` is then required.
Exiting the TUI leaves an active goal with the gateway; `/new` moves the client to a new session while the
old session's goal continues. `/clear` explicitly cancels the old goal before
retiring that conversation.

## Controls

- `/goal <objective>` starts autonomous work.
- `/goal` opens the current-goal view with objective, state, elapsed time, step
  count, latest progress/evidence, mode, provider, and model.
- `/goal pause`, `/goal resume`, and `/goal cancel` control the current goal.
- Cancellation requires confirmation in the TUI goal view.

Goals always obey the session's current Manual, Auto, or Plan policy. Changing mode
mid-flight takes effect at the next policy boundary, exactly as it does in an
ordinary run.
