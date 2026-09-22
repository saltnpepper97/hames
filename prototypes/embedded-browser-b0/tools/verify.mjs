#!/usr/bin/env node
// B0 acceptance gate.
//
//   node tools/verify.mjs                                  # both candidates
//   node tools/verify.mjs --candidates frame-stream
//   node tools/verify.mjs --label b0-verify --json
//
// Starts one candidate, drives the full interaction script through the viewer transport,
// then evaluates the milestone's hard acceptance criteria against the collected
// evidence. It writes measurements/<label>-verify-<candidate>.json and exits non-zero if
// any hard criterion fails. Nothing outside the prototype is started or stopped.

import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { MEASUREMENTS_DIR, PROTOTYPE_DIR, REPO_ROOT, RUNS_DIR } from "../common/paths.mjs";
import { VIEWPORT } from "../common/config.mjs";
import { createViewerClient } from "../common/viewer-client.mjs";
import { createInteractionScript } from "../common/interaction-script.mjs";
import {
  createControl,
  findBrowserProcess,
  readProcessCmdline,
  readProcessEnv,
  startCandidateServer,
  stopServer,
} from "../common/harness.mjs";

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? (argv[index + 1] ?? "true") : fallback;
};

const candidates = String(flag("candidates", "frame-stream,remote-view")).split(",").filter(Boolean);
const label = String(flag("label", "b0-verify"));
const asJson = argv.includes("--json");

/** @type {Array<{id: string, title: string, ok: boolean, evidence: unknown}>} */
const results = [];

