# Changelog

## Unreleased

Planned version: **v0.2.0**. These changes are in development and have not been released.

### Removed

- Flows recipes, their editor/history pages, `/flow`, and recipe execution APIs.
  Ordinary agent delegation and attached worker transcripts remain. Existing chats
  and historical events are preserved. Obsolete flow templates were removed.
- All Automation UI, scheduling runtime, APIs, notifications, and the automation creation
  tool. Historical database records remain inert for upgrade compatibility.
- The delegated-agent side panel, its top-bar button, transcript links, and private
  handoff controls. Normal chat, agent switching, and native delegation remain.

### Fixed

- Chat agent picker displays current slugs instead of legacy internal IDs.

- Sidebar width follows the remaining navigation tabs, removing unused horizontal space.


### Added

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
- The normal Web chat list excludes delegated worker sessions; delegation cards open their
  transcripts within the parent conversation.

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
