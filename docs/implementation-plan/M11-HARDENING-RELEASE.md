# M11 — Hardening, Backup/Restore, Packaging, Documentation, and v0.1.0 Release

## Goal

Turn the feature-complete system into a release that can be trusted with ongoing personal work.

No major new subsystem is added here. This milestone closes reliability, security, migration, packaging, and documentation gaps discovered by exercising complete harness.

## 1. End-to-end invariants

Create tests proving:

### Historical reconstruction

A completed session reconstructs after restart with:

- messages;
- model calls;
- tools;
- policy decisions;
- agent branches;
- context manifests;
- memory retrieval;
- flows;
- Scars;
- plugin events.

### Policy non-bypass

Attempt side effects through:

- main agent tool;
- child agent;
- flow script;
- plugin tool;
- evolution evaluator/repair action.

Every path must reach policy enforcement.

### Scope isolation

Prove:

- agent-private memory not exposed to other agents;
- project memory does not leak to unrelated project context;
- plugin broker cannot exceed manifest + policy;
- child agents cannot broaden parent authority.

### Version safety

Prove stale writes rejected for:

- agent edits if version-checked;
- flow patches;
- repair proposals;
- plugin version updates;
- policy/context-rule changes.

## 2. Database integrity and recovery

Add:

```bash
hames db check
hames db backup <path>
```

`db check` verifies:

- SQLite integrity;
- migration version;
- orphaned references;
- blob existence/hash;
- session branch consistency;
- active flow version consistency;
- memory supersession chains;
- Scar repair references.

Document recovery behavior for corruption.

Do not silently delete corrupt records.

## 3. Export and restore

Implement portable bundle:

```bash
hames export <path>
hames import <path>
```

Bundle includes:

- database snapshot;
- referenced blobs;
- agents;
- flows;
- safe configuration;
- plugin manifests/source fingerprints;
- schema/export version;
- integrity manifest.

Secrets excluded by default.

Import:

- verifies hashes;
- validates export version;
- restores into new state root or explicit merge mode;
- never silently overwrites active state root;
- reports conflicts.

Test export → wipe temporary state → import → compare behavior/history.

## 4. Backup consistency

A backup taken while Hames runs must be transactionally consistent.

Use SQLite backup API or equivalent.

Referenced blob snapshot must correspond to database snapshot.

Document external project files are not part of Hames backup.

## 5. Secret handling audit

Audit every event/config/API/log path.

Requirements:

- provider secrets use environment/keyring/secret reference mechanism;
- plaintext secrets never returned by settings API;
- logs redact secrets;
- events redact tagged secret values;
- export excludes secrets;
- Web UI does not cache/display plaintext secrets after submission;
- plugin broker does not expose unrelated environment variables.

Add regression fixtures with fake secret strings and assert absence from exported/logged/event/frontend data.

## 6. Plugin sandbox hardening

On supported Linux host:

- verify bwrap profile;
- no home access;
- no Hames state access;
- no network by default;
- no arbitrary project access;
- brokered access only;
- process termination on cancellation/controller shutdown;
- bounded worker output;
- malformed RPC cannot crash controller.

Document exactly what sandbox does/does not protect against.

## 7. Performance budgets

Create representative local benchmarks for:

- gateway startup;
- empty REPL startup;
- event append latency;
- large personal-scale session replay;
- memory FTS search;
- hybrid memory search with several thousand records;
- Web Inspector initial load;
- plugin worker startup.

Record baselines from real measurements before setting thresholds. Commit benchmark methodology and thresholds.

Performance work must not weaken correctness/provenance.

## 8. Retention and cleanup

Implement explicit commands:

```bash
hames storage status
hames storage gc
```

GC may remove:

- unreferenced temporary blobs;
- expired caches;
- abandoned plugin build environments;
- rejected proposal working files according to retention config.

GC may not delete ledger-referenced blobs/history without explicit destructive user action.

Report reclaimed bytes.

## 9. Packaging

Produce installable Python package.

Requirements:

- `uv tool install` or equivalent supported path;
- `hames` command;
- bundled/served production web assets;
- database migrations included;
- package metadata/version `0.1.0`;
- Linux dependency docs for `bubblewrap`;
- graceful doctor warning when optional sandbox dependency missing.

Normal user should not need Node.js after installing release package.

## 10. First-run setup

Implement:

```bash
hames init
```

It:

- creates safe config directories;
- asks/selects provider type;
- writes non-secret provider config;
- explains how to provide API key or local base URL;
- optionally trusts chosen project;
- leaves user able to run `hames`.

It also supports noninteractive/test mode.

Do not force account creation/cloud service.

## 11. Documentation set

Complete:

```text
docs/getting-started.md
docs/configuration.md
docs/agents.md
docs/memory.md
docs/flows.md
docs/scars.md
docs/plugins.md
docs/security.md
docs/architecture/event-ledger.md
docs/architecture/context-compiler.md
docs/architecture/policy.md
docs/backup-restore.md
docs/troubleshooting.md
```

Documentation describes implemented v0.1 behavior only.

Include concise diagrams for:

- gateway/kernel;
- event/session branches;
- memory scopes;
- flow proposal lifecycle;
- Scar repair loop;
- plugin sandbox/broker.

## 12. Release test matrix

Required automated matrix:

- supported Python versions;
- clean SQLite database;
- migration from every retained pre-release schema fixture;
- fake OpenAI-compatible provider;
- fake Anthropic provider;
- web build/tests;
- Linux plugin sandbox integration;
- export/import round trip.

Required manual release smoke:

1. install from built package in fresh environment;
2. run `hames init`;
3. configure local/fake or real provider;
4. trust disposable project;
5. complete coding task via REPL;
6. inspect in web;
7. use named child agent;
8. verify memory recall;
9. produce/approve flow proposal;
10. create/repair/heal a Scar;
11. install/execute isolated test plugin;
12. export state;
13. restore to fresh state root;
14. reopen prior session and verify provenance.

## 13. Release Git discipline

Before release:

```bash
git status --short
uv run pytest
# run frontend test/build commands
hames db check
```

Create release commit containing version/changelog only after feature code already committed:

```text
chore(release): prepare hames 0.1.0
```

Tag:

```bash
git tag -a v0.1.0 -m "Hames v0.1.0"
```

Do not combine weeks of uncommitted implementation into release commit.

## Explicit v0.1.0 non-goals

These are deliberate boundaries, not unfinished placeholders:

- rich curses/Textual TUI;
- multi-user hosted SaaS;
- Windows/macOS plugin sandbox parity;
- autonomous permission expansion;
- autonomous activation of agent-authored plugins;
- giant swarm orchestration;
- replacement of core ledger/policy/loop via plugins;
- external graph/vector database requirement.

The bare REPL and full local Web Control UI are supported clients.

## Acceptance gate

Hames v0.1.0 may be tagged only when:

- every prior milestone acceptance gate still passes;
- full automated suite passes from clean checkout;
- release smoke matrix passes;
- package installs without source-tree assumptions;
- backup/export/import round trip succeeds;
- secret regression audit passes;
- plugin isolation tests pass on supported Linux CI/host;
- docs match behavior;
- database integrity check succeeds;
- Git working tree is clean.

Finish with annotated tags `m11` and `v0.1.0`.
