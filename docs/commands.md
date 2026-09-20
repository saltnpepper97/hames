# User-defined slash commands

Custom commands are local configuration, not Hames built-ins. Web, TUI, and REPL
read the same gateway catalog. No command implies a particular provider or model.

## Maintain your commands

Put one TOML file per command in `~/.hames/commands/`.
Its filename supplies the slash name: `execute-plan.toml` becomes `/execute-plan`.

For example:

```toml
description = "Execute the approved plan with Builder"
action = "execute_plan"
agent = "builder"
```

- Change the filename to rename the command.
- Change `description` to update its menu text.
- Change `agent` to choose an agent, using its stable ID or current slug.
- Delete the file to remove the command.
- Put a file with the same name in `<workspace>/.hames/commands/` to override it
  for that workspace only. An invalid override reports an error; it never silently
  executes the global version.
- Built-in names such as `plan`, `model`, `help`, and `stop` are reserved.

The first supported action is `execute_plan`. It explicitly approves the current
ready plan and runs it with the configured agent. It accepts an optional
execution note: `/execute-plan preserve the public API`. It rejects missing or
non-ready plans, missing agents, and active conflicting work. It does not execute
shell text from the TOML file. User-invoked skills continue to appear as their own
slash commands; a custom command takes precedence over a skill with the same name.

Definitions are reread at execution, so edits need no gateway restart. Reopen the
command menu in TUI, or reload Web, to refresh its suggestions. REPL `/help` lists
current custom commands. Switching workspace changes the workspace overrides.

## Agent configuration

Change the selected agent's instructions and default model in Agents settings. Commands
select an agent; they do not define a pipeline or model configuration. Agent IDs remain
stable across renames. The harness supplies the approved plan and execution note directly,
so you do not need to copy the plan into a new message. Existing active runs retain their
loaded instructions; edits apply to the next invocation.

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
