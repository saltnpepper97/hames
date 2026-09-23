# Changelog

## Unreleased

Planned version: **v0.2.0**. These changes are in development and have not been released.

### Removed

- Playwright-specific MCP approval handling and built-in guidance preferences.
  Browser automation is no longer integrated internally; generic external MCP
  support remains available.

- Deferred the experimental built-in browser: removed its chat panel, gateway APIs,
  agent tool, prototype, obsolete implementation plans, and Python Playwright
  dependency. Existing external browser MCP
  connections and historical browser events remain compatible.

- Flows recipes, their editor/history pages, `/flow`, and recipe execution APIs.
  Ordinary agent delegation and attached worker transcripts remain. Existing chats
  and historical events are preserved. Obsolete flow templates were removed.
- All Automation UI, scheduling runtime, APIs, notifications, and the automation creation
  tool. Historical database records remain inert for upgrade compatibility.
- The delegated-agent side panel, its top-bar button, transcript links, and private
  handoff controls. Normal chat, agent switching, and native delegation remain.

### Fixed

- Sending a message from a scrolled-up Web chat now scrolls smoothly to that
  submitted message once it appears. Stopping a run before any model output returns its message
  and attachments to the composer, removes it from the transcript, and excludes
  it from future model context.

- Stopping or steering a lead leaves its workers running. A stopped worker reports
  upward without stopping siblings; steering a worker keeps its original assignment
  attached to the lead until the replacement turn finishes. Late worker results
  remain paired with their original tool calls.
- Dynamic plans, checklists, delegated assignments, and worker state no longer consume
  the fixed-instruction category allowance. The overall model input budget remains
  enforced, and worker reports now use the intended bounded prompt representation.

- Codex receives safe aliases for Hames MCP tools instead of its reserved `mcp__`
  names. Calls map back to their original tool identities, with collision handling
  and length limits applied at the provider boundary.

- Agent identity is independent of names: newly created agents receive opaque UUID
  IDs, while Web/TUI editors require no slug. Name lookups, dispatcher reports, and
  client labels use current registry names; existing IDs and aliases remain valid.
  Web agent routes stay stable across renames.

- Renaming an agent preserves its definition path and historical lookup aliases.
  Saved delegation references use immutable IDs and displayed identities resolve
  current names, so in-flight handoffs survive renames.

- Worker chats appear in the Web sidebar again, with named transcripts linked from
  delegation activity as soon as the worker session is created.
- Long question choices wrap inside their cards instead of overflowing.
- Embedded Codex instructions distinguish authorized Hames delegation from disabled
  native Codex delegation, avoiding redundant permission requests for worker handoffs.
- Codex exposes the Hames dispatcher as `hames_spawn_agent` to avoid collision with
  native `spawn_agent`; worker reports remain intact in the dynamic tool response.

- Optional `delegation.coordinator_only` enforces a coordinating agent's local
  read/dispatch scope while preserving workers' configured execution tools and models.
  Direct mutation attempts are rejected with instructions to delegate.

- Consecutive successful reads of the same file share one expandable Web transcript
  entry, including simple sed, cat, head, and tail commands. Individual commands and
  outputs remain available. Read guidance favors one bounded read for adjacent sections.

- Grok usage survives transient refresh failures with an explicit last-known-data
  notice. Web usage refreshes every minute and on window focus so new reset windows
  appear without reopening the page. TUI usage now includes Grok percentages and
  reset countdowns, including zero usage after a reset.

- Auto mode no longer asks for recursive cleanup of explicit project/scratch
  subpaths. Broad deletions and uncertain targets still require confirmation;
  Manual and Plan restrictions remain unchanged.

- Current agent identity and delegation availability are explicit in model context,
  preventing previous assistant workflow reports from defining a newly selected agent's role.
  Delegation rejections explain how to continue authorized work directly.
- Sidebar chat title scrolling stops when the pointer leaves. Clicking a chat no
  longer keeps hover actions visible; keyboard navigation still exposes the controls.

- Agent/provider switching and plan execution can replay chats with interrupted tool
  calls from ended runs. Missing results are explicitly marked unavailable in model
  context, preserving recorded results and the original transcript.

- Switching a chat's agent applies its default provider, model, reasoning effort,
  and context window even after the conversation has started. Agents without defaults
  preserve the chat's current model selection.

- Chat agent picker displays current slugs instead of legacy internal IDs.

- Sidebar width follows the remaining navigation tabs, removing unused horizontal space.


### Added

- Xiaomi MiMo API and Token Plan connections across Web, TUI, and CLI, with model
  discovery, context-window metadata, tool calls, and model-specific thinking controls.
- `agent_control` for inspecting and waiting on existing workers. Stopping workers
  through this tool requires an explicit instruction from the current user turn.
- `runtime.max_active_seconds_per_run = 0` disables the active-work timeout for
  long-running leads and workers; other execution limits remain in effect.

- Atomic batch edits to one file through `edit_file`: related replacements can share one call
  and one combined diff; a failed replacement leaves the entire file unchanged.

- A SolidJS Web UI served by the local gateway, with live conversations, workspace
  navigation, agent editing, settings, memory, skills, Scar lineage, and plugin management.
