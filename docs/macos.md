# macOS (experimental core support)

Hames now has a portability path for the Web UI, terminal UI and REPL, local
files and shell tools, native workspace folder selection, provider connections,
sessions, memory, goals, delegation,
and external MCP servers. These changes are newer than the `0.2.0` release.
Native macOS validation is pending; this is not yet a claim of tested Mac support.

## Install from the checkout

Use a native terminal for your Mac architecture. Install Apple's command-line
developer tools (`xcode-select --install`), Git, Python 3.12 or newer,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and
[Rust](https://www.rust-lang.org/tools/install) 1.85 or newer. Ensure `uv` and
`cargo` are on your shell's PATH. Node is only needed to develop the Web frontend;
the checkout includes its built assets.

```bash
git clone --branch macos-core-support https://github.com/saltnpepper97/hames.git
cd hames
HAMES_INSTALL_LOCAL=1 ./install.sh
hames setup
hames doctor
hames web
```

The command above selects the preparation branch. The normal
tagged installer still selects `0.2.0`, which does not contain macOS preparation.
The local installer keeps its Python environment in this checkout, so retain it.
The launcher normally installs into `~/.local/bin`; add that directory to PATH
if prompted. Hames state remains in `~/.hames`, or the directory set by
`HAMES_HOME`.

`hames` starts the TUI; `hames repl` starts the line-oriented interface.
Provider executables must be installed separately and accessible on PATH;
API providers need their usual connection configuration.

## Limits

| Capability | macOS preparation |
| --- | --- |
| Gateway, Web, TUI, REPL, providers, sessions and delegation | Portable implementation; native checks pending |
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

Before claiming Mac support, both Mac CI jobs must pass. A real Mac should also
verify interactive TUI resizing/input, opening the Web UI and choosing a
workspace folder in a browser, one provider-backed conversation, file edits and shell commands in a disposable
workspace, and cancellation of a running command. Automated Web HTTP checks do
not verify browser rendering or interactive terminal behavior.
