# M10 — Rich Control Surfaces: Ratatui and Web

The terminal slice is implemented and documented in [M10-TUI.md](M10-TUI.md).
The web foundation and first functional chat slice are now implemented; this
document remains the plan for approvals and the remaining rich-management views.

## Implemented foundation

The first web slice establishes the client and its trusted boundary without
claiming unfinished controls:

- the persistent gateway serves the API and web application on one loopback
  origin; `hames web` starts or verifies it, opens a one-time authenticated URL,
  and exits without owning the site's lifetime;
- a handcrafted SolidJS/Vite application provides responsive routes for Chat,
  Agents, Memory, Skills, Scars, Plugins, and Settings; Runs remains planned but
  is intentionally absent from navigation until it has useful gateway-backed
  content;
- the composable web-plugin boundary provides semantic icon packs and surface
  contributions for routes, the global icon rail, contextual sidebars, and
  conversation-node renderers, plus ordered left/right composer-control seats;
  the default icon pack uses Phosphor for the brand, agents, and memory and
  Tabler for the remaining controls, while the built-in areas form `hames.core`;
- the responsive shell uses a global icon rail, a contextual sidebar, and the
  selected surface; Chat lists only real resumable sessions scoped to the exact
  launch directory and routes a selection into the main area; the repository
  identity appears only in Chat rather than being repeated by every sidebar;
- loading, offline, expired-session, retry, reconnect, and empty states are
  explicit; an expired process-local browser session is not reported as an
  offline gateway;
- unfinished areas are honest, noninteractive route shells rather than local
  mock implementations;
- the production bundle is committed and packaged with the Python gateway, with
  no CDN, analytics, remote fonts, or separate frontend runtime;
- a bearer-authenticated request creates a one-time launch URL and exchanges it
  for an HttpOnly, SameSite browser session. Browser-authenticated API requests
  enforce exact Host, Origin, and CSRF checks while preserving native gateway
  SSE streaming and `Last-Event-ID`;
- CSP and defensive response headers apply to the browser entry points,
  application assets, bootstrap API, and web errors;
- selecting a real session reconstructs user, reasoning, assistant, and tool
  activity from durable gateway events, follows live assistant deltas, submits
  messages, and cancels the active run without a frontend-only transcript;
- dashboard polling preserves an unchanged session stream, replay and token
  updates are frame-batched, and live output does not repeatedly project the
  complete durable history;
- the chat frame, header, transcript viewport, composer, menus, and contribution
  seats are separate application components; the core plugin supplies the
  attachment affordance and live mode/model/reasoning controls through those
  seats;
- the model picker discovers configured providers and probes them concurrently;
  reasoning-capable model changes require an explicit supported-effort choice,
  one combined toolbar trigger displays both values and drills from a Model or
  Thinking root row into provider-grouped models or advertised effort levels;
- the composer uses a capped auto-growing textarea, Enter submission,
  Shift+Enter line breaks, and inner scrolling after eight lines; its circular
  send control continues to queue while a run is active;
- the Chat sidebar can create a real gateway session and route into its
  composer; it joins history after the first durable message rather than
  leaving an empty sidebar row;
- entering the bare Chat route starts fresh work automatically instead of
  rendering a passive session-selection state;
- pending approval and question events render in the transcript and resolve
  through CSRF-protected gateway mutations, including session-scoped approval,
  option notes, and custom answers;
- transcript prose renders sanitized GitHub-flavored Markdown without allowing
  remote images or executable/embed markup, and the reusable AGENT.md editor
  provides source and rendered-preview modes;
- shared Button, form-field, selection-row, settings-section, dialog, and avatar
  components keep interaction and accessibility behavior consistent across
  core surfaces;
- the Agents sidebar lists real capsules with their component-rendered avatars
  and routes directly into the responsive main-surface editor, which atomically
  edits display name, `AGENT.md` instructions, tool access, skill access, pinned
  skills, and avatar metadata without an intermediate overview;
