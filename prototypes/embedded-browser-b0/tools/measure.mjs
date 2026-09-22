#!/usr/bin/env node
// Measure one or both transport candidates with the shared interaction script.
//
//   node tools/measure.mjs                                  # both candidates, full script
//   node tools/measure.mjs --candidates frame-stream --steps connect,pointer-move-canvas
//   node tools/measure.mjs --label b0-smoke
//
// Writes measurements/<candidate>-measurement.json plus sample frames, and prints a
// compact summary. Nothing outside the prototype is started or stopped.

import { copyFile, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { MEASUREMENTS_DIR } from "../common/paths.mjs";
import { VIEWPORT } from "../common/config.mjs";
import { createViewerClient } from "../common/viewer-client.mjs";
import { createInteractionScript } from "../common/interaction-script.mjs";
import { sampleSystemCpu, summarizeUsage } from "../common/proc.mjs";
import {
  collectLatencies,
  createControl,
  percentile,
  sleep,
  startCandidateServer,
  stopServer,
} from "../common/harness.mjs";

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? (argv[index + 1] ?? "true") : fallback;
};

const candidates = String(flag("candidates", "frame-stream,remote-view")).split(",").filter(Boolean);
const stepFilter = flag("steps") ? String(flag("steps")).split(",") : null;
const label = String(flag("label", new Date().toISOString().replace(/[:.]/g, "-")));

