# macOS (experimental core support)

Hames now has a portability path for the Web UI, terminal UI and REPL, local
files and shell tools, native workspace folder selection, provider connections,
sessions, memory, goals, delegation,
and external MCP servers. These changes are newer than the `0.2.0` release.
Automated core checks pass on macOS 15 on Apple Silicon and Intel. Interactive
Mac verification remains on the checklist below, so support is experimental.

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
| Instruction-only Skills and external MCP | Available without Bubblewrap |
| Isolated plugin workers and Skill scripts | Unavailable; require Linux Bubblewrap |
| Managed SearXNG search | Optional; needs a working Docker or Podman engine; not validated on Mac |
| Automatic login startup | No bundled launchd service; gateway starts on demand |
| Desktop screenshots and Linux GUI Skills | Linux-specific guidance; no native Mac desktop automation integration |

`hames doctor` can report a healthy core while listing unavailable sandbox
features in `limitations`. Installing a command named `bwrap` on macOS does not
enable Linux isolation. Skill script execution and validation stop before
running scripts when isolation is unavailable. Plugin workers are rejected by
default. The existing `plugins.allow_unsandboxed_user_plugins` developer override
still permits explicitly trusted workers without isolation; the installer does
not enable it. Generic tools also protect Mac Keychain paths and common
credential-export commands.

The gateway survives closing the client. Use `hames gateway status`,
`hames gateway stop`, and `hames gateway restart` to manage it. Quit active work
before restarting. macOS does not use the optional Linux systemd unit.

## Verification

The platform workflow targets Linux, macOS 15 on Apple Silicon, and macOS 15 on
Intel. It runs Python and Rust tests, static checks, a local source installation,
and an installed-launcher smoke test. The smoke test uses disposable state and a
separate port to exercise diagnostics, start/restart/stop, authenticated Web
launch, and serving bundled assets without touching your normal Hames gateway:

```bash
uv run python scripts/smoke_install.py "$(command -v hames)"
```

[Platform CI](https://github.com/saltnpepper97/hames/actions/runs/36711166877)
passed on Linux, Apple Silicon, and Intel for code commit `0676294`. Both Macs
passed 503 Python tests (two optional/Linux-only skips), 246 Rust tests, static
checks, package builds, and the installed-launcher smoke. AppleScript compilation
is checked natively; folder selection and cancellation also have mocked regression
coverage.

Before removing the experimental label, a real Mac should also
verify interactive TUI resizing/input, opening the Web UI and choosing a
workspace folder in a browser, one provider-backed conversation, file edits and shell commands in a disposable
workspace, and cancellation of a running command. Automated Web HTTP checks do
not verify browser rendering or interactive terminal behavior.