- the Memory sidebar paginates real workspace-visible relationship, semantic,
  and episodic records through the gateway, separates those layers in one
  scrollable and collapsible directory, and routes into details for value,
  scope, confidence, status, timestamps, and provenance;
- the Skills sidebar lists the gateway's complete workspace-visible catalog,
  separates Hames-created, global `~/.agents`, and shipped built-in packages in
  collapsible groups, and renders each real procedure with its metadata, tools,
  requirements, scripts, package origin, and Markdown instructions;

The next vertical slice adds richer plans, tasks, and child-agent activity.
Those controls and the management capabilities listed below remain deferred.
The gateway protocol is 37 for the refined avatar schema and the agent editor's
workspace-aware capability catalog. The persistence layout remains unchanged.

## Goal

Design rich interfaces only after the Rust REPL and gateway semantics have proven
the harness. Build the heavily customized Ratatui client first or alongside the
web control surface. Both must operate Hames through the same gateway.

The web application must use the same gateway APIs and event semantics as the REPL. It must not contain an alternative hidden agent runtime.

## Product structure

Required top-level areas:

```text
Chat
Runs
Agents
Memory
Skills
Scars
Plugins
Settings
```

Existing inspector functionality becomes part of Runs/Inspect rather than being rewritten from scratch.

## 1. Chat

Implement:

- new/resume session;
- select project;
- select named agent;
- stream assistant output;
- visible tool activity;
- child-agent branch activity;
- cancel run;
- approval/deny UI;
- retry user message by creating a new branch, not mutating old events;
- clear current model/agent/project;
- links from message/model/tool to inspector detail.

Chat transcript is rendered from session/events, not separate frontend-only message database.

## 2. Run inspection

Preserve/extend M04:

- timeline;
- branch tree;
- model request detail;
- context manifests;
- provider usage;
- tools;
- policy;
- approvals;
- agent attribution;
- memory retrieval;
- skills loaded;
- Scars triggered;
- plugin calls.

A user can click from final answer to model calls and sources that produced it.

## 3. Agents

Implement:

- list agents; **Implemented**
- persist a component-rendered avatar (five shapes, three eye styles, optional
  face plate, and custom color) as validated `AGENT.md` metadata; **Implemented**
- create agent;
- edit `AGENT.md` instructions through a dedicated settings editor;
  **Implemented**
- schema/frontmatter validation before save; **Implemented for structured agent
  edits**
- show and edit effective tool and skill access; **Implemented**
- show effective policy/model/memory scopes;
- show project vs global origin;
- show agent usage statistics;
- retire/delete agent while preserving history.

Saving agent writes actual `AGENT.md` atomically.

Do not move agent identity exclusively into database.

## 4. Memory

Implement management for all M06 layers.

Semantic:

- search;
- view; **Implemented**
- provenance; **Implemented**
- confidence/status; **Implemented**
- correct/supersede/retract/delete;
- approve/reject proposals.

Relationships:

- entity page; **Initial record detail implemented**
- incoming/outgoing relationships;
- bounded relationship view;
- create/correct/retract/delete relationship.

Operational:

- active/blocked/waiting/completed work;
- update status/owner/next action;
- project filtering.

Episodic:

- search episodes; **Grouped browsing implemented; search remains deferred**
- open linked session timeline.

Memory detail shows why a record was retrieved for selected model request when retrieval events exist.

## 5. Skills

Implement:

- catalog; **Implemented for active Hames-created, portable, and built-in Skills**
- active/stale/archive status;
- full `SKILL.md`; **Implemented for the active visible version**
- version history;
- usage/outcomes;
- proposal queue;
- diff view;
- validation results;
- approve/reject;
- archive/restore;
- evidence links.

Approval calls backend promotion logic, not browser-side file writes.

## 6. Scars / Evolution

Implement:

