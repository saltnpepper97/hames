# M9 plugin smoke

Isolated-home smoke for isolated plugins. Tag `m09` only after this cycle
passes. Observed on 2026-08-23.

- Date: 2026-08-23
- Isolation: `HAMES_HOME=/tmp/hames-m9-accept`
- Demo workspace: `/tmp/hames-m9-demo`
- Fixture: `tests/fixtures/plugins/project-stats`
- Sandbox: bubblewrap present (`/usr/bin/bwrap`)
- Result: **pass**

Script: `/tmp/hames-m9-smoke.py` against this tree.

| Check | Expected | Observed |
|---|---|---|
| inspect shows permissions | `broker:project_read` | `broker:project_read` |
| install stays disabled | `enabled=false` | `enabled=False` |
| enable starts worker + tools | `project-stats.summary` running | running, tool registered |
| worker is not the gateway | `/proc` cmdline is `bwrap` | `bwrap --unshare-all ... /plugin/worker.py` |
| `$HOME` in sandbox | `/tmp`, not the host home | `--setenv HOME /tmp` |
| brokered `project.list` | `summary` counts project files | `completed 1 files` |
| worker crash | tool fails, process stays up | `failed plugin worker closed` |
| disable | tools gone | `running=False`, names empty |
| capability scar proposal | `proposed`, not installed | `cap-b0738fc9` status `proposed`, names empty |
