# M09 — Isolated Plugin System and Agent-Authored Capability Proposals

## Goal

Add genuine extensibility without turning the trusted Hames process into arbitrary third-party Python execution.

Plugins add capabilities. Skills add procedures. The distinction must remain visible in code and UI.

For v0.1, third-party and agent-authored plugins run as subprocess workers. They do not import into the Hames controller.

## Plugin package

A plugin directory contains:

```text
plugin.toml
pyproject.toml optional
src/ or main.py
tests/ optional
README.md
```

Manifest fields:

```toml
id = "git-extra"
name = "Git Extra"
version = "0.1.0"
api_version = 1
entrypoint = "hames_git_extra.worker:main"

capabilities = ["tool"]
permissions = [
  "broker:project_read",
  "broker:project_write"
]
```

Unknown API versions are rejected.

## Initial plugin capability types

Expose only proven extension seams.

### 1. Tool provider

Plugin registers one or more model-callable tools with JSON schemas.

### 2. Context source

Plugin can provide context candidates when compiler asks it, subject to context budget and source attribution.

### 3. Event consumer

Plugin can receive filtered event stream for analytics/integration behavior. It cannot mutate ledger history.

Do not expose replacement of:

- event ledger;
- policy gate;
- core agent loop;
- context compiler.

## Plugin worker protocol

Use versioned JSON-RPC or equally narrow framed protocol over stdin/stdout.

Handshake communicates:

- plugin ID/version;
- protocol version;
- declared capabilities;
- tool/context schemas;
- health status.

Controller requests include:

```text
tool.execute
context.collect
event.deliver
worker.shutdown
```

Responses are typed and bounded.

Worker crash produces typed plugin failure and does not crash Hames.

## Isolation

On Linux, untrusted/plugin workers launch through `bubblewrap`.

Default sandbox:

- plugin code/environment mounted read-only;
- empty writable temp directory;
- no direct Hames config/state access;
- no home directory;
- no host project filesystem;
- no network namespace access;
- bounded process lifetime/resource policy where practical.

Plugin receives host resources only through broker RPC calls approved by Hames policy.

If `bwrap` unavailable:

- agent-authored/untrusted plugins refuse to execute;
- user-installed plugin may run unsandboxed only through explicit unsafe configuration/confirmation;
- UI/CLI labels that state clearly.

Do not pretend subprocess separation alone is a security sandbox.

## Capability broker

Plugin permissions request broker operations, not raw host access.

Initial broker operations:

```text
project.read
project.list
project.write
process.run_scoped
network.request
```

Each broker request:

1. includes plugin ID and originating agent/run;
2. passes policy gate;
3. is restricted to manifest permissions;
4. emits policy/tool/plugin events;
5. returns bounded/redacted results.

Manifest permission is necessary but not sufficient; user policy may further restrict it.

Plugin cannot grant itself permission at runtime.

## Plugin installation

CLI:

```bash
hames plugin inspect <path>
hames plugin install <path>
hames plugin list
hames plugin enable <id>
hames plugin disable <id>
hames plugin remove <id>
```

Installation:

1. validates manifest;
2. hashes source;
3. displays requested permissions;
4. requires explicit approval;
5. creates isolated plugin environment;
6. installs dependencies into that environment if present;
7. records exact package/environment fingerprint;
8. runs plugin self-test/handshake;
9. remains disabled if validation fails.

Network dependency resolution requires confirmation.

Removing plugin does not remove historical plugin events.

## Plugin events

Add:

```text
plugin.installed
plugin.enabled
plugin.disabled
plugin.removed
plugin.worker.started
plugin.worker.stopped
plugin.worker.failed
plugin.capability.registered
plugin.broker.requested
plugin.broker.completed
plugin.proposal.created
```

## Agent-authored plugin proposals

Agent may conclude a Scar/task requires missing capability.

It can create proposal under:

```text
~/.hames/proposals/plugins/<proposal-id>/
```

Proposal includes:

- problem/capability statement;
- evidence events/Scar;
- plugin manifest;
- source;
- tests;
- requested permissions;
- security notes;
- demonstration results in sandbox;
- expected benefit.

Agent may automatically:

- write proposal;
- run static tests;
- run in sandbox with synthetic/broker-limited fixtures;
- show tool schemas/results.

It may not:

- install into active plugin set;
- enable itself;
- request newly granted host permissions without user approval.

Promotion uses the same installation flow as external plugins.

## Plugin versioning

Installing new version:

- keeps previous version metadata;
- validates requested permission diff;
- requires renewed approval when permissions expand;
- can roll back to previous installed version;
- does not overwrite historical source fingerprints.

## Context/Inspector integration

Context manifests identify plugin context sources and hashes.

Inspector adds:

- installed plugins;
- version;
- worker state;
- permissions;
- registered capabilities;
- broker request history;
- crashes;
- proposals;
- permission changes.

## Tests

Cover:

- manifest validation;
- protocol handshake;
- tool registration;
- context source;
- event consumer filtering;
- worker crash isolation;
- timeout/cancellation;
- sandbox denies direct host file access;
- sandbox denies network by default;
- broker read/write policy;
- permission escalation rejection;
- missing `bwrap` behavior;
- install/enable/disable/remove;
- version upgrade permission diff;
- rollback;
- agent-authored proposal cannot self-enable;
- migration from M08.

Where generic CI cannot provide `bwrap`, provide a Linux CI job/container that does.

## Manual smoke test

Build a tiny plugin exposing safe project statistic tool.

Verify:

1. install screen shows permissions;
2. plugin runs outside Hames controller;
3. direct access to `$HOME` fails inside sandbox;
4. brokered project read succeeds when allowed;
5. tool appears to agent;
6. tool call visible in inspector;
7. disabling plugin removes capability through clean worker lifecycle;
8. plugin crash does not kill session/controller.

Then have an agent create plugin proposal and verify it remains inactive until explicitly installed.

## Commit expectations

Suggested slices:

1. manifest/registry;
2. worker protocol;
3. bwrap sandbox;
4. capability broker;
5. install/version lifecycle;
6. agent proposals;
7. inspector/e2e/security docs.

## Acceptance gate

M09 is complete when plugins add real tools/context/event consumers, plugin code is not imported into trusted controller, untrusted execution is sandboxed on supported Linux target, host side effects pass Hames broker/policy gate, and agent-authored plugins cannot silently install themselves.

Finish clean and tag `m09`.