- Scar list by state/severity/project; **Implemented for visible workspace Scars**
- candidate confirmation/dismissal/deletion;
- evidence timeline; **Implemented**
- failure signature; **Implemented**
- trigger explanation; **Implemented**
- repair routing; **Implemented as an inspection view**
- proposed repair diff; **Implemented as structured proposal detail**
- evaluation/replay result; **Implemented for recorded evaluations**
- approve/reject when required;
- guarded success count; **Implemented**
- healed/regressed history. **Implemented through lifecycle transitions**

This screen should make self-correction understandable to a human.

## 7. Plugins

Implement:

- installed/disabled plugins;
- permissions;
- worker/sandbox status;
- version;
- capabilities;
- broker activity;
- install local package;
- explicit permission approval;
- update permission diff;
- enable/disable/remove;
- agent-authored plugin proposal review.

Unsafe unsandboxed state, if configuration permits it, must be visually obvious.

## 8. Settings

Expose only safe, meaningful settings:

- gateway local settings;
- provider definitions without displaying secret values;
- default model;
- evolution budget;
- memory auto-accept policy;
- Scar healing threshold;
- context budgets;
- trusted projects;
- plugin unsafe-execution policy.

Secrets are set through secure backend input/environment mechanisms and never returned in plaintext after storage.

## 9. Approval UX

Consistent approval component shows:

- requesting agent;
- exact operation;
- arguments/diff;
- project;
- plugin if applicable;
- reason;
- risk class;
- allow once;
- deny.

Do not add broad “always allow everything” shortcuts.

Persistent policy grants, if supported, are separate explicit actions that edit policy and show scope.

## 10. Local web security

Because UI can execute powerful actions:

- loopback bind by default;
- same-origin requests;
- CSRF-safe mutation design;
- local bearer/session token where appropriate;
- Content Security Policy;
- no third-party analytics;
- no remote script/CDN dependency in production build;
- provider secrets never sent to frontend;
- dangerous HTML/tool output escaped/sanitized.

## 11. Accessibility and resilience

At minimum:

- keyboard accessible primary actions;
- semantic labels;
- readable without animation;
- error states;
- reconnect state;
- SSE reconnection without duplicate events;
- session correct after browser refresh;
- UI never assumes in-flight request succeeded merely because connection dropped.

## Backend API completion

Add versioned endpoints required for:

- agents;
- memory;
- work items;
- skills/proposals;
- Scars/repairs/evaluations;
- plugins;
- settings;
- approvals.

Mutations use optimistic concurrency/version hashes where concurrent edits matter.

## Tests

Frontend:

- route/page rendering;
- chat streaming;
- approval;
- agent edit validation;
- memory correction;
- skill proposal diff/approval;
- Scar evidence/repair;
- plugin permission screen;
- reconnect;
- XSS/sanitization fixture.

Backend:

- authorization/local token behavior;
- optimistic concurrency;
- secrets never returned;
- all mutations emit expected ledger events.

E2E with fake provider:

1. create project/session;
2. chat;
3. execute tool;
4. approve action;
5. create/select agent;
6. inspect memory;
7. approve skill proposal;
8. inspect Scar;
9. enable test plugin;
10. refresh browser and reconstruct state.

## Manual smoke test

Use web UI for an entire small coding task without REPL.

Then open run inspector and verify every meaningful action can be traced.

Repeat one workflow that triggers skill proposal and one correction that creates Scar. Review both entirely through web UI.

## Commit expectations

This milestone is UI-heavy; commit by vertical capability, not “backend then frontend for everything.”

Suggested slices:

1. chat + approvals;
2. agents;
3. memory;
4. skills;
5. Scars/evolution;
6. plugins;
7. settings/security;
8. comprehensive e2e/accessibility/docs.

## Acceptance gate

M10 is complete when web application can operate every v0.1 Hames subsystem through gateway without alternate hidden state/runtime logic, and a user can move from chat to exact provenance/evolution details in a few clicks.

Finish clean and tag `m10`.
