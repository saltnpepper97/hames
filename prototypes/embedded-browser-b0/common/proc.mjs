// Small /proc-based resource sampler. Used to compare whole-stack CPU and memory
// for the two candidates (browser + transport processes rooted at the candidate
// server process).

import { readdir, readFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

/** Clock ticks per second; Linux user HZ is 100 on every mainstream config. */
let clockTicks = 100;
export async function detectClockTicks() {
  try {
    const { stdout } = await execFileAsync("getconf", ["CLK_TCK"]);
    const value = Number.parseInt(stdout.trim(), 10);
    if (Number.isFinite(value) && value > 0) clockTicks = value;
  } catch {
    // keep the default
  }
  return clockTicks;
}

/**
 * @typedef {{ pid: number, ppid: number, utime: number, stime: number, rssPages: number, cmd: string }} ProcEntry
 */

/** @returns {Promise<Map<number, ProcEntry>>} */
export async function readProcessTable() {
  /** @type {Map<number, ProcEntry>} */
  const table = new Map();
  let names;
  try {
    names = await readdir("/proc");
  } catch {
    return table;
  }
  for (const name of names) {
    if (!/^\d+$/.test(name)) continue;
    const pid = Number.parseInt(name, 10);
    let stat;
    try {
      stat = await readFile(`/proc/${pid}/stat`, "utf8");
    } catch {
      continue;
    }
    const close = stat.lastIndexOf(")");
    const rest = stat.slice(close + 2).split(" ");
    const ppid = Number.parseInt(rest[1], 10);
    const utime = Number.parseInt(rest[11], 10);
    const stime = Number.parseInt(rest[12], 10);
    const rssPages = Number.parseInt(rest[21], 10);
    let cmd = "";
    try {
      cmd = (await readFile(`/proc/${pid}/cmdline`, "utf8")).replaceAll("\0", " ").trim();
    } catch {
      cmd = "";
    }
    table.set(pid, { pid, ppid, utime, stime, rssPages, cmd });
  }
  return table;
}

/**
 * All descendants of rootPid (inclusive).
 *
 * @param {Map<number, ProcEntry>} table
 * @param {number} rootPid
 */
export function descendants(table, rootPid) {
  /** @type {number[]} */
  const pids = [];
  const byParent = new Map();
  for (const entry of table.values()) {
    const list = byParent.get(entry.ppid) ?? [];
    list.push(entry.pid);
    byParent.set(entry.ppid, list);
  }
  const queue = [rootPid];
  while (queue.length > 0) {
    const pid = /** @type {number} */ (queue.shift());
    if (pids.includes(pid)) continue;
    pids.push(pid);
    for (const child of byParent.get(pid) ?? []) queue.push(child);
  }
  return pids;
}

/** @param {number} rootPid */
export async function sampleStack(rootPid) {
  const table = await readProcessTable();
  const pids = descendants(table, rootPid);
  let jiffies = 0;
  let rssKb = 0;
  const processes = [];
  for (const pid of pids) {
    const entry = table.get(pid);
    if (!entry) continue;
    jiffies += entry.utime + entry.stime;
    rssKb += (entry.rssPages * 4096) / 1024;
    processes.push({ pid, cmd: entry.cmd.slice(0, 160) });
  }
  return { ts: Date.now(), rootPid, pids, jiffies, rssKb, processes };
}

export async function sampleSystemCpu() {
  const stat = await readFile("/proc/stat", "utf8");
  const line = stat.split("\n").find((row) => row.startsWith("cpu "));
  if (!line) throw new Error("could not read /proc/stat cpu line");
  const values = line.trim().split(/\s+/).slice(1).map(Number);
  const total = values.reduce((sum, value) => sum + value, 0);
  const idle = values[3] + (values[4] ?? 0);
  return { ts: Date.now(), total, idle };
}

/**
 * Derive percentages between two samples of the same stack.
 *
 * @param {{ before: Awaited<ReturnType<typeof sampleStack>>, after: Awaited<ReturnType<typeof sampleStack>>, systemBefore: Awaited<ReturnType<typeof sampleSystemCpu>>, systemAfter: Awaited<ReturnType<typeof sampleSystemCpu>> }} input
 */
export function summarizeUsage(input) {
  const { before, after, systemBefore, systemAfter } = input;
  const wallMs = after.ts - before.ts;
  const wallSeconds = Math.max(wallMs, 1) / 1000;
  const deltaJiffies = after.jiffies - before.jiffies;
  const systemTotal = systemAfter.total - systemBefore.total;
  const systemIdle = systemAfter.idle - systemBefore.idle;
  return {
    wallMs,
    stackCpuPercentOfOneCore: Number(((deltaJiffies / clockTicks / wallSeconds) * 100).toFixed(2)),
    stackRssMb: Number((after.rssKb / 1024).toFixed(1)),
    systemBusyPercent: systemTotal > 0 ? Number((((systemTotal - systemIdle) / systemTotal) * 100).toFixed(2)) : null,
    processCount: after.pids.length,
    processes: after.processes,
  };
}
