#!/usr/bin/env node
// Build the B0 transport comparison matrix from recorded measurements.
//
//   node tools/compare.mjs                                  # both default measurement files
//   node tools/compare.mjs --measurements a.json,b.json
//
// Every number in the matrix is read from measurements/*-measurement.json, which is
// produced by tools/measure.mjs running the same interaction script and the same
// resources sampler for both candidates. Nothing is estimated here; the only written
// judgement is the explicit transport decision block at the end, which is labelled as a
// decision rather than a measurement.

import { readFile, stat, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { join } from "node:path";
import { MEASUREMENTS_DIR, PROTOTYPE_DIR, VENDOR_DIR } from "../common/paths.mjs";
import { CAPTURE_FPS, ENCODING, VIEWPORT, XVFB_SCREEN } from "../common/config.mjs";

const execFileAsync = promisify(execFile);

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? (argv[index + 1] ?? "true") : fallback;
};

const explicit = flag("measurements", null);
const files = explicit
  ? String(explicit).split(",").filter(Boolean).map((name) => (name.includes("/") ? name : join(MEASUREMENTS_DIR, name)))
  : ["frame-stream", "remote-view"].map((name) => join(MEASUREMENTS_DIR, `${name}-measurement.json`));

/** @type {Array<Record<string, any>>} */
const measurements = [];
for (const file of files) {
  try {
    measurements.push(JSON.parse(await readFile(file, "utf8")));
  } catch (error) {
    console.error(`cannot read measurement ${file}: ${String(error)}`);
    process.exit(1);
  }
}

const byName = new Map(measurements.map((entry) => [entry.candidate, entry]));
const frameStream = byName.get("frame-stream");
const remoteView = byName.get("remote-view");
const labels = [...new Set(measurements.map((entry) => entry.label))];

async function directorySize(path) {
  try {
    const { stdout } = await execFileAsync("du", ["-sh", path], { timeout: 60000 });
    return stdout.trim().split(/\s+/)[0];
  } catch {
    return null;
  }
}

async function fileSize(path) {
  try {
    return (await stat(path)).size;
  } catch {
    return null;
  }
}

const dependencyCost = {
  "frame-stream": {
    prototypeDependencies: frameStream?.versions?.playwright ? [`playwright@${frameStream.versions.playwright}`, "ws"] : [],
    browser: frameStream?.candidateInfo
      ? `${frameStream.candidateInfo.executablePath ?? "playwright-managed chromium"} (Playwright-managed download, reused from the shared browser cache)`
      : null,
    additionalSystemPackages: ["none beyond Node and a Playwright-managed Chromium"],
    chromiumArgsOfNote: ["--headless", "--disable-background-networking", "--mute-audio", "--disable-features=Translate,MediaRouter,OptimizationHints"],
    installCommand: "node tools/fetch-browsers.mjs (wraps: npx playwright install chromium)",
  },
  "remote-view": {
    prototypeDependencies: remoteView?.versions?.playwright ? [`playwright@${remoteView.versions.playwright}`, "ws"] : [],
    browser: remoteView?.candidateInfo
      ? `${remoteView.candidateInfo.executablePath ?? "playwright-managed chromium"} (headed)`
      : null,
    additionalSystemPackages: [
      `Xvfb${remoteView?.versions?.xvfb?.package ? ` ${remoteView.versions.xvfb.package.version} (vendored from ${remoteView.versions.xvfb.package.name})` : ""}`,
      "xdotool (XTEST input injection)",
      "ffmpeg (x11grab capture and jpeg encode)",
    ],
    vendoredXvfb: remoteView?.versions?.xvfb ?? null,
    vendoredXvfbBytes: await fileSize(join(VENDOR_DIR, "usr", "bin", "Xvfb")),
    installCommand: "node tools/fetch-xvfb.mjs (downloads the distro package into prototype-local vendor/)",
    noPackageManagerRoot: true,
  },
  shared: {
    viewport: VIEWPORT,
    encoding: ENCODING,
    remoteViewCaptureFps: CAPTURE_FPS,
    remoteViewScreen: XVFB_SCREEN,
    nodeModulesSize: await directorySize(join(PROTOTYPE_DIR, "node_modules")),
  },
};