async function measureCandidate(candidate) {
  const artifactsDir = join(MEASUREMENTS_DIR, "frames", `${label}-${candidate}`);
  await mkdir(artifactsDir, { recursive: true });
  const startedAt = Date.now();

  const server = await startCandidateServer({ candidate, label, logPath: join(artifactsDir, "server.log") });
  const control = createControl(server.ready.controlUrl);
  const viewer = createViewerClient({ wsUrl: server.ready.wsUrl, name: candidate });

  const connectStartedAt = Date.now();
  const hello = await viewer.connect({ timeoutMs: 15000 });
  const connectMs = Date.now() - connectStartedAt;
  const firstFrameStartedAt = Date.now();
  await viewer.waitForFirstFrame(15000);
  const firstFrameMs = Date.now() - firstFrameStartedAt;
  const ping = await viewer.ping();

  const script = createInteractionScript({
    viewer,
    control,
    viewport: VIEWPORT,
    artifactsDir,
    log: (message) => console.log(`[${candidate}] ${message}`),
    stopOnFailure: false,
  });
  await script.loadLayout();

  const allStepIds = script.steps.map((step) => step.id);
  const activeIds = stepFilter
    ? script.steps.filter((step) => stepFilter.includes(step.id)).map((step) => step.id)
    : script.steps.filter((step) => step.phase === "active").map((step) => step.id);
  const idleIds = stepFilter
    ? []
    : script.steps.filter((step) => step.phase === "idle").map((step) => step.id);

  /** Sample resources around a phase of work. */
  async function phase(name, work) {
    const stackBefore = await control.stack();
    const systemBefore = await sampleSystemCpu();
    const transportBefore = await control.frames();
    const viewerBefore = viewer.stats();
    const phaseStartedAt = Date.now();
    let result;
    let error = null;
    try {
      result = await work();
    } catch (caught) {
      error = String(caught);
      result = null;
    }
    const phaseMs = Date.now() - phaseStartedAt;
    const stackAfter = await control.stack();
    const systemAfter = await sampleSystemCpu();
    const transportAfter = await control.frames();
    const viewerAfter = viewer.stats();
    const usage = summarizeUsage({ before: stackBefore, after: stackAfter, systemBefore, systemAfter });
    const bytes = viewerAfter.bytes - viewerBefore.bytes;
    const seconds = Math.max(phaseMs, 1) / 1000;
    const captureBefore = transportBefore.capture ?? null;
    const captureAfter = transportAfter.capture ?? null;
    return {
      name,
      error,
      durationMs: phaseMs,
      result,
      usage,
      stream: {
        framesToViewer: transportAfter.transport.framesSent - transportBefore.transport.framesSent,
        bytesToViewer: bytes,
        averageBytesPerFrame:
          transportAfter.transport.framesSent - transportBefore.transport.framesSent > 0
            ? Number(
                (
                  (transportAfter.transport.frameBytes - transportBefore.transport.frameBytes) /
                  (transportAfter.transport.framesSent - transportBefore.transport.framesSent)
                ).toFixed(1),
              )
            : null,
        bandwidthKbitsPerSecond: phaseMs > 0 ? Number(((bytes * 8) / seconds / 1000).toFixed(1)) : null,
        viewerFrames: viewerAfter.frames - viewerBefore.frames,
        framesPerSecond: Number(((viewerAfter.frames - viewerBefore.frames) / seconds).toFixed(2)),
        // Remote-view specific: how many frames the capture device produced (that cost
        // is paid whether or not the content changed) versus how many were forwarded.
        capturedFramesPerSecond: captureAfter
          ? Number(((captureAfter.capturedFrames - (captureBefore?.capturedFrames ?? 0)) / seconds).toFixed(2))
          : null,
        identicalFramesDropped:
          captureAfter && captureBefore ? captureAfter.identicalFrames - captureBefore.identicalFrames : null,
        captureStderr: captureAfter?.stderr ?? null,
      },
      viewerStatsAfter: viewerAfter,
      contentPixelsPerSecond: null,
    };
  }

  const activePhase = await phase("active", () => script.run({ stepIds: activeIds, record: () => {} }));
  const idlePhase = idleIds.length > 0 ? await phase("idle", () => script.run({ stepIds: idleIds, record: () => {} })) : null;

  const steps = [...(activePhase.result ?? [])];
  for (const entry of idlePhase?.result ?? []) steps.push(entry);

  const transportFrames = await control.frames();
  const pageState = await control.state();
  const fixtureStats = await control.fixtureStats();

  const latencies = collectLatencies(steps);
  const latencyValues = latencies.map((entry) => entry.value);
  const clarityStep = steps.find((step) => step.id === "identity-agreement");

  const measurement = {
    schema: 1,
    label,
    candidate,
    scriptVersion: script.version,
    startedAt: new Date(startedAt).toISOString(),
    finishedAt: new Date().toISOString(),
    viewport: VIEWPORT,
    versions: server.ready.versions,
    candidateInfo: server.ready.info,
    startup: {
      connectMs,
      firstFrameMs,
      pingRttMs: ping.rttMs,
      helloFrameSize: hello.frameSize,
      helloPageOrigin: hello.pageOrigin,
      chromeHeight: hello.chromeHeight,
      viewerUrl: server.ready.viewerUrl,
    },
    phases: {
      active: stripResult(activePhase),
      idle: idlePhase ? stripResult(idlePhase) : null,
    },
    latency: {
      samples: latencies,
      count: latencyValues.length,
      p50Ms: percentile(latencyValues, 0.5),
      p95Ms: percentile(latencyValues, 0.95),
      minMs: latencyValues.length > 0 ? Math.min(...latencyValues) : null,
      maxMs: latencyValues.length > 0 ? Math.max(...latencyValues) : null,
    },
    clarity: clarityStep?.evidence?.pixelPath ?? null,
    steps,
    stepSummary: steps.map((step) => ({ id: step.id, ok: step.ok, ms: step.ms })),
    failures: steps.filter((step) => !step.ok).map((step) => ({ id: step.id, evidence: step.evidence })),
    framesAtEnd: transportFrames,
    pageStateAtEnd: pageState,
    fixtureStats,
    limitations: LIMITATIONS[candidate] ?? [],
  };

  await viewer.close().catch(() => {});
  const exit = await stopServer(server.child);
  measurement.cleanup = exit;
  measurement.totalMs = Date.now() - startedAt;

  const outPath = join(MEASUREMENTS_DIR, `${candidate}-measurement.json`);
  await writeFile(outPath, `${JSON.stringify(measurement, null, 2)}\n`);

  // Keep the two paths' images side by side as tracked evidence.
  const frameSource = clarityStep?.evidence?.pixelPath?.frame;
  const referenceSource = clarityStep?.evidence?.pixelPath?.screenshot;
  const copied = {};
  if (frameSource) {
    copied.frame = join(MEASUREMENTS_DIR, `${candidate}-content-frame.png`);
    await copyFile(frameSource, copied.frame).catch(() => {});
  }
  if (referenceSource) {
    copied.reference = join(MEASUREMENTS_DIR, `${candidate}-content-reference.png`);
    await copyFile(referenceSource, copied.reference).catch(() => {});
  }
  measurement.evidenceImages = copied;
  await writeFile(outPath, `${JSON.stringify(measurement, null, 2)}\n`);

  return measurement;
}

