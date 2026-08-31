# M10 — Ratatui Client Slice

## Outcome

Hames now has a transcript-first, full-screen Ratatui client over the existing
versioned HTTP and SSE gateway. It does not duplicate policy, execution, session,
or persistence logic from the trusted Python runtime. The classic Rust REPL
remains available for line-oriented and non-interactive use.

## Entry and continuity

- `hames` opens Ratatui when stdin and stdout are terminals.
- `hames tui` selects Ratatui explicitly.
- `hames repl` and `hames --repl` select the classic client.
- Non-terminal input or output falls back to the classic client.
- Opening the TUI creates a new session. `/sessions` offers prior open work for
  explicit switching. Exiting restores the terminal and prints an exact resume
  command
  only when the session contains conversation; an unused session is retired
  silently.
- Durable replay reconstructs messages, Thoughts, grouped tool activity,
  approvals, failures, cancellation, and completed work after reconnect.

## Screen and input

The screen has a single-row identity/runtime header, scrollable transcript,
temporary sheet area, expanding composer, and one reserved status row. The
composer grows to eight visible content rows and then scrolls independently. It
uses a visible prompt caret, side padding, a right-aligned model/effort/mode label,
and mode-colored borders without imposing a filled background.

The status row keeps the live gateway stream state on the right: `connecting`,
`connected`, `reconnecting N`, or `offline`. Its left side shows shortcuts while
idle and becomes a short, subdued neutral activity rule during a run, with the
current activity, properly formatted elapsed time, and a truthful `Esc×2 interrupt`
control.

The header identifies Hames, the full current-directory path (with the home
prefix compacted to `~`), and the Git branch when the directory belongs to a
repository. Its right edge presents a durable,
human-readable session title and the current activity (`Ready`, `Thinking`,
`Exploring`, `Writing`, and related states).
The model may maintain the title through `session_title_set`, which emits a typed
`session.title.changed` event; `/title` uses the matching gateway endpoint.

Enter sends a message. Alt+Enter, Shift+Enter, or Ctrl+J inserts a new line.
An empty opening transcript centers a bounded ASCII mark sampled from the bundled
text asset. A slow gray sheen is separated by a long fully idle interval; the
first composer character, newline, or paste dismisses the mark for that session.
Large pastes are shown as compact capsules while their exact bytes and ranges
remain durable in the user-message event. The transcript and composer both
render proportional scrollbars with solid thumbs when content exceeds their
viewport. Their tracks support mouse clicks and dragging.
Dragging is anchored to the position at mouse-down, so grabbing either thumb
does not jump the transcript or composer before the pointer moves.

## Controls and modes

- Shift+Tab cycles Manual, Auto, and Plan and persists the selection through the
  gateway. A change made during an active turn applies at its next tool-policy
  boundary.
- Manual uses a neutral gray composer border and requests permission for changes.
- Auto keeps the neutral border with blue mode text and asks only for dangerous operations.
- Plan uses a yellow border and text and allows inspection and tests while gateway policy rejects
  code-writing operations.
- Ctrl+K opens the command palette. Runtime model, reasoning effort, agent, and
  mode controls appear as native sheets above the composer.
- Slash commands and Ctrl+K share a borderless command tray: horizontal rules
  define its top and bottom while the selectable rows remain visually open. A
  gray band marks the active row and green marks only the query-matching text.
  All other above-composer selection sheets reuse those open rules and subdued
  rows, adding only an inset title to the top rule.
  The bottom-left shortcut strip becomes sheet-specific while any tray is open.
  `/sessions` uses a reversible two-press Ctrl+D removal gesture: the first
  press marks only the selected row red, navigation or Esc disarms it, and the
  second press retires the session from resumable history. The picker refreshes
  and remains open after every removal, including an active-session replacement;
  that fresh empty replacement is omitted from the refreshed list so deletion is
  visually unambiguous.
  Rows use their literal slash names. `/new` preserves the current conversation
  and starts another without waiting on an active model run; `/clear` retires it
  before starting fresh. Empty sessions are discarded and hidden. Bare `/resume`
  aliases the `/sessions` picker and `/resume <id>` remains the direct path.
  `/status` opens session continuity and `/gateway` owns gateway health.
- `/compact` asks the gateway to summarize older conversation with the active
  session provider and model. Its live and completed states remain one collapsed,
  expandable transcript disclosure, and `/status` reports the latest compaction.
- `/goal <objective>` starts durable gateway-supervised autonomous work, while
  bare `/goal` opens a square supervisor view with state, elapsed time, steps,
  latest progress and evidence, mode, provider, and model. Pause, resume, and
  confirmed cancellation are available from the view or slash subcommands.
  Foreground messages yield the goal and run first; pressing `Esc` twice pauses
  an active goal step. `/new` leaves the old session's goal running, `/clear` cancels it, and a
  terminal exit leaves it under gateway ownership.
