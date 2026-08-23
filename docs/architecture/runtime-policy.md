# Runtime and policy boundary

The Python runtime is the only component allowed to execute model-requested work.
Clients send messages and decisions through protocol v8; provider adapters only
translate normalized messages and streams. Neither the Rust REPL nor a provider
adapter directly reads files, writes files, or starts commands.

## Bounded loop

Each user message receives independent model-turn, tool-call, and active-time
budgets. Provider and tool execution consume active time; waiting for a human
approval does not. Tool validation errors, ordinary execution failures, policy
denials, and human denials become structured tool results so the model can explain
or recover. Limit exhaustion and provider/runtime failures produce typed
`run.failed` terminals. Cancellation kills an active shell process group before
recording `run.cancelled`.

## Workspaces and tools

Every core tool chooses `project` or `scratch`; it cannot supply an arbitrary
working directory. Project resolves to the session's exact canonical launch
directory. Scratch is created lazily at
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
submits the hash with an approve-once or deny decision. A mutation or replay cannot
reuse it. Pending approvals are cancelled with their run on gateway shutdown.

The shell classifier is deliberately a policy gate rather than a containment
sandbox. Bash is expressive enough to obscure filesystem and network behavior;
strong isolation belongs in a later hardening milestone and must not be implied by
the M03 checks.
