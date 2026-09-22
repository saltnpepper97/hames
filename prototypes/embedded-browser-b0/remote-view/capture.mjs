// X11 screen capture through ffmpeg's x11grab device (the "remote view" source).
//
// The capture pipeline runs only while a viewer is connected, and identical frames
// are dropped before they reach the viewer so that idle bandwidth is comparable
// with the change-driven CDP screencast candidate.

import { spawn } from "node:child_process";

const SOI_FIRST = 0xff;
const SOI_SECOND = 0xd8;
const EOI_SECOND = 0xd9;

/**
 * Find a two-byte JPEG marker. `Buffer.indexOf(number)` coerces the value to a single
 * byte, so marker scanning has to be done explicitly.
 *
 * @param {Buffer} buffer
 * @param {number} second
 * @param {number} from
 */
function findMarker(buffer, second, from = 0) {
  for (let index = from; index + 1 < buffer.length; index += 1) {
    if (buffer[index] === SOI_FIRST && buffer[index + 1] === second) return index;
  }
  return -1;
}

/**
 * @param {{ display: string, region: { x: number, y: number, width: number, height: number }, fps: number, qscale: number, env: NodeJS.ProcessEnv, onFrame: (jpeg: Buffer, meta: { ts: number, deduped: boolean }) => void }} options
 */
export function createScreenCapture(options) {
  const { display, region, fps, qscale, env, onFrame } = options;
  const stats = {
    capturedFrames: 0,
    forwardedFrames: 0,
    identicalFrames: 0,
    bytes: 0,
    startedAt: null,
    lastFrameAt: null,
    intervals: /** @type {number[]} */ ([]),
    stderr: "",
  };

  /** @type {import("node:child_process").ChildProcessWithoutNullStreams | null} */
  let child = null;
  let buffer = Buffer.alloc(0);
  /** @type {Buffer | null} */
  let previous = null;
  let lastForwardedAt = 0;

  function parse(chunk) {
    buffer = Buffer.concat([buffer, chunk]);
    while (true) {
      const start = findMarker(buffer, SOI_SECOND);
      if (start < 0) {
        // keep at most one byte in case the SOI marker is split across chunks
        if (buffer.length > 1) buffer = buffer.subarray(buffer.length - 1);
        return;
      }
      const end = findMarker(buffer, EOI_SECOND, start + 2);
      if (end < 0) {
        if (start > 0) buffer = buffer.subarray(start);
        return;
      }
      const jpeg = Buffer.from(buffer.subarray(start, end + 2));
      buffer = buffer.subarray(end + 2);
      stats.capturedFrames += 1;
      const ts = Date.now();
      stats.lastFrameAt = ts;
      if (previous !== null && previous.length === jpeg.length && previous.equals(jpeg)) {
        stats.identicalFrames += 1;
        continue;
      }
      previous = jpeg;
      stats.forwardedFrames += 1;
      stats.bytes += jpeg.byteLength;
      if (lastForwardedAt > 0) stats.intervals.push(ts - lastForwardedAt);
      lastForwardedAt = ts;
      onFrame(jpeg, { ts, deduped: false });
    }
  }

  return {
    stats,
    start() {
      if (child) return;
      const args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "x11grab",
        "-draw_mouse",
        "0",
        "-video_size",
        `${region.width}x${region.height}`,
        "-framerate",
        String(fps),
        "-i",
        `${display}.0+${region.x},${region.y}`,
        "-c:v",
        "mjpeg",
        "-q:v",
        String(qscale),
        "-f",
        "image2pipe",
        "pipe:1",
      ];
      child = spawn("ffmpeg", args, { env, stdio: ["ignore", "pipe", "pipe"] });
      stats.startedAt = Date.now();
      child.stdout.on("data", parse);
      child.stderr.on("data", (chunk) => {
        stats.stderr = `${stats.stderr}${chunk.toString("utf8")}`.slice(-4000);
      });
    },
    async stop() {
      if (!child) return;
      const proc = child;
      child = null;
      proc.kill("SIGTERM");
      await new Promise((resolve) => {
        proc.once("exit", resolve);
        setTimeout(resolve, 2000).unref();
      });
      previous = null;
      buffer = Buffer.alloc(0);
    },
    isRunning: () => child !== null,
  };
}

/**
 * One-shot raw RGB grab, used for calibration (no JPEG noise).
 *
 * @param {{ display: string, region: { x: number, y: number, width: number, height: number }, env: NodeJS.ProcessEnv }} options
 * @returns {Promise<Buffer>}
 */
export async function grabRawRgb(options) {
  const { display, region, env } = options;
  const args = [
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "x11grab",
    "-draw_mouse",
    "0",
    "-video_size",
    `${region.width}x${region.height}`,
    "-i",
    `${display}.0+${region.x},${region.y}`,
    "-frames:v",
    "1",
    "-f",
    "rawvideo",
    "-pix_fmt",
    "rgb24",
    "pipe:1",
  ];
  const child = spawn("ffmpeg", args, { env, stdio: ["ignore", "pipe", "pipe"] });
  /** @type {Buffer[]} */
  const chunks = [];
  let stderr = "";
  child.stdout.on("data", (chunk) => chunks.push(chunk));
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });
  await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`raw grab failed (${code}): ${stderr.slice(0, 300)}`));
    });
  });
  return Buffer.concat(chunks);
}

/**
 * Locate the fixture's magenta 12x12 origin marker inside a raw RGB frame.
 *
 * @param {Buffer} rgb
 * @param {{ width: number, height: number, searchWidth?: number, searchHeight?: number }} options
 */
export function findOriginMarker(rgb, options) {
  const { width, height } = options;
  const searchWidth = Math.min(options.searchWidth ?? 400, width);
  const searchHeight = Math.min(options.searchHeight ?? 260, height);
  /** @type {{ x: number, y: number } | null} */
  let best = null;
  for (let y = 0; y < searchHeight; y += 1) {
    for (let x = 0; x < searchWidth; x += 1) {
      const index = (y * width + x) * 3;
      if (rgb[index] > 240 && rgb[index + 1] < 20 && rgb[index + 2] > 240) {
        // marker is a 12x12 block; accept the first hit as its top-left corner
        if (best === null) best = { x, y };
        else if (y * searchWidth + x < best.y * searchWidth + best.x) best = { x, y };
      }
    }
  }
  return best;
}