const numberOrDash = (value, suffix = "") => (typeof value === "number" ? `${value}${suffix}` : "—");
const list = (values) => (values && values.length > 0 ? values.join(", ") : "none");

/** @typedef {{ metric: string, frameStream: string, remoteView: string, notes: string }} Row */
/** @type {Row[]} */
const rows = [];
const row = (metric, fs, rv, notes = "") => rows.push({ metric, frameStream: String(fs), remoteView: String(rv), notes });

row("Input → visible latency, p50", numberOrDash(frameStream?.latency?.p50Ms, " ms"), numberOrDash(remoteView?.latency?.p50Ms, " ms"), "viewer action to the frame that shows its effect");
row("Input → visible latency, p95", numberOrDash(frameStream?.latency?.p95Ms, " ms"), numberOrDash(remoteView?.latency?.p95Ms, " ms"), "same probe, 95th percentile");
row("Viewer connect", numberOrDash(frameStream?.startup?.connectMs, " ms"), numberOrDash(remoteView?.startup?.connectMs, " ms"), "WebSocket open to hello");
row("First frame after connect", numberOrDash(frameStream?.startup?.firstFrameMs, " ms"), numberOrDash(remoteView?.startup?.firstFrameMs, " ms"), "hello to first binary frame");
row("Streamed frame size", frameStream ? `${frameStream.candidateInfo.frameSize?.width}×${frameStream.candidateInfo.frameSize?.height}` : "—", remoteView ? `${remoteView.candidateInfo.frameSize?.width}×${remoteView.candidateInfo.frameSize?.height}` : "—", "remote-view includes browser chrome in the same frame");
row("Page pixels per frame", `${VIEWPORT.width}×${VIEWPORT.height}`, `${VIEWPORT.width}×${VIEWPORT.height}`, "both stream the same page viewport");
row("Encoding", ENCODING["frame-stream"].setting, ENCODING["remote-view"].setting, "JPEG quality settings differ; both are JPEG on the same wire protocol");
row("Active-phase CPU (whole stack, one core = 100%)", numberOrDash(frameStream?.phases?.active?.usage?.stackCpuPercentOfOneCore, "%"), numberOrDash(remoteView?.phases?.active?.usage?.stackCpuPercentOfOneCore, "%"), "browser + transport + capture processes");
row("Idle-phase CPU", numberOrDash(frameStream?.phases?.idle?.usage?.stackCpuPercentOfOneCore, "%"), numberOrDash(remoteView?.phases?.idle?.usage?.stackCpuPercentOfOneCore, "%"), "page visible, nobody acting");
row("Active bandwidth", numberOrDash(frameStream?.phases?.active?.stream?.bandwidthKbitsPerSecond, " kbit/s"), numberOrDash(remoteView?.phases?.active?.stream?.bandwidthKbitsPerSecond, " kbit/s"), "bytes actually sent to the viewer");
row("Idle bandwidth", numberOrDash(frameStream?.phases?.idle?.stream?.bandwidthKbitsPerSecond, " kbit/s"), numberOrDash(remoteView?.phases?.idle?.stream?.bandwidthKbitsPerSecond, " kbit/s"), "the fixture clock ticks once per second, so idle is not a frozen page");
row("Average bytes per frame", numberOrDash(frameStream?.phases?.active?.stream?.averageBytesPerFrame), numberOrDash(remoteView?.phases?.active?.stream?.averageBytesPerFrame), "");
row("Frames/s reaching the viewer (active)", numberOrDash(frameStream?.phases?.active?.stream?.framesPerSecond), numberOrDash(remoteView?.phases?.active?.stream?.framesPerSecond), "frame-stream is change-driven");
row("Frames/s captured before dedup", "not applicable (change-driven)", numberOrDash(remoteView?.phases?.active?.stream?.capturedFramesPerSecond), "x11grab samples at a fixed rate whether or not anything changed");
row("Whole-stack memory (RSS)", numberOrDash(frameStream?.phases?.active?.usage?.stackRssMb, " MB"), numberOrDash(remoteView?.phases?.active?.usage?.stackRssMb, " MB"), "all candidate descendants");
row("Image fidelity vs. Playwright render (content PSNR)", numberOrDash(frameStream?.clarity?.contentPsnrDb, " dB"), numberOrDash(remoteView?.clarity?.contentPsnrDb, " dB"), "higher is closer to the agent's own render");
row("Identity text (nonce) ink IoU", numberOrDash(frameStream?.clarity?.regionPsnrDb?.nonce?.inkIoU), numberOrDash(remoteView?.clarity?.regionPsnrDb?.nonce?.inkIoU), "glyph overlap after calibration");
row("Identity text (URL) ink IoU", numberOrDash(frameStream?.clarity?.regionPsnrDb?.url?.inkIoU), numberOrDash(remoteView?.clarity?.regionPsnrDb?.url?.inkIoU), "");
row("Form value ink IoU", numberOrDash(frameStream?.clarity?.regionPsnrDb?.fieldValue?.inkIoU), numberOrDash(remoteView?.clarity?.regionPsnrDb?.fieldValue?.inkIoU), "viewer-typed value as the agent renders it");
row("Interaction steps passing", `${frameStream?.stepSummary.filter((step) => step.ok).length}/${frameStream?.stepSummary.length}`, `${remoteView?.stepSummary.filter((step) => step.ok).length}/${remoteView?.stepSummary.length}`, "same script, both candidates");
row("Reconnect behaviour", "viewer reconnects and receives a fresh snapshot frame", "viewer reconnects, window is re-raised, capture restarts", "page, target, nonce, and URL preserved in both");
row("Page identity across the run", frameStream?.pageStateAtEnd?.targetId ? `stable target ${frameStream.pageStateAtEnd.targetId.slice(0, 8)}…, ${frameStream.pageStateAtEnd.contextPageCount} page(s)` : "—", remoteView?.pageStateAtEnd?.targetId ? `stable target ${remoteView.pageStateAtEnd.targetId.slice(0, 8)}…, ${remoteView.pageStateAtEnd.contextPageCount} page(s)` : "—", "");
row("Extra host requirements", "Playwright-managed Chromium only", "Xvfb + xdotool + ffmpeg; a headed browser on a virtual display", "remote-view cannot run on a host without an X server");
row("Browser chrome, native dialogs, IME, downloads", "not represented (page pixels only)", "represented, because the capture is of a real window", "the central trade-off between the two paths");
row("Documented limitations", `${frameStream?.limitations?.length ?? 0} items`, `${remoteView?.limitations?.length ?? 0} items`, "listed in full in the JSON output and the milestone evidence");

