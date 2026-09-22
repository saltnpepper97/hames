#!/usr/bin/env node
// B0 endurance check: keep one shared page alive for a long interval while several
// actions happen and the page is left idle, and prove it is never replaced.
//
//   node tools/endurance.mjs --candidates frame-stream --minutes 10
//   node tools/endurance.mjs --candidates remote-view --minutes 2 --label b0-quick
//
// What it verifies, once per checkpoint:
//   * the URL is still the fixture (never about:blank and never a new document by accident)
//   * the Playwright page target is the same target as at the start and is not closed
//   * the document nonce (per-document identity) is unchanged
//   * the fixture heartbeat is still advancing, so the page is executing
//   * viewer input still works (keyboard, pointer, wheel) and a Playwright action is still
//     visible in the streamed pixels after several minutes
//   * the viewer can disconnect and reconnect without disturbing the page
//   * the browser process is still the same process, with resident memory recorded
//
// It writes measurements/<label>-<candidate>-endurance.json and exits non-zero on failure.

import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { MEASUREMENTS_DIR } from "../common/paths.mjs";
import { VIEWPORT } from "../common/config.mjs";
import { detectClockTicks, sampleStack } from "../common/proc.mjs";
import { createViewerClient } from "../common/viewer-client.mjs";
import { createInteractionScript } from "../common/interaction-script.mjs";
import { createControl, findBrowserProcess, sleep, startCandidateServer, stopServer } from "../common/harness.mjs";

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? (argv[index + 1] ?? "true") : fallback;
};

const candidates = String(flag("candidates", "frame-stream")).split(",").filter(Boolean);
const minutes = Number(flag("minutes", "10"));
const label = String(flag("label", "b0-endurance"));
/** One checkpoint per interval: a short activity burst followed by an idle stretch. */
const checkpointSeconds = Number(flag("checkpoint-seconds", "60"));
const clockTicks = await detectClockTicks();