/** @param {string} candidate */
async function verifyCandidate(candidate) {
  const artifactsDir = join(MEASUREMENTS_DIR, "frames", `${label}-${candidate}`);
  await mkdir(artifactsDir, { recursive: true });
  const server = await startCandidateServer({ candidate, label, logPath: join(artifactsDir, "server.log") });
  const control = createControl(server.ready.controlUrl);
  const viewer = createViewerClient({ wsUrl: server.ready.wsUrl, name: candidate });
  const isolation = await inspectIsolation({ candidate, control, ready: server.ready });

  const connectStartedAt = Date.now();
  await viewer.connect({ timeoutMs: 15000 });
  const connectMs = Date.now() - connectStartedAt;
  const firstFrameStartedAt = Date.now();
  await viewer.waitForFirstFrame(15000);
  const firstFrameMs = Date.now() - firstFrameStartedAt;

  const script = createInteractionScript({
    viewer,
    control,
    viewport: VIEWPORT,
    artifactsDir,
    log: (message) => console.log(`  [${candidate}] ${message}`),
    stopOnFailure: false,
  });
  await script.loadLayout();

  const startedAt = new Date().toISOString();
  const fixtureContract = await inspectFixture(control);
  const stateAtStart = await control.state();
  const steps = await script.run({ stepIds: null, record: () => {} });
  const stateAtEnd = await control.state();
  const fixtureStats = await control.fixtureStats();
  const framesAtEnd = await control.frames();
  const versions = (await control.versions()).versions;

  const step = (id) => steps.find((entry) => entry.id === id) ?? { id, ok: false, evidence: { missing: true } };
  /** @type {Array<{id: string, title: string, ok: boolean, evidence: unknown}>} */
  const criteria = [];

  criteria.push({
    id: "fixture-contract",
    title:
      "Disposable fixture exposes identity, form, counters, scrolling, keyboard, canvas, two-origin links, and a failing request",
    ok: Object.values(fixtureContract.checks).every((check) => check === true),
    evidence: fixtureContract,
  });

  criteria.push({
    id: "failing-request-is-real",
    title: "The deliberate failure is an HTTP 500 from the fixture, not a simulated label",
    ok:
      (fixtureStats.counts?.["GET /api/fail-once"] ?? 0) >= 1 &&
      (fixtureStats.counts?.["GET /api/fail"] ?? 0) >= 1 &&
      /500/.test(String(step("viewer-failing-request").evidence?.failStatus ?? "")),
    evidence: {
      failStatus: step("viewer-failing-request").evidence?.failStatus ?? null,
      counts: {
        failOnce: fixtureStats.counts?.["GET /api/fail-once"] ?? 0,
        fail: fixtureStats.counts?.["GET /api/fail"] ?? 0,
      },
      externalOriginRequests: fixtureStats.externalRequestCount,
    },
  });

  const connectEvidence = step("connect").evidence ?? {};
  criteria.push({
    id: "one-dedicated-page",
    title: "One dedicated Playwright page, no iframes, no extra page targets",
    ok:
      connectEvidence.pageIdentity?.contextPageCount === 1 &&
      connectEvidence.pageIdentity?.pageTargetCount === 1 &&
      connectEvidence.pageIdentity?.iframes === 0 &&
      connectEvidence.pageIdentity?.topLevel === true &&
      typeof connectEvidence.pageIdentity?.targetId === "string" &&
      connectEvidence.pageIdentity.targetId.length > 0,
    evidence: connectEvidence.pageIdentity ?? null,
  });

  criteria.push({
    id: "manual-input-visible-to-agent",
    title: "Typing and form submission through the viewer are visible to the Playwright adapter",
    ok:
      step("viewer-typing").ok === true &&
      step("viewer-typing").evidence?.fieldValue === step("viewer-typing").evidence?.typed &&
      step("viewer-submit").ok === true &&
      step("viewer-submit").evidence?.submitStatus === "ok 200" &&
      step("viewer-submit").evidence?.lastSubmitted === step("viewer-typing").evidence?.typed,
    evidence: { typing: step("viewer-typing").evidence ?? null, submit: step("viewer-submit").evidence ?? null },
  });

  criteria.push({
    id: "agent-action-visible-to-viewer",
    title: "A Playwright action (click and DOM write) is visible in the streamed pixels",
    ok: step("agent-action-visible").ok === true && step("agent-marker-visible").ok === true,
    evidence: {
      click: step("agent-action-visible").evidence ?? null,
      marker: step("agent-marker-visible").evidence ?? null,
    },
  });

  const agreement = step("identity-agreement").evidence ?? {};
  criteria.push({
    id: "identity-and-url-agreement",
    title: "URL, title, nonce, and field values agree, and the streamed pixels match the agent's own render",
    ok:
      step("identity-agreement").ok === true &&
      agreement.domPath?.url === stateAtEnd.url &&
      agreement.domPath?.fieldValue === stateAtEnd.fixture?.fieldValue &&
      (agreement.pixelPath?.contentPsnrDb ?? -1) >= 18 &&
      (agreement.pixelPath?.probeBackgroundDelta ?? 99) <= 26,
    evidence: agreement,
  });

  criteria.push({
    id: "reconnect-preserves-page",
    title: "Viewer disconnect and reconnect keeps the same page, target, URL, and running document",
    ok:
      step("viewer-disconnect-reconnect").ok === true &&
      step("viewer-disconnect-reconnect").evidence?.targetIdStable === true &&
      step("viewer-disconnect-reconnect").evidence?.pageClosed === false &&
      step("viewer-disconnect-reconnect").evidence?.heartbeatDuringGap >
        step("viewer-disconnect-reconnect").evidence?.heartbeatAtDisconnect,
    evidence: step("viewer-disconnect-reconnect").evidence ?? null,
  });

  criteria.push({
    id: "idle-does-not-reset-page",
    title: "Page survives an idle interval: same nonce, advancing heartbeat, never reset to about:blank",
    ok:
      step("idle").ok === true &&
      step("idle").evidence?.nonceStable === true &&
      step("idle").evidence?.heartbeatAfter > step("idle").evidence?.heartbeatBefore &&
      stateAtEnd.closed === false &&
      stateAtEnd.targetId === stateAtStart.targetId &&
      [stateAtEnd.url, stateAtStart.url].every((url) =>
        [server.ready.fixture.indexUrl, server.ready.fixture.secondUrl, server.ready.fixture.thirdUrl].some(
          (fixtureUrl) => String(url).startsWith(new URL(fixtureUrl).origin),
        ),
      ),
    evidence: {
      idle: step("idle").evidence ?? null,
      urlAtStart: stateAtStart.url,
      urlAtEnd: stateAtEnd.url,
      pageClosed: stateAtEnd.closed,
      targetStable: stateAtStart.targetId === stateAtEnd.targetId,
    },
  });

  const failedSteps = steps.filter((entry) => !entry.ok).map((entry) => entry.id);
  criteria.push({
    id: "all-supported-interactions",
    title: "Every interaction in the shared script succeeded (pointer, typing, keys, shortcut, canvas, scroll, both links, failing request)",
    ok: failedSteps.length === 0,
    evidence: { failedSteps, steps: steps.map((entry) => ({ id: entry.id, ok: entry.ok, ms: entry.ms })) },
  });

  criteria.push({
    id: "isolation",
    title: "No personal browser, no live display, throwaway profile/home/ports, loopback only, no Hames process touched",
    ok: Object.values(isolation.checks).every((check) => check.ok),
    evidence: isolation,
  });

  await viewer.close().catch(() => {});
  const cleanup = await stopServer(server.child);

  const report = {
    schema: 1,
    label,
    candidate,
    generatedAt: new Date().toISOString(),
    startedAt,
    viewport: VIEWPORT,
    versions,
    candidateInfo: server.ready.info,
    startup: { connectMs, firstFrameMs, viewerUrl: server.ready.viewerUrl },
    fixtureContract,
    isolation,
    criteria,
    steps,
    stepDigest: steps.map((entry) => ({ id: entry.id, ok: entry.ok, ms: entry.ms })),
    framesAtEnd,
    fixtureStats,
    cleanup,
    hardPass: criteria.every((entry) => entry.ok),
  };
  const outPath = join(MEASUREMENTS_DIR, `${label}-${candidate}.json`);
  await writeFile(outPath, `${JSON.stringify(report, null, 2)}\n`);
  return { report, outPath };
}

