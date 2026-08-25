# Runtime and policy boundary

The Python runtime is the only component allowed to execute model-requested work.
Clients send messages and decisions through protocol v28; provider adapters only
translate normalized messages and streams. Neither the Rust REPL nor a provider
adapter directly reads files, writes files, or starts commands.

Every protocol-v28 message admission carries a client-generated UUID. The gateway
stores a durable receipt keyed by session and submission ID before accepting the
message. Retrying the same payload returns the original run or queue result;
reusing that ID with different content is rejected. This makes a lost HTTP
response recoverable without creating a second user turn.

## Bounded loop

Each user message receives independent model-turn, tool-call, and active-time
budgets. Provider and tool execution consume active time; waiting for a human
approval does not. Tool validation errors, ordinary execution failures, policy
denials, and human denials become structured tool results so the model can explain
or recover. Limit exhaustion and provider/runtime failures produce typed
`run.failed` terminals. Cancellation kills an active shell process group before
recording `run.cancelled`.

Durable goals supervise a sequence of these independently bounded runs. They do
not weaken or combine the per-run budgets. See [goals.md](goals.md).

## Workspaces and tools

Filesystem tools choose `project`, `scratch`, or confirmed `home`; they cannot
supply an arbitrary working directory. A leading `~/` is normalized to the home
workspace before policy evaluation. Project resolves to the session's exact
canonical launch directory. Scratch is created lazily at
`/tmp/hames/runs/<run-id>/<agent-id>/workspace` and is never a deliverable.

File operations reject absolute paths, parent traversal, and symlink escape.
Writes and exact single-match edits use a private temporary file, `fsync`, and an
atomic replacement. Shell uses `/bin/bash -lc`, a fixed workspace, separate
bounded stdout/stderr capture, a timeout, and process-group cancellation. Large
results use the existing content-addressed blob store while the model receives a
bounded preview. The child environment omits variables whose names identify
tokens, passwords, authentication, credentials, cookies, or API keys.

## Trust, classification, and approvals

A session cannot start a run until its exact project root has a persisted trust
grant. Inside a trusted root, normal reads, writes, edits, and ordinary shell
commands run without per-call confirmation. Deterministic rules deny Hames state,
known credential/secret paths, raw-device operations, and path escape. Obvious
destructive commands require confirmation.
Obvious absolute paths outside the selected workspace and parent-directory
traversal in shell strings also require confirmation.

An approval request hashes canonical tool arguments together with run, session,
agent, and working-directory identity. The REPL displays that exact request and
submits the hash with an allow-once or deny decision. Manual-mode state changes
also offer allow-for-session. That grant is scoped to the exact session and tool
name; built-in dangerous-operation checks still run and can require a fresh
one-shot approval. A mutation or replay cannot reuse an exact approval. Pending
approvals are cancelled with their run on gateway shutdown.

## Execution modes

The gateway persists and enforces the mode for each session. Clients only select
and display it, so the REPL, TUI, and web client share identical semantics:

- `manual` confirms state-changing tools. The user may allow the exact action,
  allow that tool for the session, or deny it.
- `auto` runs ordinary trusted work automatically and confirms only dangerous
  or out-of-workspace actions.
- `plan` permits inspection and a narrow set of test/check shell commands, but
  denies code writes, delegation, plugin tools, and durable memory/Scar/Skill
  mutation.

Mode policy is included in the model's compiled system context and enforced again
at each tool call. A client cannot weaken it by omitting mode UI.

The shell classifier is deliberately a policy gate rather than a containment
sandbox. Bash is expressive enough to obscure filesystem and network behavior;
strong isolation belongs in a later hardening milestone and must not be implied by
the M03 checks.

## Skill script isolation

The core shell policy above remains the boundary for ordinary agent-selected Bash.
Self-authored Skill scripts are narrower: validation and execution require
Bubblewrap, use fresh user/network/process namespaces, hide the real home, mount
the immutable package read-only, and allow writes only in disposable run scratch.
Normal execution sees the project read-only at `/project`; validation self-tests do
not receive a project mount at all. If isolation is unavailable, Hames rejects the
script instead of falling back to host execution.