const decision = {
  chosen: "frame-stream",
  whatWasCompared: [
    "CDP Page.startScreencast frames (change-driven) plus CDP input dispatch, in a headless Chromium context.",
    "ffmpeg x11grab capture of a headed Chromium window on an isolated Xvfb display, plus xdotool XTEST input injection.",
  ],
  decidedBy: [
    `Input latency: ${numberOrDash(frameStream?.latency?.p50Ms, " ms")} vs ${numberOrDash(remoteView?.latency?.p50Ms, " ms")} at p50.`,
    `Idle CPU: ${numberOrDash(frameStream?.phases?.idle?.usage?.stackCpuPercentOfOneCore, "%")} vs ${numberOrDash(remoteView?.phases?.idle?.usage?.stackCpuPercentOfOneCore, "%")} of one core while the page is untouched.`,
    "Host requirements: frame-stream needs no X server, no xdotool, and no Xvfb; the browser lives headless on the gateway host.",
    "Frames only move when the page changes, so an idle browser costs almost nothing to watch.",
  ],
  acceptedLimitations: frameStream?.limitations ?? [],
  rejectedCandidateKeptAs: "an isolated, non-shipping prototype under prototypes/embedded-browser-b0/remote-view/, retained only as evidence and as a reference path for hosts that need native chrome",
  followUps: [
    "B2 must define text/IME, clipboard, and keyboard-shortcut handling that this prototype does not implement.",
    "B5 must decide popup, upload, download, certificate, and modal-dialog behaviour explicitly, because a page-image stream does not show browser chrome.",
    "If a future milestone needs native dialogs or IME, the remote-view prototype is the reference implementation, not a fallback to attach silently.",
  ],
};