/**
 * Evidence that the run is isolated: throwaway profile/home/ports, loopback only, and the
 * browser process itself has no access to the live Wayland session.
 *
 * @param {{ candidate: string, control: ReturnType<typeof createControl>, ready: Record<string, any> }} options
 */
async function inspectIsolation(options) {
  const { candidate, control, ready } = options;
  const stack = await control.stack();
  const versions = await control.versions();
  const browser = findBrowserProcess(stack);
  const env = browser ? await readProcessEnv(browser.pid) : null;
  // The stack snapshot truncates cmdlines for readability, so read the browser's full
  // command line from /proc: --user-data-dir appears late in Chromium's argument list.
  const browserCmd = (browser ? await readProcessCmdline(browser.pid) : null) ?? browser?.cmd ?? "";
  const userDataDir = browserCmd.match(/--user-data-dir=(\S+)/)?.[1] ?? null;
  const home = process.env.HOME ?? "";
  /** @type {Array<{ pid: number, cmd: string }>} */
  const harnessProcesses = stack.processes;
  const repoRoot = resolve(REPO_ROOT);
  const prototypeDir = resolve(PROTOTYPE_DIR);
  const foreignHames = harnessProcesses.filter((entry) => {
    const stripped = entry.cmd.split(repoRoot).join("").split(prototypeDir).join("");
    return /hames/i.test(stripped);
  });
  // Profiles of the personal browsers on this host; the prototype must not use them.
  const personalProfiles = [
    `${home}/.config/chromium`,
    `${home}/.config/google-chrome`,
    `${home}/.config/google-chrome-for-testing`,
  ];

  const browserEnvKeys = env ? Object.keys(env) : [];
  // Current Chromium builds clear /proc/<pid>/environ down to a single variable after
  // startup, so a missing WAYLAND_DISPLAY there does not prove the launch environment.
  const browserEnvCleared = env !== null && browserEnvKeys.length <= 3;
  const ozonePlatform = browserCmd.match(/--ozone-platform(?:=|\s+)(\S+)/)?.[1] ?? null;
  const headlessArg = /(?:^|\s)--headless(?:=|\s|$)/.test(browserCmd);
  const cmdlineHasWayland = /wayland/i.test(browserCmd);
  /** @type {Array<{ pid: number, cmd: string, wayland: string | null, display: string | null, keyCount: number }>} */
  const siblingEnv = [];
  for (const entry of harnessProcesses) {
    if (/(chrome|chromium)/.test(entry.cmd)) continue;
    const procEnv = await readProcessEnv(entry.pid);
    if (!procEnv) continue;
    siblingEnv.push({
      pid: entry.pid,
      cmd: entry.cmd.split(" ")[0] ?? entry.cmd,
      wayland: procEnv.WAYLAND_DISPLAY ?? null,
      display: procEnv.DISPLAY ?? null,
      keyCount: Object.keys(procEnv).length,
    });
  }
  const parentDisplay = process.env.DISPLAY ?? null;
  const expectedDisplay = candidate === "remote-view" ? (ready.info?.display ?? null) : null;
  // /proc/<pid>/environ is the environment at exec. The candidate server is exec'd by
  // the harness with the caller's environment, then deletes WAYLAND_DISPLAY/DISPLAY
  // before spawning children. That deletion is visible to children, not to the
  // server's own /proc snapshot, so only spawned helpers count as leaks.
  const spawnedHelpers = siblingEnv.filter((entry) => !/(^|\/)node$/.test(entry.cmd));
  const waylandLeak = spawnedHelpers.some((entry) => entry.wayland);
  // Xvfb is started with the display number as an argument, before DISPLAY is exported
  // for the browser. ffmpeg/xdotool inherit DISPLAY only after calibration, which may be
  // later than this sample, so the Xvfb command line is the stable evidence.
  const xvfbCommandHasDisplay =
    Boolean(expectedDisplay) &&
    harnessProcesses.some((entry) => /Xvfb/.test(entry.cmd) && entry.cmd.includes(` ${expectedDisplay} `));
  const displayOnIsolatedServer =
    candidate === "remote-view"
      ? Boolean(expectedDisplay) &&
        expectedDisplay !== ":0" &&
        expectedDisplay !== parentDisplay &&
        xvfbCommandHasDisplay &&
        spawnedHelpers.every((entry) => !entry.display || entry.display === expectedDisplay)
      : spawnedHelpers.every((entry) => !entry.display);

  const checks = {
    loopbackOnly: {
      ok: [ready.viewerUrl, ready.wsUrl, ready.controlUrl, ready.fixture.baseUrl, ready.fixture.thirdUrl].every((url) =>
        /(^|\/\/)(127\.0\.0\.1|\[::1\])(:|\/)/.test(String(url).replace(/^ws:/, "http:")),
      ),
      urls: {
        viewer: ready.viewerUrl,
        ws: ready.wsUrl,
        control: ready.controlUrl,
        fixture: ready.fixture.baseUrl,
        externalOrigin: ready.fixture.thirdUrl,
      },
    },
    throwawayProfile: {
      ok:
        typeof userDataDir === "string" &&
        userDataDir.length > 0 &&
        !personalProfiles.some((profile) => userDataDir.startsWith(profile)) &&
        !userDataDir.startsWith(`${home}/.local/share/hames`),
      userDataDir,
      reportedProfileDir: ready.info?.profileDir ?? null,
      personalProfilesNotUsed: personalProfiles,
      browserPid: browser?.pid ?? null,
    },
    throwawayStateRoots: {
      ok:
        String(ready.runDir).startsWith(RUNS_DIR) &&
        (versions.isolatedTempRoots ?? []).every((dir) => dir.startsWith("/tmp/")),
      runDir: ready.runDir,
      hamesHome: `${ready.runDir}/hames-home`,
      isolatedTempRoots: versions.isolatedTempRoots ?? [],
    },
    noWaylandForBrowser: {
      ok:
        env !== null &&
        env.WAYLAND_DISPLAY === undefined &&
        !cmdlineHasWayland &&
        !waylandLeak &&
        Boolean(displayOnIsolatedServer) &&
        (candidate === "frame-stream"
          ? headlessArg && ozonePlatform !== "wayland"
          : ozonePlatform === "x11" && Boolean(expectedDisplay)),
      waylandDisplay: env?.WAYLAND_DISPLAY ?? null,
      display: env?.DISPLAY ?? null,
      browserEnvCleared,
      browserEnvKeyCount: browserEnvKeys.length,
      ozonePlatform,
      headlessArg,
      cmdlineHasWayland,
      expectedDisplay,
      siblingEnv,
      waylandLeak,
      note: browserEnvCleared
        ? "Chromium cleared its environ after launch; Wayland/display evidence is taken from the server, Xvfb, ffmpeg, and xdotool processes plus the browser command line"
        : "browser environ still readable",
      browserPid: browser?.pid ?? null,
    },
    isolatedDisplayNotLive: {
      ok:
        candidate === "frame-stream"
          ? headlessArg && !waylandLeak && spawnedHelpers.every((entry) => !entry.display)
          : Boolean(expectedDisplay) &&
            expectedDisplay !== ":0" &&
            expectedDisplay !== parentDisplay &&
            ozonePlatform === "x11" &&
            !waylandLeak,
      display: env?.DISPLAY ?? null,
      expectedDisplay,
      parentDisplay,
      ozonePlatform,
      headless: ready.info?.headless ?? null,
      note:
        candidate === "remote-view"
          ? "headed Chromium on a prototype-local Xvfb display, never the live session display"
          : "headless Chromium; the server process has no DISPLAY",
    },
    noHamesProcessStarted: {
      ok: foreignHames.length === 0,
      processCount: harnessProcesses.length,
      suspicious: foreignHames,
      note:
        "the harness's own process tree contains only node, chromium and (remote-view) Xvfb/ffmpeg/xdotool; no Hames gateway or service process is started or signalled",
      binaries: [...new Set(harnessProcesses.map((entry) => entry.cmd.split(" ")[0]))],
      processes: harnessProcesses.map((entry) => `${entry.pid} ${entry.cmd.slice(0, 120)}`),
    },
    sameOriginIsolationForPages: {
      ok: new URL(ready.viewerUrl).origin !== new URL(ready.fixture.baseUrl).origin,
      note: "the fixture page and the viewer page run on different loopback origins/ports; the viewer holds no CDP or gateway credential",
      viewerOrigin: new URL(ready.viewerUrl).origin,
      fixtureOrigin: new URL(ready.fixture.baseUrl).origin,
    },
  };
  return { checks, browser, userDataDir, runDir: ready.runDir, versionsReport: versions.versions };
}

