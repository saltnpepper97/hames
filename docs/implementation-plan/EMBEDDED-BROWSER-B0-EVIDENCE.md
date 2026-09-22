# Embedded browser B0 — evidence

Status: **pending**. B0 is not accepted and is not a production go.

The prototype lives only under `prototypes/embedded-browser-b0/`. It does not import
Hames, and it does not change the gateway, MCP configuration, or production web UI.
`docs/implementation-plan/EMBEDDED-BROWSER.md` leaves every B0 box unchecked.

## What was reviewed

This file separates checks that were re-run from records that only exist as files
from earlier automated runs. File presence is not treated as a completed check.

### Re-run in review (2026-09-22)

Commands, from `prototypes/embedded-browser-b0`, Node v26.9.0:

```text
node --check  # every prototype .mjs plus viewer/viewer.js — syntax ok
node --input-type=module  # startFixture smoke
node tools/verify.mjs --candidates frame-stream,remote-view --label reviewer-b0
```

Fixture smoke: index HTTP 200, `__EXTERNAL_ORIGIN__` replaced, canvas and form
present, second origin HTTP 200, `GET /api/fail` HTTP 500 with
`{"error":"deliberate-failure","endpoint":"/api/fail"}`.

`verify.mjs` exit 0. Both candidates: 18/18 scripted steps and every hard criterion
in that script passed, including isolation after the gate was corrected (below).
Reports: `measurements/reviewer-b0-frame-stream.json`,
`measurements/reviewer-b0-remote-view.json`, `measurements/reviewer-b0-summary.json`.

That run is an automated viewer client plus the harness control API. It is not a
person using the standalone viewer. The idle step inside it is about 10 seconds,
not ten minutes.

Observed input-to-visible samples from this run only (not a full latency study):

| Candidate | Probe | Milliseconds |
| --- | --- | --- |
| frame-stream | pointer click | 31 |
| frame-stream | keyboard | 16 |
| remote-view | pointer click | 99 |
| remote-view | keyboard | 81 |

Remote-view used vendored Xvfb display `:90` (`--ozone-platform=x11`), not the live
Wayland session. Frame-stream was headless (`--ozone-platform=headless`). No gateway
was restarted.

### Earlier automated records, not re-run here

| Record | What it claims | Review treatment |
| --- | --- | --- |
| `measurements/b0-final-summary.json` and `b0-comparison.md` | Both candidates, 18/18 steps, CPU, bandwidth, PSNR, and a frame-stream decision. Generated 2026-09-21T23:45–23:47Z. | Not remeasured. Do not cite the CPU/bandwidth/PSNR figures as confirmed by this review. |
| `measurements/b0-endurance-frame-stream-endurance.json` | frame-stream only, `requestedMinutes` 10, `actualMs` 600063, heartbeat 0→600, same nonce and target, `pass: true`. Checkpoints 2026-09-21T23:58:44Z through 2026-09-22T00:07:51Z. | Consistent automated log. Not repeated. No remote-view endurance file. No human observation field. |
| `measurements/b0-check-summary.json` | frame-stream hard gate failed `one-dedicated-page` and `isolation` at 23:43Z. Isolation evidence had `userDataDir: null`. The stored page-identity object already showed one page and a target id; this review did not reconstruct why that check failed. | Superseded by later full-command-line reads (`/tmp/playwright_chromiumdev_profile-…`) and by `reviewer-b0`. |

## Acceptance criteria

| Plan item | Evidence | Status |
| --- | --- | --- |
| Deterministic fixture: identity, URL/title, form, counters, links, scrolling, keyboard, canvas, visible failed request | Fixture files plus review fixture smoke and `fixture-contract` / `failing-request-is-real` on both candidates | Met by automation |
| One dedicated Playwright page | `one-dedicated-page` passed on both candidates in `reviewer-b0` | Met by automation |
| Compare frame-stream and virtual-display/remote-view, including CPU, bandwidth, latency, reconnect | Both candidates run the same script. Resource and image-quality numbers exist only in the earlier measurement JSON | Partial. Functional compare re-run; performance numbers not re-run |
| Choose a transport and document limitations | `measurements/b0-comparison.md` chooses frame-stream and lists page-image limits (no chrome, dialogs, IME, downloads, clipboard) | Recorded by the earlier compare script, not a new measurement |
| Live view forwards pointer, keyboard, and scroll | Scripted viewer client, both candidates, `reviewer-b0` | Met by automation, not by a person |
| Viewer typing visible to Playwright, and a Playwright action visible in the view; URL and field values agree | `manual-input-visible-to-agent`, `agent-action-visible-to-viewer`, `identity-and-url-agreement` | Met by automation. The word "manual" in the criterion id means "through the viewer transport", not a human |
| Page alive at least ten minutes across actions and idle, no silent `about:blank` | Builder endurance JSON for frame-stream only | Not re-run; not done for remote-view; not watched by a person |
| Disconnect and reconnect the viewer on the same page | `reconnect-preserves-page` in `reviewer-b0` (scripted socket close) | Met by automation |
| Go/no-go, prototype isolated from production | Code is confined to the prototype directory. Decision text prefers frame-stream. Milestone not closed | No-go for production until the blockers below are done |

Plan acceptance still requires a human to operate the test site through the view
while the adapter observes the same page. That did not happen in this review.

## Isolation defect fixed during review

`verify.mjs` previously marked isolation successful when Chromium's `/proc`
environment was empty. Chromium clears that environment after launch, so a missing
`WAYLAND_DISPLAY` was not evidence. The remote-view report from the earlier
`b0-final-verify` run had `displayMatches: false` (expected `:90`, observed display
unset) and still set `hardPass: true`.

The gate now requires a headless Ozone platform for frame-stream, `x11` plus an
Xvfb display other than `:0` and other than the caller's `DISPLAY` for remote-view,
a non-personal `--user-data-dir`, and no Wayland display on spawned helpers.
`tools/serve.mjs` deletes `WAYLAND_DISPLAY` and `DISPLAY` before it spawns children.
Version probes do the same. `/proc/<pid>/environ` for the Node server stays the
exec-time snapshot, so the gate does not treat that snapshot as a leak.

## Blockers before B0 can be checked

1. A person must open the printed viewer URL and operate the fixture (type, scroll,
   pointer, reconnect) while Playwright reads the same page. Record what was seen.
2. Accept or repeat the ten-minute endurance run. The existing file is frame-stream
   only and was not repeated here.
3. Re-run `node tools/measure.mjs` if the CPU, bandwidth, and image-quality figures
   in `b0-comparison.md` are going to be cited as current.
4. Do not check B0, and do not start B1, on the automated gate alone.
