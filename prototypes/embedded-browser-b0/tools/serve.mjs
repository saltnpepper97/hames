#!/usr/bin/env node
// Start one candidate end to end: fixture site, browser, frame transport, viewer
// route, and a prototype-internal control API used only by the harness.
//
//   node tools/serve.mjs --candidate frame-stream
//   node tools/serve.mjs --candidate remote-view
//
// Prints a single machine-readable line when ready:
//   B0_READY {"viewerUrl": "...", "wsUrl": "...", "controlUrl": "..."}
// and stays alive until SIGINT/SIGTERM.
//
// Everything binds to 127.0.0.1 only. No Hames module, gateway, or MCP config is
// touched; the browser profile and HAMES_HOME are throwaway directories.

import { createServer } from "node:http";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import { startFixture } from "../fixture/server.mjs";
import { createViewerTransport } from "../common/viewer-transport.mjs";
import { createFrameStreamCandidate } from "../frame-stream/candidate.mjs";
import { createRemoteViewCandidate } from "../remote-view/candidate.mjs";
import { createRunDir, PROTOTYPE_DIR } from "../common/paths.mjs";
import { VIEWPORT, XVFB_SCREEN } from "../common/config.mjs";
import { detectClockTicks, sampleStack } from "../common/proc.mjs";
import { cleanupIsolatedTemps, isolatedTempRoots } from "../common/env.mjs";
import { collectVersions } from "../common/versions.mjs";

// Drop the live session before any child is spawned. Chromium later clears its own
// /proc environ, so absence of WAYLAND_DISPLAY there is not evidence; the server
// process must not have it to pass on in the first place. remote-view sets DISPLAY
// only on the Xvfb child environment.
delete process.env.WAYLAND_DISPLAY;
delete process.env.DISPLAY;

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? (argv[index + 1] ?? "true") : fallback;
};

const candidateName = flag("candidate", "frame-stream");
const keepRun = process.argv.includes("--keep-run");
const viewport = VIEWPORT;

const viewersDir = join(PROTOTYPE_DIR, "viewer");

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

function json(res, status, value) {
  const payload = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(payload) });
  res.end(payload);
}

const runDir = await createRunDir(candidateName);
const runCleanup = async () => {
  if (!keepRun) await rm(runDir, { recursive: true, force: true }).catch(() => {});
};

await detectClockTicks();
const fixture = await startFixture({ host: "127.0.0.1" });

/** @type {Record<string, unknown>} */
let candidateInfo = { candidate: candidateName, viewport };
/** @type {Record<string, unknown> | null} */
let cachedState = null;

const transport = await createViewerTransport({
  host: "127.0.0.1",
  port: 0,
  viewerDir: viewersDir,
  hello: () => ({
    candidate: candidateName,
    viewport,
    frameSize: /** @type {Record<string, unknown>} */ (candidateInfo.frameSize ?? viewport),
    pageOrigin: /** @type {Record<string, unknown>} */ (candidateInfo.contentOrigin ?? { x: 0, y: 0 }),
    chromeHeight: candidateInfo.chromeHeight ?? 0,
    display: candidateInfo.display ?? null,
    protocol: "b0-jpeg-frames",
    page: cachedState,
  }),
});

const candidate =
  candidateName === "remote-view"
    ? await createRemoteViewCandidate({ runDir, fixture, viewport, transport })
    : await createFrameStreamCandidate({ runDir, fixture, viewport, transport });

candidateInfo = await candidate.start();

const control = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  try {
    if (url.pathname === "/control/info") {
      json(res, 200, {
        candidate: candidateName,
        info: candidateInfo,
        fixture: {
          baseUrl: fixture.baseUrl,
          externalUrl: fixture.externalUrl,
          indexUrl: fixture.indexUrl,
          secondUrl: fixture.secondUrl,
          thirdUrl: fixture.thirdUrl,
        },
        transport: { url: transport.url, wsUrl: transport.wsUrl, port: transport.port },
        viewport,
        xvfbScreen: XVFB_SCREEN,
      });
      return;
    }
    if (url.pathname === "/control/state") {
      const state = await candidate.tools.snapshot();
      json(res, 200, state);
      return;
    }
    if (url.pathname === "/control/fixture-stats") {
      json(res, 200, await fixture.stats());
      return;
    }
    if (url.pathname === "/control/stack") {
      json(res, 200, await sampleStack(process.pid));
      return;
    }
    if (url.pathname === "/control/frames") {
      json(res, 200, {
        candidate: candidate.frameStats,
        capture: candidate.captureStats?.() ?? null,
        transport: transport.stats,
        clients: transport.clientCount(),
      });
      return;
    }
    if (url.pathname === "/control/versions") {
      json(res, 200, { versions: await collectVersions(), isolatedTempRoots: isolatedTempRoots() });
      return;
    }
    if (url.pathname === "/control/action") {
      const body = await readJsonBody(req);
      const action = String(body.type ?? "");
      const tools = candidate.tools;
      let result = null;
      switch (action) {
        case "click":
          await tools.click(String(body.selector));
          break;
        case "type":
          await tools.type(String(body.selector), String(body.text ?? ""));
          break;
        case "fill":
          await tools.fill(String(body.selector), String(body.value ?? ""));
          break;
        case "press":
          await tools.press(String(body.key));
          break;
        case "marker":
          await tools.setMarker(String(body.text ?? ""));
          break;
        case "goto":
          await tools.goto(String(body.url));
          break;
        case "evaluate":
          result = await tools.evaluate(String(body.expression));
          break;
        case "screenshot":
          result = await tools.screenshot(String(body.path));
          break;
        case "focus":
          await tools.focusPage();
          break;
        default:
          json(res, 400, { error: `unknown action ${action}` });
          return;
      }
      json(res, 200, { ok: true, result, state: await tools.snapshot() });
      return;
    }
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("not found");
  } catch (error) {
    json(res, 500, { error: String(error) });
  }
});

await new Promise((resolve) => control.listen(0, "127.0.0.1", resolve));
const controlAddress = control.address();
const controlPort = typeof controlAddress === "object" && controlAddress !== null ? controlAddress.port : 0;

// Periodic page state so the viewer (and the harness) can show what the streamed
// page is, without the viewer ever reaching for CDP.
const stateTimer = setInterval(async () => {
  try {
    cachedState = await candidate.tools.snapshot();
    transport.sendState({ kind: "page", ...cachedState });
  } catch (error) {
    transport.sendState({ kind: "page", error: String(error) });
  }
}, 1000);
stateTimer.unref?.();

const ready = {
  candidate: candidateName,
  runDir,
  viewerUrl: transport.url,
  wsUrl: transport.wsUrl,
  viewerPort: transport.port,
  controlUrl: `http://127.0.0.1:${controlPort}`,
  fixture: {
    baseUrl: fixture.baseUrl,
    externalUrl: fixture.externalUrl,
    indexUrl: fixture.indexUrl,
    secondUrl: fixture.secondUrl,
    thirdUrl: fixture.thirdUrl,
  },
  info: candidateInfo,
  versions: await collectVersions(),
  pid: process.pid,
};
console.log(`B0_READY ${JSON.stringify(ready)}`);

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  clearInterval(stateTimer);
  console.log(`B0_STOPPING ${signal}`);
  await candidate.stop().catch(() => {});
  await transport.close().catch(() => {});
  await fixture.stop().catch(() => {});
  await new Promise((resolve) => control.close(resolve));
  await cleanupIsolatedTemps();
  await runCleanup();
  console.log("B0_STOPPED");
  process.exit(0);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
