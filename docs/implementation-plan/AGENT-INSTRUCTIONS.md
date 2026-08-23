# Hames Rewrite — Instructions for the Implementing Agent

This file governs how the implementation is executed.

The implementation agent is expected to work diligently, make reasonable local decisions without repeatedly stopping for permission, and preserve a readable Git history as the work progresses.

## 1. Start with Git, immediately

Before writing implementation code:

```bash
git init -b main
git status
```

Create the initial project metadata, `.gitignore`, license/readme material required by M00, and make the first commit before starting runtime code:

```bash
git add .
git commit -m "chore: initialize hames rewrite"
```

Do not build half the project and initialize Git afterward.

## 2. Commit while working, not after working

Commits are part of the engineering output.

A good commit:

- implements one coherent behavior;
- includes the tests for that behavior;
- leaves the repository runnable and the relevant tests passing;
- does not mix unrelated refactors;
- has a message explaining what changed.

Preferred forms:

```text
feat(events): add append-only event store
feat(gateway): stream session events over SSE
feat(policy): require approval for destructive shell operations
test(memory): cover scope visibility and supersession
fix(skills): reject stale compare-and-swap patches
docs(m06): document memory provenance model
```

Do not make one giant “implement milestone” commit.

Do not intentionally commit failing tests with the plan to fix them later. If a test is added to expose a bug, fix the bug in the same coherent commit unless a milestone explicitly requires a separate regression reproduction state.

## 3. Milestone close procedure

At the end of every milestone:

1. Run formatting and static checks.
2. Run all tests created or affected by the milestone.
3. Run the full test suite unless the milestone explicitly cannot yet support it.
4. Start the application from a clean state root and perform the milestone’s manual smoke test.
5. Confirm migrations work from a new database.
6. Confirm `git status --short` is empty.
7. Update documentation required by that milestone.
8. Commit any final documentation/test adjustment.
9. Tag the milestone, e.g.:

```bash
git tag -a m00 -m "Hames milestone M00 complete"
```

Never tag a milestone with a known failing acceptance criterion.

## 4. Tests must be independent of paid APIs

The normal test suite may not require network access or paid model calls.

Every provider must have deterministic fixtures or a fake provider capable of:

- streaming text;
- streaming tool calls;
- reporting usage;
- returning malformed events;
- disconnecting mid-stream;
- timing out;
- being cancelled.

Model-dependent evaluation features must have fixture-backed tests. Live-provider smoke tests may exist behind explicit environment flags.

## 5. State isolation

Tests must never write to the user’s real Hames directories.

All tests set `HAMES_HOME` to a temporary isolated root.

The runtime must support constructing an explicit application state root for tests.

## 6. Schema and migration discipline

Every durable schema change requires:

- a migration;
- a test that creates a fresh database;
- when applicable, a test migrating the previous milestone’s schema;
- a rollback or recovery strategy documented when destructive transformations are unavoidable.

Never edit an already-released migration to make a later test pass.

## 7. Public boundary discipline

Anything that crosses a durable or process boundary uses a versioned typed schema.

Examples:

- gateway requests/responses;
- persisted event payloads;
- plugin RPC messages;
- plugin manifests;
- `AGENT.md` frontmatter;
- skill metadata;
- export bundles.

Internal helper objects do not need serialization ceremony.

## 8. Event discipline

The event ledger is the provenance root.

When adding a feature, ask:

- What event proves it happened?
- What event links it to the model call or user action that caused it?
- Can a client or transcript explain this feature using existing events?

Do not create hidden mutable state that materially influences behavior but cannot be reconstructed or attributed.

Secrets are the exception: store a redacted event plus secure reference or hash, never plaintext secrets in event payloads.

## 9. Security discipline

Never weaken the policy gate to make a feature easier.

All side effects initiated through Hames must pass through the same policy decision path, including:

- main agent tools;
- child agents;
- skills;
- plugin tools;
- automatic evaluators;
- evolution/repair jobs.

Agent-authored code must never be activated merely because it was generated successfully.

## 10. Keep the kernel small

Do not pull in LangChain, AutoGen, CrewAI, or another agent orchestration framework as the runtime.

Libraries for focused problems are fine. The Hames loop, event semantics, policy gate, context compiler, memory scopes, skills, and Scars are product behavior and must remain owned by Hames.

## 11. Avoid speculative abstraction

Do not build extension points merely because they might be useful someday.

The plugin API is deliberately delayed until M09 so real internal implementations have already demonstrated what needs to be extensible.

Prove behavior in the Rust REPL before investing in rich clients. A customized
Ratatui client comes before or alongside later web work; all clients must use the
same gateway rather than introducing another runtime.

## 12. Error handling

A runtime error must be one of:

- converted to a typed failure and surfaced to the user;
- retried under an explicit bounded retry policy;
- rejected before execution;
- recorded and propagated to the caller.

Do not silently swallow failures.

Provider retries must not accidentally duplicate side-effecting tool calls.

## 13. Documentation is part of completion

Each milestone specifies the documentation it requires. Keep documentation factual and current.

Do not write aspirational docs that describe features not yet implemented.

## 14. When implementation reality disagrees with this plan

If a detail is impossible or clearly counterproductive:

1. preserve the milestone’s user-visible outcome and safety properties;
2. choose the smallest alternative architecture that achieves them;
3. document the deviation in `docs/decisions/ADR-XXXX-*.md`;
4. commit the ADR with the implementation change.

Do not silently drift from the plan.

## 15. Final quality bar

Prefer code that is boring to debug.

Hames should be understandable from:

- its event history;
- its schemas;
- its tests;
- its Git history;
- its UI.

Cleverness that hides causality is a regression.
