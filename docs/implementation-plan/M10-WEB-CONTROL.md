# M10 — Full Web Control Surface

## Goal

Turn the early read-only Web Inspector into the primary rich interface for operating Hames.

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

- list agents;
- create agent;
- edit `AGENT.md` through text editor;
- schema/frontmatter validation before save;
- show effective tools/policy/model/memory scopes;
- show project vs global origin;
- show agent usage statistics;
- retire/delete agent while preserving history.

Saving agent writes actual `AGENT.md` atomically.

Do not move agent identity exclusively into database.

## 4. Memory

Implement management for all M06 layers.

Semantic:

- search;
- view;
- provenance;
- confidence/status;
- correct/supersede/retract;
- approve/reject proposals.

Relationships:

- entity page;
- incoming/outgoing relationships;
- bounded relationship view;
- create/correct/retract relationship.

Operational:

- active/blocked/waiting/completed work;
- update status/owner/next action;
- project filtering.

Episodic:

- search episodes;
- open linked session timeline.

Memory detail shows why a record was retrieved for selected model request when retrieval events exist.

## 5. Skills

Implement:

- catalog;
- active/stale/archive status;
- full `SKILL.md`;
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

- Scar list by state/severity/project;
- candidate confirmation/dismissal;
- evidence timeline;
- failure signature;
- trigger explanation;
- repair routing;
- proposed repair diff;
- evaluation/replay result;
- approve/reject when required;
- guarded success count;
- healed/regressed history.

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
