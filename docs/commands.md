# User-defined slash commands

Custom commands are local configuration, not Hames built-ins. Web, TUI, and REPL
read the same gateway catalog. No command implies a particular provider or model.

## Maintain your commands

Put one TOML file per command in `~/.hames/commands/`.
Its filename supplies the slash name: `build-review.toml` becomes `/build-review`.

Your build/review command is:

```toml
description = "Execute the approved plan with my build/review coordinator"
action = "execute_plan"
agent = "build-review"
```

- Change the filename to rename the command.
- Change `description` to update its menu text.
- Change `agent` to choose a coordinator, using its stable ID or current slug.
- Delete the file to remove the command.
- Put a file with the same name in `<workspace>/.hames/commands/` to override it
  for that workspace only. An invalid override reports an error; it never silently
  executes the global version.
- Built-in names such as `plan`, `model`, `help`, and `stop` are reserved.

The first supported action is `execute_plan`. It explicitly approves the current
ready plan and runs it with the configured coordinator. It accepts an optional
execution note: `/build-review preserve the public API`. It rejects missing or
non-ready plans, missing agents, and active conflicting work. It does not execute
shell text from the TOML file. User-invoked skills continue to appear as their own
slash commands; a custom command takes precedence over a skill with the same name.

Definitions are reread at execution, so edits need no gateway restart. Reopen the
command menu in TUI, or reload Web, to refresh its suggestions. REPL `/help` lists
current custom commands. Switching workspace changes the workspace overrides.

## Maintain the build/review workflow

The command only chooses an agent. Edit the coordinator's AGENT.md in
`~/.hames/agents/build-review/AGENT.md` to change stage order, worker targets,
review verdicts, or conditions for using the finisher. Keep its
`delegation.allowed_agents` consistent with the workers it may call.

Change each worker's default model in Agents settings, or its `default_model`
frontmatter, to change providers/models/effort. Do not put model names in the
command definition. Agent IDs are stable even when you rename an agent or change its
model. Keep delegation targets tied to those IDs; do not infer a provider from an
agent ID such as `qwen-builder`. The optional `contrib/build-review/` example starts
with `deepseek-builder`, `luna-reviewer`, and `sol-finisher`, but their default models
are yours to configure.

Builder and finisher instructions require small, coherent commits after relevant
checks pass, staging only their own work. Explicit task instructions not to commit
override that default. Reviewers stay read-only. The coordinator checks reported
commit hashes and remaining changes before declaring completion. Pushing requires
an explicit user request.

The harness transfers the exact approved plan and execution note automatically. Coordinators
can also give delegated calls stable `stage_id` and `depends_on` values. Completed dependencies
are attached as evidence, failed attempts remain available after restart, and invoking the
command again resumes a needs-attention plan without replacing completed checklist work.
Coordinator prompts should contain only the stage assignment and necessary reports,
not a rewritten copy of the plan. Existing active runs keep their loaded agent
instructions; configuration edits are intended for the next invocation.

## Continuity and limits

Each worker has its own active-time budget. Time the coordinator spends awaiting
delegated workers is excluded from the coordinator's budget, including inline
delegations. Nested measurements do not count the same work twice. Real active
model/tool work still times out, and explicit cancellation still stops work.

This does not automatically restart failed or cancelled work after sleep or a
gateway crash. Inspect the durable child results and continue from the parent;
do not rerun an already completed implementation merely to restart its review.

An explicit Auto-mode continuation such as "continue", "resume", or "finish this up"
in a session with a failed, previously approved plan continues that plan without replacing its text or checklist. Its execution status
follows the new run; Plan-mode feedback and new unapproved plans do not resume
execution. Completed plans are left completed.
