# M03 — Single-Agent Runtime, Core Tools, Policy Gate, and Bare REPL

## Goal

Produce the first genuinely usable Hames.

At the end of M03, a user can start the bare REPL, talk to one default agent, let it call core coding tools, approve or deny sensitive actions, and receive a final response. Every model/tool/policy action is recorded.

This milestone deliberately does **not** build a rich TUI.

## Agent loop

Implement one clear loop:

1. receive user message;
2. build minimal context;
3. call provider;
4. stream assistant output/tool calls;
5. validate requested tool;
6. obtain policy decision;
7. execute or reject tool;
8. append normalized result;
9. continue model;
10. finish or hit bounded limits.

Required limits:

```text
max_model_turns_per_user_message
max_tool_calls_per_run
max_wall_clock_seconds
```

Limit exhaustion returns a typed final failure rather than looping forever.

## Core tool schema

All tools implement one typed interface:

```text
name
description
input_schema
side_effect_class
execute(context, args)
```

Tool results contain:

```text
status
summary
content
structured_data optional
truncated
artifact/blob references
duration
```

Large results use the blob store.

## Core tools

### `read_file`

- path relative to project root by default;
- optional line range;
- binary detection;
- bounded output;
- clear missing/permission errors.

### `list_dir`

- bounded entries;
- file type;
- relative paths;
- hidden file handling.

### `write_file`

- full replacement/create;
- atomic write;
- parent creation only under explicit option/policy;
- emits before/after hashes.

### `edit_file`

Use exact text replacement or an equally deterministic patch representation.

Requirements:

- rejects ambiguous match;
- rejects zero match;
- atomic;
- reports diff;
- before/after hashes.

Do not start with a fuzzy model-controlled patch engine.

### `shell`

- executes with explicit command semantics;
- fixed working directory under trusted project;
- captures stdout/stderr separately;
- timeout;
- process-group cancellation;
- bounded output with blob spill;
- exit code and duration.

The policy gate inspects shell requests before execution.

## Project trust

Introduce a project object:

```text
project_id
root_path
trusted
created_at
```

CLI:

```bash
hames project trust /path/to/project
hames project list
```

The agent cannot write or execute within an untrusted project.

Read-only inspection of an untrusted project may require confirmation or be denied according to one documented default rule.

## Policy gate

Every side-effecting tool call passes through one service.

Policy decision states:

```text
allow
deny
require_confirmation
```

Initial default policy:

- read/list inside trusted project: allow;
- write/edit inside trusted project: allow;
- ordinary project shell operations: allow;
- access outside trusted roots: confirm or deny according to path class;
- destructive/high-risk command signatures: require confirmation;
- known secret paths: deny unless explicitly whitelisted;
- privilege escalation: confirm or deny by default;
- Hames config/state mutation through generic shell/file tools: deny.

Policy rules must be deterministic code/config, not an LLM prompt.

### Shell risk classifier

Implement a conservative deterministic classifier for obvious dangerous patterns such as:

- recursive deletion;
- filesystem formatting;
- raw disk writes;
- destructive Git operations;
- privilege escalation;
- shutdown/reboot;
- broad process killing.

Do not claim this is a complete shell sandbox. Policy is a gate, not magical command interpretation.

## Approval protocol

When confirmation is required:

- emit approval request event with exact tool, arguments, agent, project, and reason;
- pause that tool call;
- client can approve once or deny;
- approval is tied to exact request hash;
- modified arguments require new approval.

Gateway route:

```text
POST /v1/approvals/{id}
```

Approvals emit durable events.

## Tool events

Add:

```text
tool.requested
tool.started
tool.completed
tool.failed
tool.rejected
policy.requested
policy.decided
approval.requested
approval.resolved
run.started
run.completed
run.failed
```

## Bare REPL

Add:

```bash
hames
hames chat
hames chat --project /path
```

The REPL is intentionally simple stdin/stdout.

Required commands:

```text
/help
/session
/new
/project
/model
/usage
/cancel
/quit
```

Display:

- streamed assistant text;
- concise tool start/finish lines;
- approval prompt with exact action;
- typed errors.

No panels, mouse handling, terminal graphics framework, or dashboard.

The REPL talks to the gateway contract. It may auto-start a local gateway child process for convenience, but must still use the same HTTP/SSE APIs as an external client.

## Minimal context in M03

Until M04, context includes only:

- core system contract;
- recent session conversation;
- current project root and policy summary;
- tool schemas.

Do not build hidden memory or skill injection yet.

## Tests

Cover:

- ordinary text-only conversation;
- one tool call and continuation;
- multiple sequential tool calls;
- model loop limit;
- tool call limit;
- read/write/edit correctness;
- ambiguous edit rejection;
- shell timeout/cancellation;
- path traversal rejection;
- untrusted project behavior;
- approval request hashing;
- approval once;
- denied action returned to model as structured rejection;
- destructive command confirmation;
- Hames state/config self-protection;
- tool result truncation/blob spill;
- deterministic REPL run using fake provider;
- migration from M02.

## Manual smoke test

In a temporary trusted Git repository:

1. start Hames REPL;
2. ask it to inspect a source file;
3. ask it to make a safe edit;
4. ask it to run tests;
5. ask for a destructive command and verify approval is required;
6. deny it;
7. verify session history contains exact request and denial;
8. exit and resume session.

## Commit expectations

Suggested slices:

1. run/loop state machine;
2. tool interface + read/list;
3. write/edit;
4. shell execution;
5. policy/approval system;
6. REPL client and smoke coverage.

## Acceptance gate

M03 is complete when Hames can safely perform a real small coding task from the REPL with a trusted project, all actions are observable in the event ledger, approvals are exact and durable, loop limits work, and the full offline test suite passes.

Finish with clean Git state and tag `m03`.