function stripResult(phaseResult) {
  const { result, ...rest } = phaseResult;
  return { ...rest, stepResults: (result ?? []).map((step) => ({ id: step.id, ok: step.ok, ms: step.ms })) };
}

const LIMITATIONS = {
  "frame-stream": [
    "Page images only: no browser chrome, native dialogs, download shelves, or permission prompts are visible.",
    "CDP screencast is change-driven; a fully static page produces no frames (no keepalive frame for a new viewer beyond the initial snapshot).",
    "IME/composition input is not implemented (Input.dispatchKeyEvent only).",
    "Clipboard, drag-and-drop from OS, and file pickers are not forwarded.",
    "Headless rendering can differ subtly from a headed browser (fonts, GPU rasterisation, scrollbars).",
  ],
  "remote-view": [
    "Requires an Xvfb binary; on this host it is fetched into prototype-local vendor/ because installing packages needs root.",
    "No window manager: focus follows the pointer, there is no window list, and native dialogs/child windows are not managed.",
    "Fixed-rate x11grab capture costs CPU even when idle; identical frames are dropped before transmission but still encoded.",
    "Screen capture carries browser chrome pixels as well as page pixels, so bandwidth is not directly comparable per page pixel.",
    "Pointer coordinates depend on the detected page origin inside the window; a moving/resized window invalidates it.",
    "Clipboard, drag-and-drop from OS, and file pickers are not forwarded.",
  ],
};

const results = [];
for (const candidate of candidates) {
  console.log(`== measuring ${candidate} ==`);
  const measurement = await measureCandidate(candidate);
  results.push(measurement);
  console.log(
    `[${candidate}] steps: ${measurement.stepSummary.filter((step) => step.ok).length}/${measurement.stepSummary.length} ok, ` +
      `latency p50 ${measurement.latency.p50Ms ?? "-"} ms / p95 ${measurement.latency.p95Ms ?? "-"} ms, ` +
      `content PSNR ${measurement.clarity?.contentPsnrDb ?? "-"} dB`,
  );
  const failed = measurement.failures.map((failure) => failure.id);
  if (failed.length > 0) console.log(`[${candidate}] failed steps: ${failed.join(", ")}`);
}

const summary = {
  label,
  generatedAt: new Date().toISOString(),
  candidates: results.map((measurement) => ({
    candidate: measurement.candidate,
    stepsOk: measurement.stepSummary.filter((step) => step.ok).length,
    stepsTotal: measurement.stepSummary.length,
    failed: measurement.failures.map((failure) => failure.id),
    latencyP50Ms: measurement.latency.p50Ms,
    latencyP95Ms: measurement.latency.p95Ms,
    contentPsnrDb: measurement.clarity?.contentPsnrDb ?? null,
    activeCpuPercentOfOneCore: measurement.phases.active?.usage?.stackCpuPercentOfOneCore ?? null,
    idleCpuPercentOfOneCore: measurement.phases.idle?.usage?.stackCpuPercentOfOneCore ?? null,
    activeBandwidthKbits: measurement.phases.active?.stream?.bandwidthKbitsPerSecond ?? null,
    idleBandwidthKbits: measurement.phases.idle?.stream?.bandwidthKbitsPerSecond ?? null,
    activeBytesPerFrame: measurement.phases.active?.stream?.averageBytesPerFrame ?? null,
    idleFramesPerSecond: measurement.phases.idle?.stream?.framesPerSecond ?? null,
    activeFramesPerSecond: measurement.phases.active?.stream?.framesPerSecond ?? null,
    capturedFramesPerSecond: measurement.phases.active?.stream?.capturedFramesPerSecond ?? null,
    connectMs: measurement.startup.connectMs,
    firstFrameMs: measurement.startup.firstFrameMs,
  })),
};
await writeFile(join(MEASUREMENTS_DIR, `${label}-summary.json`), `${JSON.stringify(summary, null, 2)}\n`);
console.log(JSON.stringify(summary, null, 2));
