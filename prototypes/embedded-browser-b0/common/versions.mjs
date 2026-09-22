// Exact tool versions for the evidence report. Everything is read from the
// running system, never hard-coded from memory.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { PROTOTYPE_DIR, VENDOR_DIR } from "./paths.mjs";
import { resolveXvfbBinary } from "../remote-view/xvfb.mjs";

const execFileAsync = promisify(execFile);

/**
 * @param {string} command
 * @param {string[]} args
 */
async function run(command, args) {
  const env = { ...process.env };
  delete env.WAYLAND_DISPLAY;
  delete env.DISPLAY;
  try {
    const { stdout } = await execFileAsync(command, args, { timeout: 20000, env });
    return stdout.trim().split("\n")[0]?.trim() ?? "";
  } catch (error) {
    return `unavailable (${String(error).slice(0, 80)})`;
  }
}

/**
 * Xvfb has no `-version` flag (it prints usage and exits), so the virtual-display
 * dependency is reported as: the binary that will actually be used, whichever copy is
 * on PATH (usually none), and the package metadata recorded when tools/fetch-xvfb.mjs
 * fetched it. Nothing here is guessed.
 */
async function xvfbInfo() {
  /** @type {{ binary: string | null, fromPath: string | null, package: Record<string, unknown> | null }} */
  const info = { binary: null, fromPath: null, package: null };
  try {
    info.binary = await resolveXvfbBinary();
  } catch {
    info.binary = null;
  }
  try {
    const { stdout } = await execFileAsync("which", ["Xvfb"]);
    info.fromPath = stdout.trim() || null;
  } catch {
    info.fromPath = null;
  }
  try {
    const lock = JSON.parse(await readFile(join(VENDOR_DIR, "xvfb.lock.json"), "utf8"));
    info.package = lock.package ?? null;
  } catch {
    info.package = null;
  }
  return info;
}

export async function collectVersions() {
  const pkg = JSON.parse(await readFile(join(PROTOTYPE_DIR, "package.json"), "utf8"));
  const versions = {
    node: process.version,
    platform: `${process.platform} ${process.arch}`,
    kernel: await run("uname", ["-r"]),
    playwright: pkg.dependencies?.playwright ?? "unknown",
    ws: pkg.dependencies?.ws ?? "unknown",
    chromiumPackage: await run("chromium", ["--version"]),
    ffmpeg: await run("ffmpeg", ["-version"]),
    xdotool: await run("xdotool", ["--version"]),
    xvfb: await xvfbInfo(),
  };
  return versions;
}

/** Playwright's own report of the chromium build it launches. */
export async function playwrightBrowserDescription(browser) {
  return {
    version: browser.version(),
    executablePath: browser.browserType().executablePath(),
  };
}