const matrix = {
  schema: 1,
  generatedAt: new Date().toISOString(),
  labels,
  labelsMatch: labels.length === 1,
  measurements: measurements.map((entry) => ({
    candidate: entry.candidate,
    label: entry.label,
    finishedAt: entry.finishedAt,
    totalMs: entry.totalMs,
    versions: entry.versions,
    candidateInfo: entry.candidateInfo,
    startup: entry.startup,
    phases: entry.phases,
    latency: entry.latency,
    clarity: entry.clarity,
    stepSummary: entry.stepSummary,
    failures: entry.failures,
    limitations: entry.limitations,
    pageStateAtEnd: entry.pageStateAtEnd,
  })),
  dependencyCost,
  rows,
  decision,
};
await writeFile(join(MEASUREMENTS_DIR, "b0-comparison.json"), `${JSON.stringify(matrix, null, 2)}\n`);

const table = [
  "| Metric | frame-stream | remote-view | Notes |",
  "| --- | --- | --- | --- |",
  ...rows.map((entry) => `| ${entry.metric} | ${entry.frameStream} | ${entry.remoteView} | ${entry.notes} |`),
];

const markdown = `# Embedded browser B0 — transport comparison

Generated ${matrix.generatedAt} by \`node tools/compare.mjs\` from measurement labels: ${list(labels)}${matrix.labelsMatch ? "" : " (labels differ: the two files do not come from the same run)"}.

Both candidates were driven by the same interaction script, the same fixture, the same
viewer wire protocol, the same viewport, and the same resource sampler. Only the
capture and input mechanism differs.

${table.join("\n")}

## Dependency cost

| | frame-stream | remote-view |
| --- | --- | --- |
| Prototype npm dependencies | ${list(dependencyCost["frame-stream"].prototypeDependencies)} | ${list(dependencyCost["remote-view"].prototypeDependencies)} |
| Browser | ${dependencyCost["frame-stream"].browser} | ${dependencyCost["remote-view"].browser} |
| Extra host packages | ${list(dependencyCost["frame-stream"].additionalSystemPackages)} | ${list(dependencyCost["remote-view"].additionalSystemPackages)} |
| Vendored Xvfb binary | not used | ${dependencyCost["remote-view"].vendoredXvfbBytes ? `${(dependencyCost["remote-view"].vendoredXvfbBytes / 1024 / 1024).toFixed(1)} MB` : "—"} |
| Install command | \`${dependencyCost["frame-stream"].installCommand}\` | \`${dependencyCost["remote-view"].installCommand}\` |

Node modules in the prototype: ${dependencyCost.shared.nodeModulesSize ?? "—"} (shared by both candidates).

## Supported interactions (measured)

${list((frameStream?.stepSummary ?? []).filter((step) => step.ok).map((step) => step.id))}

## Interactions known to be missing or unsupported (not measured, from design review)

${[...new Set([...(frameStream?.limitations ?? []), ...(remoteView?.limitations ?? [])])].map((item) => `- ${item}`).join("\n")}

## Decision

**Chosen transport: ${decision.chosen}.**

Why:

${decision.decidedBy.map((item) => `- ${item}`).join("\n")}

Accepted limitations of the chosen transport:

${decision.acceptedLimitations.map((item) => `- ${item}`).join("\n")}

The rejected candidate is kept as an isolated prototype only: ${decision.rejectedCandidateKeptAs}.

Follow-ups this decision creates:

${decision.followUps.map((item) => `- ${item}`).join("\n")}
`;
await writeFile(join(MEASUREMENTS_DIR, "b0-comparison.md"), markdown);

console.log(table.join("\n"));
console.log(`\nLabels: ${list(labels)}${matrix.labelsMatch ? "" : " (MISMATCH — rerun tools/measure.mjs for both candidates)"}`);
console.log(`Wrote ${join(MEASUREMENTS_DIR, "b0-comparison.md")} and b0-comparison.json`);
