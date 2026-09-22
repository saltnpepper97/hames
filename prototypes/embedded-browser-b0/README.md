# Embedded browser — B0 feasibility prototype

A disposable, local-only prototype for the B0 milestone of
[the embedded browser plan](../../docs/implementation-plan/EMBEDDED-BROWSER.md): prove
that one real browser page can be watched and driven through a live view while a
Playwright adapter works with the *same* page.

This is not production code. Nothing here is imported by Hames, and nothing here starts,
stops, or reconfigures a gateway, an MCP server, or a personal browser. It exists to
answer the transport question and to leave reproducible evidence behind.

Findings: `measurements/b0-comparison.md` (matrix and decision) and
[docs/implementation-plan/EMBEDDED-BROWSER-B0-EVIDENCE.md](../../docs/implementation-plan/EMBEDDED-BROWSER-B0-EVIDENCE.md)
(acceptance evidence, limitations, remaining work).

## What it does

- **Fixture site** (`fixture/`) — two dependency-free Node HTTP servers on loopback.
  `index.html` shows session/page identity (nonce, URL, title, generation, heartbeat,
  viewport), a form with live field values and submit counters, keyboard counters,
  a pointer-driven canvas, inner and document scrolling, a same-origin link, a
  second-origin "external-style" link, and a button that triggers a deliberately failing
  request (HTTP 500). No external fonts, images, or network calls.
- **One shared page** — a single dedicated Playwright Chromium context with exactly one
  page. The viewer never creates a page; the agent adapter never creates a page.
- **Two transport candidates**, driven by the same interaction script and the same
  viewer wire protocol, so the comparison is about capture/input only:
  - `frame-stream/` — `Page.startScreencast` JPEG frames and CDP input dispatch in a
    headless Chromium context.
  - `remote-view/` — headed Chromium on an isolated Xvfb display, captured with
    ffmpeg/x11grab, input injected with xdotool (XTEST).
- **Standalone viewer** (`viewer/`) — a plain HTML/canvas page served by the prototype:
  it paints frames, forwards pointer/wheel/keyboard input, shows page identity, and
  reconnects on its own. It contains no iframe and no browser endpoint.
- **Harness tools** (`tools/`) — start one candidate headlessly (`serve`), drive and
  measure it (`measure`), check the milestone's hard acceptance criteria (`verify`),
  build the comparison matrix (`compare`), and keep a page alive for a long interval
  (`endurance`).

## Requirements

Linux with:

- Node.js 22 or newer (developed and measured on Node 26.9.0)
- `ffmpeg` (frame decoding in the harness; capture for `remote-view`)
- `xdotool` (input injection for `remote-view`)
- `Xvfb` for `remote-view` only — not installed on the development host, so
  `tools/fetch-xvfb.mjs` extracts the distribution package into the prototype-local
  `vendor/` directory. No root, no system changes.
- An X server is **not** required for `frame-stream`, which runs headless.

## Setup

```bash
cd prototypes/embedded-browser-b0
npm install --no-audit --no-fund      # playwright 1.61.1 (pinned) + ws
npm run browsers                      # npx playwright install chromium (shared browser cache)
npm run fetch:xvfb                    # only needed for the remote-view candidate
```

Installed and measured versions are recorded in every report and in
`measurements/b0-comparison.json`; `common/versions.mjs` reads them from the running
host rather than hard-coding them.

## Run

```bash
# one candidate, end to end; prints B0_READY with the viewer/control URLs
npm run serve -- --candidate frame-stream
npm run serve -- --candidate remote-view

# then open the printed viewer URL in an ordinary browser to watch and control the page
```

Automated checks (all bounded, all loopback-only, none of them touch the live session):

```bash
# full script, both candidates, resource sampling, artefacts under measurements/
node tools/measure.mjs --label b0-final

# hard acceptance gate: exits non-zero if any B0 criterion fails
node tools/verify.mjs --label b0-final-verify

# ten minutes of actions and idle, proving the page is never replaced
node tools/endurance.mjs --candidates frame-stream --minutes 10

# comparison matrix + decision, read from the measurement JSON
node tools/compare.mjs
```

Useful narrower runs:

```bash
node tools/measure.mjs --candidates frame-stream --steps connect,viewer-typing,identity-agreement
node tools/verify.mjs --candidates remote-view --json
node tools/endurance.mjs --candidates frame-stream --minutes 2 --label b0-quick
```

## Isolation guarantees

Every child process is started through `common/env.mjs`, and the isolation is measured,
not assumed:

- throwaway `HAMES_HOME`, `XDG_RUNTIME_DIR`, `XDG_CACHE_HOME`, and `TMPDIR` per run,
  inside `.run/` (or `/tmp` where a short path is required by Chromium's socket);
- a Playwright-managed temporary browser profile — never `~/.config/chromium` or any
  personal profile, verified from `/proc/<pid>/cmdline` in the acceptance gate;
- `WAYLAND_DISPLAY` is removed from the child environment, so the browser cannot reach
  the live compositor; `remote-view` uses an isolated Xvfb display, never `:0`;
- every server binds `127.0.0.1` on an ephemeral port;
- no Hames gateway, MCP configuration, TUI, or REPL process is started or signalled.

## Layout

| Path | Purpose |
| --- | --- |
| `fixture/` | deterministic test site (two loopback origins) |
| `frame-stream/` | candidate 1: CDP screencast + CDP input |
| `remote-view/` | candidate 2: Xvfb + x11grab + xdotool |
| `common/` | shared config, isolated env, viewer transport/client, interaction script, JPEG metrics, proc sampling, harness helpers |
| `viewer/` | standalone viewer/control page |
| `tools/` | `serve`, `measure`, `verify`, `compare`, `endurance`, `fetch-browsers`, `fetch-xvfb` |
| `measurements/` | tracked evidence: per-candidate measurements, verify reports, endurance reports, comparison, control screenshots |
| `.run/`, `vendor/`, `measurements/frames/` | disposable per-run state, vendored Xvfb, bulky per-run frames (gitignored) |

## Known limitations of this prototype

- The viewer transport is unauthenticated and loopback-only, by design: authentication,
  origin validation, and control grants are B2 work. Do not expose it.
- The control endpoint used by the harness (`/control/*`) is prototype-internal. It lets
  the harness act as the "agent adapter" and is not part of any proposed product API.
- `frame-stream` streams page pixels only: no browser chrome, native dialogs, download
  shelf, permission prompts, or IME composition.
- `remote-view` has no window manager, so focus follows the pointer and no native window
  decorations are managed; its calibrated page origin assumes a stationary window.
- Neither candidate forwards clipboard, OS drag-and-drop, or file pickers.
- The measured latencies are viewer-action-to-visible-frame on the host that ran the
  measurement; they are not a promise for other hardware.
