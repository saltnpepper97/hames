// Isolated virtual display management for the remote-view candidate.
//
// Xvfb is not installed on this host and installing packages requires root, so the
// prototype fetches the Arch package once (tools/fetch-xvfb.mjs) and extracts it
// into prototype-local vendor/. Running the extracted binary is a plain userspace
// operation: no root, no system-wide changes, and the display is a unix socket with
// -nolisten tcp, i.e. never reachable outside the prototype.

import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { access, readdir } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";
import { VENDOR_DIR } from "../common/paths.mjs";

const execFileAsync = promisify(execFile);

/** @returns {Promise<string[]>} */
async function candidateBinaries() {
  const list = [];
  if (process.env.B0_XVFB_PATH) list.push(process.env.B0_XVFB_PATH);
  list.push(join(VENDOR_DIR, "usr", "bin", "Xvfb"));
  try {
    const { stdout } = await execFileAsync("which", ["Xvfb"]);
    if (stdout.trim()) list.push(stdout.trim());
  } catch {
    // not on PATH
  }
  return list;
}

/** @returns {Promise<string | null>} */
export async function resolveXvfbBinary() {
  for (const candidate of await candidateBinaries()) {
    try {
      await access(candidate, constants.X_OK);
      return candidate;
    } catch {
      // try the next one
    }
  }
  return null;
}

async function isDisplayFree(number) {
  const lock = `/tmp/.X${number}-lock`;
  const socket = `/tmp/.X11-unix/X${number}`;
  try {
    await access(lock, constants.F_OK);
    return false;
  } catch {
    // no lock file
  }
  try {
    await access(socket, constants.F_OK);
    return false;
  } catch {
    return true;
  }
}

/** @param {number} number */
function toDisplay(number) {
  return `:${number}`;
}

/**
 * @param {{ screen: { width: number, height: number }, env: NodeJS.ProcessEnv, firstDisplay?: number }} options
 */
export async function startXvfb(options) {
  const { screen, env } = options;
  const binary = await resolveXvfbBinary();
  if (!binary) {
    throw new Error(
      "Xvfb not found. Run `node tools/fetch-xvfb.mjs` (prototype-local, no root) or set B0_XVFB_PATH.",
    );
  }

  let displayNumber = null;
  for (let number = options.firstDisplay ?? 90; number < 130; number += 1) {
    if (await isDisplayFree(number)) {
      displayNumber = number;
      break;
    }
  }
  if (displayNumber === null) throw new Error("no free X display number in :90-:129");

  const display = toDisplay(displayNumber);
  // Loopback-only in the strongest available sense: a local unix socket, no TCP
  // listener at all. The socket is never exposed beyond the prototype.
  const args = [display, "-screen", "0", `${screen.width}x${screen.height}x24`, "-nolisten", "tcp", "-noreset"];

  const child = spawn(binary, args, {
    env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
  });
  let log = "";
  child.stdout.on("data", (chunk) => {
    log = `${log}${chunk.toString("utf8")}`.slice(-4000);
  });
  child.stderr.on("data", (chunk) => {
    log = `${log}${chunk.toString("utf8")}`.slice(-4000);
  });

  const deadline = Date.now() + 10000;
  let ready = false;
  while (Date.now() < deadline) {
    try {
      const { stdout } = await execFileAsync("xdotool", ["getdisplaygeometry"], {
        env: { ...env, DISPLAY: display },
        timeout: 3000,
      });
      if (/\d+ \d+/.test(stdout)) {
        ready = true;
        break;
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  if (!ready) {
    child.kill("SIGKILL");
    throw new Error(`Xvfb ${display} did not become ready; log: ${log.slice(0, 600)}`);
  }

  const { stdout: geometry } = await execFileAsync("xdotool", ["getdisplaygeometry"], {
    env: { ...env, DISPLAY: display },
  });
  const [width, height] = geometry.trim().split(/\s+/).map(Number);

  return {
    binary,
    display,
    displayNumber,
    geometry: { width, height },
    log: () => log,
    pid: child.pid,
    async stop() {
      if (child.exitCode !== null) return;
      child.kill("SIGTERM");
      const killDeadline = Date.now() + 4000;
      while (child.exitCode === null && Date.now() < killDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (child.exitCode === null) child.kill("SIGKILL");
    },
  };
}

/** Best-effort listing of leftover X sockets/locks created by this prototype. */
export async function listLeftoverDisplays() {
  try {
    const sockets = await readdir("/tmp/.X11-unix");
    return sockets.filter((name) => name.startsWith("X"));
  } catch {
    return [];
  }
}