/** @param {string} candidate */
async function runEndurance(candidate) {
  const artifactsDir = join(MEASUREMENTS_DIR, "frames", `${label}-${candidate}`);
  await mkdir(artifactsDir, { recursive: true });
  const server = await startCandidateServer({ candidate, label, logPath: join(artifactsDir, "server.log") });
  const control = createControl(server.ready.controlUrl);
  const viewer = createViewerClient({ wsUrl: server.ready.wsUrl, name: candidate });
  await viewer.connect({ timeoutMs: 15000 });
  await viewer.waitForFirstFrame(15000);

  const script = createInteractionScript({
    viewer,
    control,
    viewport: VIEWPORT,
    artifactsDir,
    log: () => {},
    stopOnFailure: false,
  });
  await script.loadLayout();
  const { prepare } = script.helpers;

  const startState = await control.state();
  const startStack = await sampleStack(process.pid);
  const browserPid = findBrowserProcess(startStack)?.pid ?? null;
  const startedAt = Date.now();
  const deadline = startedAt + minutes * 60 * 1000;
  const plannedCheckpoints = Math.max(2, Math.floor((minutes * 60) / checkpointSeconds));
  const fixtureOrigins = [server.ready.fixture.indexUrl, server.ready.fixture.secondUrl, server.ready.fixture.thirdUrl].map(
    (url) => new URL(url).origin,
  );

  /** @type {Array<Record<string, unknown>>} */
  const checkpoints = [];
  /** @type {string[]} */
  const failures = [];
  let previousStack = startStack;
  let viewerReconnects = 0;
  let checkpointIndex = 0;

  /** Poll the page until the predicate holds, then return that state. */
  async function waitUntil(predicate, timeoutMs = 8000) {
    const limit = Date.now() + timeoutMs;
    let state = await control.state();
    while (Date.now() < limit) {
      state = await control.state();
      if (predicate(state)) return state;
      await sleep(200);
    }
    throw new Error(`condition not met within ${timeoutMs}ms (url=${state.url})`);
  }

  /** Run one check without letting its failure abort the whole endurance run. */
  async function attempt(checks, name, work) {
    try {
      checks[name] = (await work()) === true;
    } catch (error) {
      checks[name] = false;
      checks[`${name}Error`] = String(error).slice(0, 200);
    }
  }

  /**
   * A short burst of real interactions, exercised through the viewer transport, plus one
   * Playwright-side action whose effect must still reach the streamed pixels.
   *
   * @param {number} index
   * @param {boolean} withPixelCheck
   */
  async function activityBurst(index, withPixelCheck) {
    /** @type {Record<string, unknown>} */
    const checks = {};
    const before = await control.state();

    // Keyboard path: plain "p" toggles the latency probe while focus is not in a field.
    await attempt(checks, "keyboardTogglesProbe", async () => {
      viewer.sendInput({ kind: "key", action: "down", key: "p", code: "KeyP", modifiers: {} });
      viewer.sendInput({ kind: "key", action: "up", key: "p", code: "KeyP", modifiers: {} });
      const state = await waitUntil((snapshot) => snapshot.fixture.probeState !== before.fixture.probeState);
      return state.fixture.probeState !== before.fixture.probeState;
    });

    // Pointer path: clicking the toggle button through the viewer.
    await attempt(checks, "pointerTogglesProbe", async () => {
      const baseline = await control.state();
      const rect = await prepare("#probe-btn");
      viewer.sendInput({ kind: "pointer", action: "move", x: rect.centerX, y: rect.centerY });
      await sleep(60);
      viewer.sendInput({ kind: "pointer", action: "down", x: rect.centerX, y: rect.centerY, button: "left", clickCount: 1 });
      await sleep(60);
      viewer.sendInput({ kind: "pointer", action: "up", x: rect.centerX, y: rect.centerY, button: "left", clickCount: 1 });
      const state = await waitUntil((snapshot) => snapshot.fixture.probeState !== baseline.fixture.probeState);
      return state.fixture.probeState !== baseline.fixture.probeState;
    });

    // Wheel path. The direction alternates so the burst cannot get stuck at an end stop;
    // it starts downwards because a fresh page is already at the top of its scroll area.
    const scrollProbe = { before: null, after: null };
    await attempt(checks, "wheelScrolls", async () => {
      const area = await prepare("#scroll-area");
      scrollProbe.before = await control.state();
      viewer.sendInput({
        kind: "wheel",
        x: area.centerX,
        y: area.centerY,
        deltaX: 0,
        deltaY: index % 2 === 1 ? 200 : -200,
      });
      const state = await waitUntil((snapshot) => {
        const movedInner = snapshot.fixture.scrollAreaY !== scrollProbe.before.fixture.scrollAreaY;
        const movedDocument = snapshot.fixture.docScrollY !== scrollProbe.before.fixture.docScrollY;
        return movedInner || movedDocument;
      });
      scrollProbe.after = state;
      return state.fixture.scrollAreaY !== scrollProbe.before.fixture.scrollAreaY;
    });

    // The agent acts on the shared page; the viewer must still see the pixels change.
    let visibleAfterMs = null;
    if (withPixelCheck) {
      await attempt(checks, "agentActionVisibleInPixels", async () => {
        const baseline = await viewer.currentMean();
        const started = Date.now();
        await control.action("click", { selector: "#probe-btn" });
        const frame = await viewer.waitForMeanChange({ baseline, threshold: 25, timeoutMs: 12000, sinceTs: started });
        visibleAfterMs = frame.arrivalTs - started;
        return true;
      });
    }

    const after = await control.state();
    return {
      index,
      at: new Date().toISOString(),
      checks,
      visibleAfterMs,
      scroll: {
        areaBefore: scrollProbe.before?.fixture.scrollAreaY ?? null,
        areaAfter: scrollProbe.after?.fixture.scrollAreaY ?? null,
      },
      fieldValue: after.fixture.fieldValue,
    };
  }

  while (Date.now() < deadline) {
    checkpointIndex += 1;
    const isLastBurst = Date.now() + checkpointSeconds * 1000 >= deadline;
    const burst = await activityBurst(checkpointIndex, checkpointIndex === 1 || isLastBurst);

    // One planned viewer reconnect around the half-way point; the page must survive it.
    if (checkpointIndex === Math.max(2, Math.floor(plannedCheckpoints / 2))) {
      await viewer.close();
      await sleep(3000);
      await viewer.connect({ timeoutMs: 15000 });
      await viewer.waitForFrame((frame) => frame.snapshot, 15000).catch(() => {});
      viewerReconnects += 1;
    }

    const remainingMs = deadline - Date.now();
    const idleMs = Math.min(Math.max(0, checkpointSeconds * 1000 - 8000), Math.max(0, remainingMs));
    if (idleMs > 0) await sleep(idleMs);

    const state = await control.state();
    const stack = await sampleStack(process.pid);
    const browser = findBrowserProcess(stack);
    const viewerStats = viewer.stats();
    const wallMs = stack.ts - previousStack.ts;
    const cpuPercent =
      wallMs > 0
        ? Number((((stack.jiffies - previousStack.jiffies) / clockTicks / (wallMs / 1000)) * 100).toFixed(2))
        : null;
    previousStack = stack;

    const checkpoint = {
      index: checkpointIndex,
      at: new Date().toISOString(),
      elapsedMs: Date.now() - startedAt,
      url: state.url,
      title: state.title,
      targetId: state.targetId,
      pageClosed: state.closed,
      contextPageCount: state.contextPageCount,
      nonce: state.fixture?.nonce ?? null,
      heartbeat: state.fixture?.heartbeat ?? null,
      browserPid: browser?.pid ?? null,
      browserRssMb: Number((stack.rssKb / 1024).toFixed(1)),
      stackCpuPercentOfOneCore: cpuPercent,
      processCount: stack.processes.length,
      viewerFrames: viewerStats.frames,
      viewerBytes: viewerStats.bytes,
      viewerMaxGapMs: viewerStats.maxGapMs,
      viewerCurrentGapMs: viewerStats.lastArrivalTs === null ? null : Date.now() - viewerStats.lastArrivalTs,
      burst: burst.checks,
      visibleAfterMs: burst.visibleAfterMs,
      scroll: burst.scroll,
    };
    checkpoints.push(checkpoint);

    const problems = [
      Object.entries(burst.checks).some(([, ok]) => ok !== true)
        ? `checkpoint ${checkpointIndex} interaction checks failed: ${JSON.stringify(burst.checks)}`
        : null,
      !fixtureOrigins.some((origin) => String(state.url).startsWith(origin))
        ? `checkpoint ${checkpointIndex} left the fixture: ${state.url}`
        : null,
      state.closed !== false ? `checkpoint ${checkpointIndex} reports a closed page` : null,
      state.targetId !== startState.targetId
        ? `checkpoint ${checkpointIndex} changed page target (${startState.targetId} -> ${state.targetId})`
        : null,
      state.fixture?.nonce !== startState.fixture?.nonce
        ? `checkpoint ${checkpointIndex} document was replaced (nonce changed)`
        : null,
      !(state.fixture?.heartbeat > startState.fixture?.heartbeat)
        ? `checkpoint ${checkpointIndex} heartbeat did not advance`
        : null,
      browserPid !== null && browser?.pid !== browserPid
        ? `checkpoint ${checkpointIndex} browser process changed (${browserPid} -> ${browser?.pid})`
        : null,
    ].filter(Boolean);
    if (problems.length > 0) failures.push(...problems);
    console.log(
      `  [${candidate}] checkpoint ${checkpointIndex}/${plannedCheckpoints} t+${Math.round(checkpoint.elapsedMs / 1000)}s ` +
        `heartbeat=${checkpoint.heartbeat} rss=${checkpoint.browserRssMb}MB cpu=${cpuPercent}% frames=${checkpoint.viewerFrames}` +
        (problems.length > 0 ? ` PROBLEMS: ${problems.join("; ")}` : ""),
    );
  }

  const endState = await control.state();
  await viewer.close().catch(() => {});
  const cleanup = await stopServer(server.child);

  const report = {
    schema: 1,
    label,
    candidate,
    generatedAt: new Date().toISOString(),
    requestedMinutes: minutes,
    checkpointSeconds,
    actualMs: Date.now() - startedAt,
    viewport: VIEWPORT,
    versions: server.ready.versions,
    candidateInfo: server.ready.info,
    start: {
      url: startState.url,
      targetId: startState.targetId,
      nonce: startState.fixture?.nonce ?? null,
      heartbeat: startState.fixture?.heartbeat ?? null,
      browserPid,
    },
    end: {
      url: endState.url,
      targetId: endState.targetId,
      nonce: endState.fixture?.nonce ?? null,
      heartbeat: endState.fixture?.heartbeat ?? null,
      pageClosed: endState.closed,
      contextPageCount: endState.contextPageCount,
    },
    viewerReconnects,
    checkpoints,
    memoryDriftMb:
      checkpoints.length > 1
        ? Number((checkpoints[checkpoints.length - 1].browserRssMb - checkpoints[0].browserRssMb).toFixed(1))
        : null,
    failures,
    pass:
      failures.length === 0 &&
      checkpoints.length >= plannedCheckpoints - 1 &&
      Date.now() - startedAt >= minutes * 60 * 1000 - 1000 &&
      endState.targetId === startState.targetId &&
      endState.fixture?.nonce === startState.fixture?.nonce &&
      endState.closed === false,
    cleanup,
  };
  const outPath = join(MEASUREMENTS_DIR, `${label}-${candidate}-endurance.json`);
  await writeFile(outPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(
    `  [${candidate}] ${report.pass ? "ENDURANCE PASS" : "ENDURANCE FAIL"} — ` +
      `${Math.round(report.actualMs / 1000)}s, ${checkpoints.length} checkpoints, ${failures.length} problems, ` +
      `memory drift ${report.memoryDriftMb} MB (${outPath})`,
  );
  return report;
}

const reports = [];
for (const candidate of candidates) {
  console.log(`== endurance ${candidate} (${minutes} min) ==`);
  reports.push(await runEndurance(candidate));
}
process.exit(reports.every((report) => report.pass) ? 0 : 1);
