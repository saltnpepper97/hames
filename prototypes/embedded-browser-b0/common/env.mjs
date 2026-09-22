// Isolated environment for every process the prototype starts.
//
// Hard rules for B0:
//   * never attach to a personal Chromium profile
//   * never touch the live Wayland session (no WAYLAND_DISPLAY for children)
//   * use a throwaway HAMES_HOME and throwaway XDG directories
//   * no Hames process is started or restarted by this prototype
//
// TMPDIR/XDG_RUNTIME_DIR use a short /tmp path on purpose: Chromium aborts with
// "Socket path too long" when TMPDIR is deeply nested (its singleton socket lives
// under TMPDIR).

import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ensureDir } from "./paths.mjs";

/** Short-lived temp roots created for child processes; removed on cleanup. */
const shortTemps = new Set();

/**
 * @param {{ runDir: string, display?: string | null, extra?: Record<string, string> }} options
 * @returns {Promise<NodeJS.ProcessEnv>}
 */
export async function isolatedEnv(options) {
  const { runDir, display = null, extra = {} } = options;
  const hamesHome = await ensureDir(join(runDir, "hames-home"));
  const short = await mkdtemp(join(tmpdir(), "b0-"));
  shortTemps.add(short);

  /** @type {NodeJS.ProcessEnv} */
  const env = {
    ...process.env,
    HAMES_HOME: hamesHome,
    XDG_RUNTIME_DIR: short,
    XDG_CACHE_HOME: await ensureDir(join(runDir, "xdg-cache")),
    TMPDIR: short,
    B0_ISOLATED: "1",
    ...extra,
  };
  // Children must not reach the live compositor even by accident.
  delete env.WAYLAND_DISPLAY;
  if (display) {
    env.DISPLAY = display;
  } else {
    delete env.DISPLAY;
  }
  return env;
}

/** Remove the short-lived temp roots created by isolatedEnv(). */
export async function cleanupIsolatedTemps() {
  for (const dir of shortTemps) {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
  shortTemps.clear();
}

/** Paths of the short-lived temp roots currently handed to children (evidence). */
export function isolatedTempRoots() {
  return [...shortTemps];
}
