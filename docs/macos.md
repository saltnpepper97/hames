# macOS (experimental)

Hames now has a portability path for the Web UI, terminal UI and REPL, local
files and shell tools, native workspace folder selection, provider connections,
sessions, memory, goals, delegation, isolated plugins and Skill scripts, and
external MCP servers. These changes are newer than the `0.2.0` release.
Interactive Mac verification and managed-search validation remain on the
checklist below, so support is experimental.

## Install from the checkout

Use a native terminal for your Mac architecture. Install Apple's command-line
developer tools (`xcode-select --install`), Git, Python 3.12 or newer,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and
[Rust](https://www.rust-lang.org/tools/install) 1.85 or newer. Ensure `uv` and
`cargo` are on your shell's PATH. Node is only needed to develop the Web frontend;
the checkout includes its built assets.

```bash
git clone https://github.com/saltnpepper97/hames.git
cd hames
HAMES_INSTALL_LOCAL=1 ./install.sh
hames setup
hames doctor
hames web
```

The command above builds the current `main` checkout. The normal tagged installer
still selects `0.2.0`, which does not contain these macOS changes.
The local installer keeps its Python environment in this checkout, so retain it.
The launcher normally installs into `~/.local/bin`; add that directory to PATH
if prompted. When updating an existing installation, finish active work and run
`hames gateway restart` after installation to load the new backend.
Hames state remains in `~/.hames`, or the directory set by
`HAMES_HOME`.

`hames` starts the TUI; `hames repl` starts the line-oriented interface.
Provider executables must be installed separately and accessible on PATH;
API providers need their usual connection configuration.

## Limits

| Capability | macOS preparation |
| --- | --- |
| Gateway, Web, TUI, REPL, providers, sessions and delegation | Native automated checks pass; interactive checks remain |
| Instruction-only Skills and external MCP | Available without a sandbox |
| Isolated plugin workers and Skill scripts | Use macOS `sandbox-exec`; native tests check home, network, project-write, and scratch boundaries |
| Managed SearXNG search | Optional; needs a working Docker or Podman engine; not validated on Mac |
| Automatic login startup | Optional per-user LaunchAgent; gateway still starts on demand without it |
| Desktop screenshots and GUI Skills | `macos-gui-testing` covers native capture and dialogs; interactive permission and visual checks remain |

`hames doctor` reports `macos_sandbox` separately from `bubblewrap`. Installing a
command named `bwrap` on macOS does not enable Linux isolation. If
`/usr/bin/sandbox-exec` is missing, Skill script execution and validation stop
before running scripts and plugin workers are rejected by default. The existing
`plugins.allow_unsandboxed_user_plugins` developer override still permits
explicitly trusted workers without isolation; the installer does not enable it.
Generic tools also protect Mac Keychain paths and common credential-export
commands. The macOS sandbox relies on Apple's deprecated `sandbox-exec` tool;
native boundary tests are required before treating an OS upgrade as supported.

The gateway survives closing the client. Use `hames gateway status`,
`hames gateway stop`, and `hames gateway restart` to manage it. Quit active work
before restarting. For optional login startup, stop the gateway while idle and
then install the per-user LaunchAgent:

```bash
hames gateway stop
hames gateway service install
hames gateway service status
```

The agent uses the Python backend from the current installation and the current
`HAMES_HOME`. Keep that installation in place. `hames gateway stop` unloads the
agent for this login session; `hames gateway start` loads it again.
`hames gateway service remove` unloads the agent and removes login startup.
The agent captures the current `PATH` at install time so provider executables and
Docker/Podman can be found. If those paths change, remove and reinstall it after
stopping active work. API credentials supplied only through a terminal's
environment are not automatically present in a login agent; use Hames's
credential-file configuration or configure the agent environment explicitly.

## Verification

The platform workflow targets Linux and macOS 15 and 26 on Apple Silicon and
Intel. It runs Python and Rust tests, native isolation probes, static checks, a
local source installation, and installed-launcher, TUI PTY, and launchd smoke checks.
The TUI smoke sends a message to a local fixture provider and checks its reply. The
launcher smoke uses disposable state and a separate port to exercise diagnostics,
start/restart/stop, authenticated Web launch, and serving bundled assets without
touching your normal Hames gateway:

```bash
uv run python scripts/smoke_install.py "$(command -v hames)"
uv run python scripts/smoke_tui.py "$(command -v hames)"
```

[Platform CI](https://github.com/saltnpepper97/hames/actions) contains the
latest matrix results. AppleScript compilation is checked natively; folder
selection and cancellation also have mocked regression coverage. The interactive
folder-picker smoke needs an unlocked GUI session with Accessibility permission
for UI automation. It is not run in CI; on a Mac, run
`HAMES_TEST_INTERACTIVE_PICKER=1 uv run python scripts/smoke_install.py "$(command -v hames)"`
to exercise it. The optional SearXNG smoke requires a working Docker or Podman
engine and remains a manual Mac check:

```bash
HAMES_TEST_SEARXNG=1 uv run pytest -q tests/unit/test_web_search.py -k managed_searxng_container_smoke
```

Before removing the experimental label, a real Mac should also
verify the TUI's visible appearance after resizing, opening the Web UI and choosing a
workspace folder in a browser, one provider-backed conversation, file edits and
shell commands in a disposable workspace, cancellation of a running command,
and a real Screen Recording or Accessibility permission flow when using desktop
automation. Automated Web HTTP checks do not verify browser rendering or
interactive terminal behavior.
