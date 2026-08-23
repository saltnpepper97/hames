# M00 — Repository Bootstrap and Engineering Contract

## Goal

Produce a clean, runnable Hames repository with immediate Git history, deterministic configuration/state paths, database migration infrastructure, logging, testing, and development commands.

At the end of this milestone there is not yet an agent. There is, however, a production-quality foundation on which every later milestone can safely build.

## Required user-visible outcome

From a clean checkout:

```bash
uv sync
uv run hames --version
uv run hames doctor
uv run pytest
```

all succeed without network access.

`hames doctor` reports the resolved config/state/cache directories, SQLite availability including FTS5, Python version, and whether optional host facilities such as `bwrap` are available. Missing future-only facilities are informational, not fatal.

## Work

### 1. Initialize Git before implementation code

Follow `AGENT-INSTRUCTIONS.md`.

Minimum early commits:

```text
chore: initialize hames rewrite
build: configure python project and development tooling
```

### 2. Create the Python project

Use `src/` layout and expose a `hames` console command.

Define:

```text
hames --version
hames doctor
```

`--version` must come from package metadata, not a duplicated hard-coded constant.

### 3. XDG paths

Implement a single path resolver with:

- config root;
- state root;
- cache root;
- database path;
- blob path;
- proposal path;
- plugin-worker path.

Respect environment overrides and permit an explicit application root in tests.

Creating directories must be lazy and have deterministic permissions.

### 4. Configuration

Implement `config.toml` loading with:

- typed schema;
- defaults;
- environment overrides for secrets and machine-specific provider settings;
- validation errors with exact field paths;
- no automatic rewriting of the user’s config during normal startup.

Initial configuration sections:

```toml
[server]
host = "127.0.0.1"
port = 7411

[database]
path = ""

[logging]
level = "INFO"

[providers]
default = ""

[security]
trusted_project_roots = []
```

Empty/default provider is valid in M00 so tests and `doctor` can run without a model.

Unknown configuration keys must produce a warning or error according to one documented rule; do not silently ignore typos.

### 5. Logging

Use structured application logging with:

- timestamp;
- level;
- subsystem;
- human-readable message;
- optional session/event identifiers.

Secrets, authorization headers, API keys, and raw environment dumps must never be logged.

Provide plain terminal logs first. JSON log output may be configurable but must use the same event fields.

### 6. SQLite and migrations

Create migration infrastructure now.

Requirements:

- SQLite WAL mode;
- foreign keys enabled;
- migration table with monotonic migration IDs;
- application startup applies pending compatible migrations;
- migration failures abort startup rather than continuing with half-upgraded state;
- FTS5 capability is checked by `doctor`.

M00 schema may contain only migration metadata and an application metadata table.

### 7. Test harness

Configure:

- `pytest`;
- `pytest-asyncio`;
- temporary isolated XDG roots;
- helper fixture for fresh app state;
- helper fixture for migrated SQLite database;
- no tests touching the real home directory.

### 8. Developer quality commands

Define reproducible commands for:

- formatting;
- linting;
- tests;
- type checking if selected;
- web commands may be added later.

Document them in the root README.

### 9. Basic documentation

Root README must state only what exists at M00:

- project purpose in one paragraph;
- supported Python/Linux target;
- install/dev setup;
- `hames doctor`;
- test command;
- repository status as pre-runtime foundation.

Do not document agents, memory, skills, or plugins as already available.

## Tests

At minimum:

- default XDG path resolution;
- environment-overridden paths;
- explicit temporary root;
- config parsing;
- invalid config rejection;
- secret values absent from logs;
- clean database creation;
- idempotent migration application;
- migration failure leaves a detectable failed startup;
- `doctor` succeeds on a supported Linux environment;
- CLI `--version` matches package metadata.

## Commit expectations

This milestone should normally contain 3–5 coherent commits, including tests with the implementation they verify.

Suggested slices:

1. repository/project bootstrap;
2. XDG/config/logging;
3. SQLite migrations;
4. doctor/test harness/docs.

## Acceptance gate

M00 is complete only when:

- the repository was initialized before runtime implementation;
- the working tree is clean;
- all M00 tests pass offline;
- `hames --version` and `hames doctor` work from a clean environment;
- no code writes to the real user directories during tests;
- migration state is deterministic;
- README matches implemented behavior;
- an annotated `m00` tag is created only after the above passes.
