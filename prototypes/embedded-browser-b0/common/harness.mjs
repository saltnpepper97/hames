// Shared harness plumbing for the B0 tools (measure / verify / compare / endurance).
//
// Everything here talks to one candidate process started by tools/serve.mjs and to the
// prototype's own loopback control endpoint. It never touches Playwright directly, so
// the numbers and checks it produces describe what a viewer + the agent adapter can
// observe, not what a privileged test hook can read out of the browser.

import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { PROTOTYPE_DIR } from "./paths.mjs";

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Start tools/serve.mjs for one candidate and wait for its B0_READY line.
 *
 * @param {{ candidate: string, logPath: string, label?: string, timeoutMs?: number, extraEnv?: Record<string, string> }} options
 */
export async function startCandidateServer(options) {
  const { candidate, logPath, label = "manual", timeoutMs = 90000 } = options;
  const child = spawn(process.execPath, [join(PROTOTYPE_DIR, "tools", "serve.mjs"), "--candidate", candidate], {
    cwd: PROTOTYPE_DIR,
    env: { ...process.env, B0_MEASURE_LABEL: label, ...(options.extraEnv ?? {}) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  /** @type {string[]} */
  const log = [];
  /** @type {Record<string, any> | null} */
  let ready = null;
  /** @type {(chunk: Buffer) => void} */
  const onData = (chunk) => {
    const text = chunk.toString("utf8");
    log.push(text);
    for (const line of text.split("\n")) {
      if (line.startsWith("B0_READY ")) ready = JSON.parse(line.slice("B0_READY ".length));
    }
  };
  child.stdout.on("data", onData);
  child.stderr.on("data", onData);
  const deadline = Date.now() + timeoutMs;
  while (!ready && Date.now() < deadline) {
    if (child.exitCode !== null) break;
    await sleep(100);
  }
  await writeFile(logPath, log.join("")).catch(() => {});
  if (!ready) {
    child.kill("SIGKILL");
    throw new Error(
      `candidate ${candidate} did not become ready within ${timeoutMs}ms; log:\n${log.join("").slice(-4000)}`,
    );
  }
  return { child, ready, logPath, log: () => log.join("") };
}

/**
 * Thin client for the prototype's harness-only control endpoint.
 *
 * @param {string} baseUrl
 */
export function createControl(baseUrl) {
  const call = async (path, body) => {
    const response = await fetch(`${baseUrl}${path}`, body
      ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    if (!response.ok) throw new Error(`control ${path} failed: ${response.status} ${await response.text()}`);
    return await response.json();
  };
  return {
    info: () => call("/control/info"),
    state: () => call("/control/state"),
    stack: () => call("/control/stack"),
    frames: () => call("/control/frames"),
    fixtureStats: () => call("/control/fixture-stats"),
    versions: () => call("/control/versions"),
    action: (type, body = {}) => call("/control/action", { type, ...body }),
    evaluate: async (expression) => (await call("/control/action", { type: "evaluate", expression })).result,
    screenshot: (path) => call("/control/action", { type: "screenshot", path }),
  };
}

/** Ask the candidate server to stop and report how it exited. */
export async function stopServer(child) {
  if (child.exitCode !== null) return { alreadyExited: true, exitCode: child.exitCode };
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  child.kill("SIGTERM");
  const result = await Promise.race([exited, sleep(20000).then(() => ({ code: null, signal: "timeout" }))]);
  if (result.code === null) {
    child.kill("SIGKILL");
    await new Promise((resolve) => child.once("exit", resolve));
    return { code: null, signal: "SIGKILL", forced: true };
  }
  return result;
}

/**
 * Environment of a live process, read from /proc (same-user processes only).
 *
 * @param {number} pid
 * @returns {Promise<Record<string, string> | null>}
 */
export async function readProcessEnv(pid) {
  try {
    const raw = await readFile(`/proc/${pid}/environ`, "utf8");
    /** @type {Record<string, string>} */
    const env = {};
    for (const pair of raw.split("\0")) {
      if (!pair) continue;
      const index = pair.indexOf("=");
      if (index <= 0) continue;
      env[pair.slice(0, index)] = pair.slice(index + 1);
    }
    return env;
  } catch {
    return null;
  }
}

/**
 * Full command line of a live process, untruncated (same-user processes only). The
 * process table in the stack snapshot truncates for readability; checks that need to
 * read a late argument (for example --user-data-dir) use this instead.
 *
 * @param {number} pid
 * @returns {Promise<string | null>}
 */
export async function readProcessCmdline(pid) {
  try {
    return (await readFile(`/proc/${pid}/cmdline`, "utf8")).replaceAll("\0", " ").trim();
  } catch {
    return null;
  }
}

/**
 * Find the browser root process of a candidate's process tree (the one Chromium main
 * process, not a renderer/zygote/utility child).
 *
 * @param {{ processes: Array<{ pid: number, cmd: string }> }} stack
 */
export function findBrowserProcess(stack) {
  return (
    stack.processes.find((entry) => /(chrome|chromium)\s+--/.test(entry.cmd) && !/--type=/.test(entry.cmd)) ?? null
  );
}

/** Percentile helper shared by the tools. */
export function percentile(values, fraction) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

/** Recursively collect latency-looking numbers from step evidence. */
export function collectLatencies(value, out = []) {
  if (value === null || typeof value !== "object") return out;
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "number" && /(latencyMs|visibleAfterMs)$/.test(key)) out.push({ key, value: entry });
    else if (typeof entry === "object") collectLatencies(entry, out);
  }
  return out;
}

/** Compact per-step summary used by every tool and by the evidence report. */
export function stepSummary(steps) {
  return steps.map((step) => ({ id: step.id, ok: step.ok, ms: step.ms }));
}