- Chat controls for agent, model, reasoning effort, and mode; attachments, queued
  messages, tool activity, approvals, questions, usage details, and searchable event inspection.
- A plan review flow with explicit execution and revision actions, separate implementation
  chats, delegation links, and durable tracking of approved plans.
- Provider connections for DeepSeek, Z.ai API and Coding Plan, xAI Grok API, and
  OpenAI API, alongside existing Codex and Grok Build integrations. Web and TUI connection
  controls, automatic connection status, and provider model discovery.
- Configurable personal and workspace slash commands, with a generic approved-plan execution
  example.
- Durable coordinator-built workflow graphs for delegated stages, with dependencies, attempt
  history, automatic evidence handoff, exact child failure details, and child-chat follow-ups.
- A bounded read-only Git inspection tool for reviewers to inspect status, commits, and diffs
  without shell access.
- Agent model defaults, a model step during creation, additional avatar shapes including
  a water drop, and more expressive eye and visor movement.
- Rolling annual token activity with month labels, broader usage presentation, and
  updated visual-verification guidance for launching apps and capturing compositor output,
  including Halley.

### Changed

- Removed routine started/queued success banners and persistent plan-attention badges
  from the chat input. Resume remains available in the composer action menu; ready plans
  retain explicit approval controls, and failed sends keep their error and draft.

- Consecutive successful edits to the same file appear in one expandable transcript row,
  preserving individual diffs and boundaries between runs, messages, and other actions.
- Delegated worker chats remain accessible from the Web sidebar and delegation
  activity, including while work is still in progress.

- Chat composer shows one additional line by default while retaining automatic growth.

- Installation defaults to the latest stable version tag rather than rolling `main`.
  `HAMES_VERSION` pins a tag; `HAMES_REF` remains a tag-only compatibility alias.
  Building the current checkout requires `HAMES_INSTALL_LOCAL=1`.
- Refined Web navigation, dark appearance, forms, dialogs, dropdown hover and selection
  feedback, connection labels, status colors, branding, and tab icon.
- README logo now uses the tab icon’s rounded-square sage background at a smaller size.
  Updated the overview and quick start for Web and terminal use, highlighting agent configuration,
  provider connections, planning, and delegation, with explicit unreleased version guidance.
- Improved event table layout and resizing, wide Markdown table scrolling, composer
  menus, workspace and agent sidebar labels, and responsive chat layout.
- Expanded architecture, provider, plugin, command and delegation documentation.
- Updated builder and finisher workflow guidance to commit verified work and report
  the actual execution models.

### Fixed

- Split-pane chat headers adapt to the available pane width without overlapping controls.
  Worker message inputs share the normal round Send button and borderless focus styling.

- The worker drawer keeps the latest flow available after completion and ordinary
  follow-up messages, resetting only when another flow starts. Its trigger waits for
  a real worker transcript, and session polling no longer closes the drawer.
- The task list remembers its expanded state per chat across navigation and reloads,
  with the disclosure arrow direction corrected.

- Waiting for approvals or answers no longer consumes the run's active-time budget.
  Pending approvals are cancelled when a run ends and reconciled after a gateway restart,
  preventing permission cards that cannot be answered because their run has expired.
- Delegation labels use configured agent names rather than internal persistence IDs.

- Preserve the exact approved plan when respawning workers after a failed execution;
  reject plan-based delegation before starting a child if the plan is missing.
- Preserve interrupted plan executions as a resumable needs-attention state, including
  the exact failure and blocker reasons. Resuming keeps completed checklist work and the
  approved execution note instead of rebuilding the plan from a phrase-matched chat turn.
- Reconcile a failed or needs-attention plan when a later successful turn resolves its
  remaining checklist, without allowing unrelated turns or empty checklists to mark it complete.
- Exclude unrelated episodic task reports from delegated worker context so historical
  assignments cannot substitute for the current plan.
- Accept memory extraction tool calls with nonzero indexes and multiple submissions.
  Background maintenance warnings explicitly leave the conversation outcome unchanged.

- Resolve renamed agent slugs through the registry during execution and delegation,
  preserving stable IDs instead of constructing nonexistent folders from display slugs.
  Agent renames move their capsule folders to the new slug while existing session IDs
  remain valid; the built-in default agent retains its reserved folder. Delegating agents
  now see the current slug in their permitted targets and child results instead of a stale ID.


- Long-running conversation context recovery, including active-turn input budget
  exhaustion, context compaction, and provider-specific recovery behavior.
- Streaming transcript assembly and reconciliation when switching chats or pages,
  scroll-position restoration, follow-at-bottom behavior, and interference with manual scrolling.
- Approved-plan handoff continuity, avoiding lossy rewriting or truncation of the plan
  passed to implementation agents.
- Delegated run cancellation and parent notification, execution-time accounting while
  awaiting children, and plan completion/continuation status reconciliation.
- Initial model loading, agent renaming and slugs, misleading connection state, and
  redundant or persistent chat status messages.
- Avatar eye distortion, gaze timing, visor movement, and shape alignment.
- Installer updates preserve dirty checkouts and reject branch or commit references.

### Verification

- Expanded Python, Rust, and Web regression coverage for context recovery, providers,
  plans, delegation, commands, attachments, workspaces and UI behavior.