/**
 * Inspect the fixture document directly, so "the fixture has these widgets" is a
 * measurement rather than a claim about the HTML files.
 *
 * @param {ReturnType<typeof createControl>} control
 * @returns {Promise<{ checks: Record<string, boolean>, observed: Record<string, unknown> }>}
 */
async function inspectFixture(control) {
  return await control.evaluate(`(() => {
    const byId = (id) => document.getElementById(id);
    const text = (id) => (byId(id)?.textContent ?? "").trim();
    const present = (ids) => ids.every((id) => byId(id) !== null);
    const nonEmpty = (ids) => ids.every((id) => text(id).length > 0);
    const canvas = byId("canvas");
    const area = byId("scroll-area");
    const internal = byId("internal-link");
    const external = byId("external-link");
    const identity = ["page-nonce","url-display","title-display","session-id","generation","viewport-size","uptime","load-ts"];
    const counters = ["probe-count","submit-count","key-count","shortcut-count","scroll-area-y","doc-scroll-y","scroll-events","fail-count"];
    const checks = {
      topLevelDocument: window.self === window.top,
      noIframes: document.querySelectorAll("iframe").length === 0,
      identityNonceLooksUnique: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text("page-nonce")),
      identityFieldsFilled: nonEmpty(identity),
      displayUrlMatchesLocation: text("url-display") === location.href,
      displayTitleMatchesDocument: text("title-display") === document.title,
      formWithTextInput: Boolean(document.querySelector("form#form input#text-field")) && byId("submit-btn") !== null,
      countersPresent: present(counters),
      keyboardFeedbackPresent: present(["key-count","last-key"]),
      pointerCanvasPresent: canvas instanceof HTMLCanvasElement && canvas.width >= 100 && canvas.height >= 100,
      internalLinkSameOrigin: internal ? new URL(internal.href).origin === location.origin : false,
      externalLinkCrossOrigin: external ? new URL(external.href).origin !== location.origin : false,
      externalLinkNotPlaceholder: external ? !external.href.includes("__EXTERNAL_ORIGIN__") : false,
      scrollAreaScrollable: area ? area.scrollHeight > area.clientHeight : false,
      scrollAreaHasRows: (byId("scroll-items")?.children.length ?? 0) >= 20,
      documentScrollable: document.documentElement.scrollHeight > innerHeight + 200,
      failingRequestShown: /500/.test(text("fail-status")),
      fixtureStateFunctionExposed: typeof window.__FIXTURE_STATE__ === "function",
      originMarkerPresent: byId("origin-marker") !== null,
    };
    return {
      checks,
      observed: {
        identity: Object.fromEntries(identity.map((id) => [id, text(id)])),
        counters: Object.fromEntries(counters.map((id) => [id, text(id)])),
        internalHref: internal?.href ?? null,
        externalHref: external?.href ?? null,
        scrollRows: byId("scroll-items")?.children.length ?? 0,
        failStatus: text("fail-status"),
        bodyClass: document.body.className,
      },
    };
  })()`);
}

