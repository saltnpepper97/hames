# M04 — Context Compiler, Context Manifest, Usage Accounting, and Web Inspector

## Goal

Make Hames explainable while it is still young.

M04 replaces ad-hoc prompt assembly with one context compiler and builds the first web interface as a **read-only inspector** over real sessions, model calls, tools, policy decisions, and context composition.

The inspector is not yet the primary chat UI.

## Context compiler

All model requests must pass through one compiler.

A context source returns candidates with:

```text
source_type
source_id
content
priority
estimated_tokens
scope
confidence nullable
provenance_event_ids
cache_class
reason
```

Initial source types:

```text
core.system
agent.instructions
project.instructions
conversation
tool.result
policy.summary
```

Memory, skills, and Scars add new source types in later milestones without bypassing the compiler.

## Context budget

Define configurable budgets:

```text
max_context_tokens
reserved_output_tokens
reserved_tool_tokens
```

The compiler must:

1. preserve mandatory core/system material;
2. include tool schemas required for the run;
3. preserve recent conversation;
4. include other candidates by deterministic priority/budget rules;
5. compact or omit older content using explicit policies;
6. produce the final provider request;
7. emit a manifest explaining every included component.

Do not rely on provider “context too long” errors as budget enforcement.

## Conversation compaction

Implement deterministic first-stage compaction:

- recent turns retained verbatim under budget;
- old tool output may be replaced by the stable summary already emitted by the tool result;
- old assistant/user messages are not silently paraphrased by an LLM in this milestone.

If history still cannot fit, return a clear context-capacity error. Any future model-authored compaction must be its own traceable context source and model call.

## Token estimation

Create a `TokenEstimator` interface.

Requirements:

- provider-reported usage remains authoritative for aggregate totals;
- internal source attribution is explicitly estimated;
- use provider/model-aware tokenizer when available;
- use documented fallback estimator otherwise;
- UI always labels internal category values as estimates.

## Context manifest

For every model request emit:

```text
context.compiled
```

Manifest contains:

```text
model_request_id
context_limit
reserved_output
estimated_total
components[]
dropped_candidates[]
```

Each component contains:

```text
source_type
source_id
estimated_tokens
priority
included
reason
provenance
content_hash
```

Sensitive content may expose only redacted previews.

This event is a core invariant: material model influence must be attributable.

## Usage projection

Derive from ledger events:

- model calls;
- provider input/output/cached/reasoning usage when reported;
- per-session totals;
- per-run totals;
- tool duration;
- wall-clock duration;
- model turn count;
- estimated context category shares.

Do not create a second unrelated telemetry truth store.

## Web Inspector v1

Create the TypeScript/React/Vite web project.

The gateway serves the built web assets in development/production-compatible form.

### Sessions

Show:

- title/ID;
- created time;
- status;
- project;
- model call count;
- aggregate provider tokens;
- duration.

### Session timeline

Show at least:

- user message;
- model call;
- assistant response;
- tool request/result;
- policy decision;
- approval;
- errors.

Causal/nested relationships should be visually distinguishable even before child agents exist.

### Model request detail

Show:

- model/provider;
- timing;
- provider-reported usage;
- context manifest;
- estimated breakdown by category;
- included sources;
- dropped candidates;
- hashes/provenance links.

### Tool detail

Show:

- requested arguments with redaction;
- policy decision;
- start/end;
- result summary;
- output/blob reference;
- failure details.

### Usage

Show exact provider-reported aggregate separately from approximate context attribution.

Never present estimated category tokens as billing truth.

## Live updates

The inspector subscribes to SSE and updates an open session timeline live.

Reconnect using event sequence without duplicating rows.

Read-only means the inspector cannot yet send chat messages or approvals.

## Tests

Backend:

- deterministic context ordering;
- budget boundary behavior;
- dropped-candidate manifest;
- token estimator fallback;
- provider-reported usage aggregation;
- no double counting after SSE reconnect;
- context manifest stored for every model request.

Frontend:

- timeline rendering;
- usage exact-vs-estimated labels;
- context component drill-down;
- SSE reconnect/deduplication;
- redacted data display.

End-to-end:

- fake-provider run appears in Web Inspector;
- tool call and policy decision are visible;
- clicking model request shows exact manifest used.

## Manual smoke test

Run a real REPL task while inspector is open.

Verify live:

1. user message appears;
2. model request appears;
3. tool and policy events appear;
4. response finishes;
5. usage totals update;
6. context manifest explains request;
7. browser refresh reconstructs same view from durable events.

## Commit expectations

Suggested slices:

1. context source/compiler contracts;
2. token budgeting and manifests;
3. usage projection;
4. web project + sessions/timeline;
5. request/tool detail;
6. live SSE + frontend/e2e tests.

## Acceptance gate

M04 is complete when every model request is produced by the context compiler, every request has an inspectable manifest, exact provider usage is clearly separated from internal estimates, and a browser can reconstruct and live-follow real Hames sessions.

Finish clean and tag `m04`.
