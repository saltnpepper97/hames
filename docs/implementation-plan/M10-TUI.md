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
- Opening the TUI offers recent sessions for the same canonical working
  directory. Exiting restores the terminal and prints an exact resume command.
- Durable replay reconstructs messages, Thoughts, grouped tool activity,
  approvals, failures, cancellation, and completed work after reconnect.

## Screen and input

The screen has a single-row identity/runtime header, scrollable transcript,
temporary sheet area, expanding composer, and one reserved status row. The
composer grows to eight visible content rows and then scrolls independently. It
uses a visible prompt caret, side padding, a right-aligned model/effort/mode label,
and mode-colored borders without imposing a filled background.

The status row keeps `[connected]` on the right. Its left side shows shortcuts
while idle and becomes a short, subdued neutral activity rule during a run, with
the current activity, properly formatted elapsed time, and a truthful
`Esc interrupt` control.

The header's right edge presents a durable, human-readable session title and the
current activity (`Ready`, `Thinking`, `Exploring`, `Writing`, and related states).
The model may maintain the title through `session_title_set`, which emits a typed
`session.title.changed` event; `/title` uses the matching gateway endpoint.

Enter sends a message. Alt+Enter, Shift+Enter, or Ctrl+J inserts a new line.
Large pastes are shown as compact capsules while their exact bytes and ranges
remain durable in the user-message event. The transcript and composer both
render proportional scrollbars with solid thumbs when content exceeds their
viewport. Their tracks support mouse clicks and dragging.

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
- Centered modals use square, neutral-gray borders; semantic accents stay within
  their content. Selection inside a modal does not dismiss it. Trust and approval
  decisions use focused modals with inset actions. Approvals preserve the gateway's
  allow-for-session, allow-once, and deny semantics.
- `/memory` is a selectable active-record browser. The focused memory expands its
  full summary and value with independent detail scrolling; permanently deleted
  memories disappear from the browser.
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

Web search, vision input, the web application, and richer management editors are
outside this terminal slice. They remain future gateway-backed capabilities; the
TUI introduces no alternate provider or policy path for them.