- `/model` shows reachable configured providers only. Reasoning-capable model
  choice leads to a capability-specific second sheet: `on`/`off` for boolean
  reasoning or the advertised named effort scale. Both selections apply
  atomically at the end.
- `/themes` switches between the custom Hames RGB palette and terminal-native
  ANSI colors. The global choice is persisted in `~/.hames/ui.toml` and loaded
  before the first draw.
- Transcript and modal mouse selection are rendered by the TUI and copied on release with
  OSC 52, preserving mouse-driven scrollbars and Thought toggles. A short copy
  confirmation occupies the notice row above the composer.
- Only `model.requested` events belonging to the active foreground chat run may
  create a pending Thought; background memory and workflow model jobs remain out
  of the conversation transcript.
- Centered modals use square borders in the lighter input gray; semantic accents stay within
  their content. Selection inside a modal does not dismiss it. Workspace trust is
  decided before either client starts its UI, using one shared terminal list with
  arrow navigation and Enter confirmation; non-terminal input cannot grant trust.
  Approvals preserve the gateway's allow-for-session, allow-once, and deny semantics.
- `/memory` is a selectable active-record browser. The focused memory expands its
  full summary and value with independent detail scrolling. Ctrl+D arms a red-row
  confirmation and Ctrl+D again permanently deletes the record while leaving the
  browser open; deleted memories disappear immediately.
- `/scars` mirrors that browser interaction while exposing a fuller diagnosis:
  lifecycle state, detection, signature, problem, expected behavior, linked
  repair, evidence count, guards, regressions, and last trigger. E opens a
  structured editor for the human-facing diagnosis and Ctrl+S records the edit;
  immutable evidence and repair lineage are never rewritten. Ctrl+D uses the
  same two-step permanent-deletion gesture and leaves the browser open.
- Status, usage, events, inspection, context, memory, Skills, Scars, plugins,
  export, correction, and explicit memory capture are reachable from the palette
  or slash commands.

## Activity presentation

Adjacent operations retain ledger order while visually grouping under continuity
headings such as Explore and Write. Preparing, streaming, completion, rejection,
and failure states remain visible. Empty stream fragments never create phantom
activity or assistant rows. Memory operations use semantic verbs such as
Remembered, Updated, and Forgot rather than generic completion labels. Active
Thoughts animate with a slow restrained sheen; settled Thoughts
collapse when subsequent work begins and show a formatted duration only after the
significance threshold. Keyboard and mouse controls can expand them again.

## Acceptance evidence

Rust unit tests cover replay reduction, paste fidelity, command routing, mode
cycling, adaptive rendering, significant Thought duration, and composer scrolling.
The PTY end-to-end test launches an isolated gateway and database, sends a real
chat turn through the TUI, observes durable reasoning and completion events, quits,
and verifies terminal cleanup plus the resume handoff.

## Pre-web terminal hardening closure

The 2026-08-25 hardening pass closes the terminal slice before work begins on the
web control surface:

- Protocol v28 assigns every message admission a UUID submission ID. Migration 17
  stores durable receipts, exact replays return the original run or queue result,
  and reuse with a different payload returns a typed conflict. Clients retain the
  same ID across bounded admission retries and ambiguous TUI retries.
- Control requests have bounded connect and operation timeouts; provider probes
  have a separate longer budget; SSE has no total response timeout. Both terminal
  clients use one strict byte-buffered SSE decoder with CRLF and multiline-data
  support, a 1 MiB frame cap, and no lossy UTF-8 conversion.
- Event streams reconnect forever with a capped 250 ms through 8 s backoff and
  resume from the last durable sequence. Classic REPL cancellation remains usable
  during reconnect.
- TUI terminal ownership is staged and reversible. Partial setup failures, normal
  exit, panic unwinding, SIGTERM, and SIGHUP restore raw mode, alternate screen,
  mouse/focus/paste modes, keyboard enhancement flags, and cursor visibility before
  any gateway cleanup is attempted.
- Repeated key events are accepted only for editing and navigation; commit and
  action keys do not repeat. Width wrapping and truncation preserve Unicode
  grapheme clusters.
- The classic REPL precreates its history with mode `0600`, reports load/save
  failures as warnings, and appends accepted lines incrementally instead of
  risking the whole session history at shutdown.

Web search, vision input, the web application, and richer management editors are
outside this terminal slice. They remain future gateway-backed capabilities; the
TUI introduces no alternate provider or policy path for them.