for (const candidate of candidates) {
  console.log(`== verifying ${candidate} ==`);
  const { report, outPath } = await verifyCandidate(candidate);
  results.push(report);
  console.log(`  report: ${outPath}`);
  for (const criterion of report.criteria) {
    console.log(`  ${criterion.ok ? "PASS" : "FAIL"}  ${criterion.id} — ${criterion.title}`);
  }
  console.log(`  ${report.hardPass ? "ALL CRITERIA PASS" : "CRITERIA FAILED"} for ${candidate}`);
}

const summary = {
  label,
  generatedAt: new Date().toISOString(),
  candidates: results.map((report) => ({
    candidate: report.candidate,
    hardPass: report.hardPass,
    failed: report.criteria.filter((criterion) => !criterion.ok).map((criterion) => criterion.id),
    stepsOk: report.steps.filter((entry) => entry.ok).length,
    stepsTotal: report.steps.length,
    report: `${label}-${report.candidate}.json`,
  })),
  allPass: results.every((report) => report.hardPass),
};
await writeFile(join(MEASUREMENTS_DIR, `${label}-summary.json`), `${JSON.stringify(summary, null, 2)}\n`);
console.log(asJson ? JSON.stringify(summary, null, 2) : `\n${summary.candidates.map((entry) => `${entry.candidate}: ${entry.stepsOk}/${entry.stepsTotal} steps, hard checks ${entry.hardPass ? "pass" : `FAIL (${entry.failed.join(", ")})`}`).join("\n")}`);
process.exit(summary.allPass ? 0 : 1);
