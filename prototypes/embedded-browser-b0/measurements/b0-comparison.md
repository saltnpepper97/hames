# Embedded browser B0 — transport comparison

Generated 2026-09-21T23:47:05.640Z by `node tools/compare.mjs` from measurement labels: b0-final.

Both candidates were driven by the same interaction script, the same fixture, the same
viewer wire protocol, the same viewport, and the same resource sampler. Only the
capture and input mechanism differs.

| Metric | frame-stream | remote-view | Notes |
| --- | --- | --- | --- |
| Input → visible latency, p50 | 30 ms | 115 ms | viewer action to the frame that shows its effect |
| Input → visible latency, p95 | 78 ms | 138 ms | same probe, 95th percentile |
| Viewer connect | 28 ms | 22 ms | WebSocket open to hello |
| First frame after connect | 4 ms | 141 ms | hello to first binary frame |
| Streamed frame size | 1280×800 | 1280×887 | remote-view includes browser chrome in the same frame |
| Page pixels per frame | 1280×800 | 1280×800 | both stream the same page viewport |
| Encoding | CDP Page.startScreencast quality=70 | ffmpeg -c:v mjpeg -q:v 4 | JPEG quality settings differ; both are JPEG on the same wire protocol |
| Active-phase CPU (whole stack, one core = 100%) | 10.88% | 13.43% | browser + transport + capture processes |
| Idle-phase CPU | 3.22% | 32.99% | page visible, nobody acting |
| Active bandwidth | 2224.1 kbit/s | 3189.5 kbit/s | bytes actually sent to the viewer |
| Idle bandwidth | 823.8 kbit/s | 1271.8 kbit/s | the fixture clock ticks once per second, so idle is not a frozen page |
| Average bytes per frame | 81304.1 | 129639.9 |  |
| Frames/s reaching the viewer (active) | 3.42 | 3.08 | frame-stream is change-driven |
| Frames/s captured before dedup | not applicable (change-driven) | 26.16 | x11grab samples at a fixed rate whether or not anything changed |
| Whole-stack memory (RSS) | 1048 MB | 1177.7 MB | all candidate descendants |
| Image fidelity vs. Playwright render (content PSNR) | 35.08 dB | 41.53 dB | higher is closer to the agent's own render |
| Identity text (nonce) ink IoU | 0.955 | 0.986 | glyph overlap after calibration |
| Identity text (URL) ink IoU | 0.958 | 0.981 |  |
| Form value ink IoU | 0.979 | 0.987 | viewer-typed value as the agent renders it |
| Interaction steps passing | 18/18 | 18/18 | same script, both candidates |
| Reconnect behaviour | viewer reconnects and receives a fresh snapshot frame | viewer reconnects, window is re-raised, capture restarts | page, target, nonce, and URL preserved in both |
| Page identity across the run | stable target 72D19C38…, 1 page(s) | stable target 00FFA859…, 1 page(s) |  |
| Extra host requirements | Playwright-managed Chromium only | Xvfb + xdotool + ffmpeg; a headed browser on a virtual display | remote-view cannot run on a host without an X server |
| Browser chrome, native dialogs, IME, downloads | not represented (page pixels only) | represented, because the capture is of a real window | the central trade-off between the two paths |
| Documented limitations | 5 items | 6 items | listed in full in the JSON output and the milestone evidence |

## Dependency cost

| | frame-stream | remote-view |
| --- | --- | --- |
| Prototype npm dependencies | playwright@1.61.1, ws | playwright@1.61.1, ws |
| Browser | /home/dustin/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome (Playwright-managed download, reused from the shared browser cache) | /home/dustin/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome (headed) |
| Extra host packages | none beyond Node and a Playwright-managed Chromium | Xvfb 21.1.24-1 (vendored from xorg-server-xvfb), xdotool (XTEST input injection), ffmpeg (x11grab capture and jpeg encode) |
| Vendored Xvfb binary | not used | 1.9 MB |
| Install command | `node tools/fetch-browsers.mjs (wraps: npx playwright install chromium)` | `node tools/fetch-xvfb.mjs (downloads the distro package into prototype-local vendor/)` |

Node modules in the prototype: 18M (shared by both candidates).

## Supported interactions (measured)

connect, pointer-move-canvas, pointer-click-probe, viewer-typing, viewer-submit, viewer-key-probe, viewer-shortcut, viewer-canvas-drag, viewer-scroll, viewer-internal-link, viewer-back-to-index, viewer-external-link, viewer-failing-request, agent-action-visible, agent-marker-visible, identity-agreement, viewer-disconnect-reconnect, idle

## Interactions known to be missing or unsupported (not measured, from design review)

- Page images only: no browser chrome, native dialogs, download shelves, or permission prompts are visible.
- CDP screencast is change-driven; a fully static page produces no frames (no keepalive frame for a new viewer beyond the initial snapshot).
- IME/composition input is not implemented (Input.dispatchKeyEvent only).
- Clipboard, drag-and-drop from OS, and file pickers are not forwarded.
- Headless rendering can differ subtly from a headed browser (fonts, GPU rasterisation, scrollbars).
- Requires an Xvfb binary; on this host it is fetched into prototype-local vendor/ because installing packages needs root.
- No window manager: focus follows the pointer, there is no window list, and native dialogs/child windows are not managed.
- Fixed-rate x11grab capture costs CPU even when idle; identical frames are dropped before transmission but still encoded.
- Screen capture carries browser chrome pixels as well as page pixels, so bandwidth is not directly comparable per page pixel.
- Pointer coordinates depend on the detected page origin inside the window; a moving/resized window invalidates it.

## Decision

**Chosen transport: frame-stream.**

Why:

- Input latency: 30 ms vs 115 ms at p50.
- Idle CPU: 3.22% vs 32.99% of one core while the page is untouched.
- Host requirements: frame-stream needs no X server, no xdotool, and no Xvfb; the browser lives headless on the gateway host.
- Frames only move when the page changes, so an idle browser costs almost nothing to watch.

Accepted limitations of the chosen transport:

- Page images only: no browser chrome, native dialogs, download shelves, or permission prompts are visible.
- CDP screencast is change-driven; a fully static page produces no frames (no keepalive frame for a new viewer beyond the initial snapshot).
- IME/composition input is not implemented (Input.dispatchKeyEvent only).
- Clipboard, drag-and-drop from OS, and file pickers are not forwarded.
- Headless rendering can differ subtly from a headed browser (fonts, GPU rasterisation, scrollbars).

The rejected candidate is kept as an isolated prototype only: an isolated, non-shipping prototype under prototypes/embedded-browser-b0/remote-view/, retained only as evidence and as a reference path for hosts that need native chrome.

Follow-ups this decision creates:

- B2 must define text/IME, clipboard, and keyboard-shortcut handling that this prototype does not implement.
- B5 must decide popup, upload, download, certificate, and modal-dialog behaviour explicitly, because a page-image stream does not show browser chrome.
- If a future milestone needs native dialogs or IME, the remote-view prototype is the reference implementation, not a fallback to attach silently.
