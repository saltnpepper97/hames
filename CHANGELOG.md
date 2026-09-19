# Changelog

## Unreleased

Planned version: **v0.2.0**. These changes are in development and have not been released.

### Added

- A SolidJS Web UI served by the local gateway, with live conversations, workspace
  navigation, agent editing, settings, memory, skills, Scar lineage, and plugin management.
- Chat controls for agent, model, reasoning effort, and mode; attachments, queued
  messages, tool activity, approvals, questions, usage details, and searchable event inspection.
- A plan review flow with explicit execution and revision actions, separate implementation
  chats, delegation links, and durable tracking of approved plans.
- Provider connections for DeepSeek, Z.ai API and Coding Plan, xAI Grok API, and
  OpenAI API, alongside existing Codex and Grok Build integrations. Web and TUI connection
  controls, automatic connection status, and provider model discovery.
- Scheduled automations with a calendar-clock navigation tab, guided setup, timezone-aware
  schedules, review before enabling, pause, Run now, run history, and a chat per attempt.
  Project workspaces are optional for automations. Includes sleep catch-up, overlap prevention,
  bounded retries, restart reconciliation, and native desktop or browser notifications.
- Configurable personal and workspace slash commands, with a documented build-review
  example instead of a setup-specific built-in workflow.
- Agent model defaults, a model step during creation, additional avatar shapes including
  a water drop, and more expressive eye and visor movement.
- Rolling annual token activity with month labels, broader usage presentation, and
  updated visual-verification guidance for launching apps and capturing compositor output,
  including Halley.

### Changed

- Chat composer shows one additional line by default while retaining automatic growth.

- Installation defaults to the latest stable version tag rather than rolling `main`.
  `HAMES_VERSION` pins a tag; `HAMES_REF` remains a tag-only compatibility alias.
  Building the current checkout requires `HAMES_INSTALL_LOCAL=1`.
- Refined Web navigation, dark appearance, forms, dialogs, dropdown hover and selection
  feedback, connection labels, status colors, branding, and tab icon.
- README logo now uses the tab icon’s rounded-square sage background at a smaller size.
  Updated the overview and quick start for Web and terminal use, highlighting automations,
  provider connections, planning, and delegation, with explicit unreleased version guidance.
- Improved event table layout and resizing, wide Markdown table scrolling, composer
  menus, workspace and agent sidebar labels, and responsive chat layout.
- Expanded architecture, provider, plugin, command, automation, and workflow documentation.
- Updated builder and finisher workflow guidance to commit verified work and report
  the actual execution models.

### Fixed

- Resolve renamed agent slugs through the registry during execution and delegation,
  preserving stable IDs instead of constructing nonexistent folders from display slugs.
  Agent renames move their capsule folders to the new slug while existing session IDs
  remain valid; the built-in default agent retains its reserved folder.

- Added proper padding to the automation task input and removed its resize handle.

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
  plans, delegation, commands, attachments, workspaces, automations, and UI behavior.
- Exercised automation creation, isolated execution, run history, pause, optional
  workspaces, and desktop notification availability through runtime and browser checks.
