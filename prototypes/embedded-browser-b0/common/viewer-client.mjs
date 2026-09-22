// Headless viewer client for the harness.
//
// It speaks exactly the same WebSocket protocol as viewer/viewer.js, so the numbers
// it produces describe what a real viewer experiences: it never touches Playwright,
// the browser, or the candidate internals.
//
// Frames are not decoded as they arrive. Frames are counted on arrival (for bandwidth
// and gap statistics) and decoded on demand with a one-shot ffmpeg process, because a
// streaming ffmpeg decoder buffers several frames before emitting anything, which is
// useless for latency measurement. A one-shot decode of a 1280x800 JPEG takes ~46 ms
// on this host; that cost is not part of any reported latency because latencies are
// derived from frame *arrival* timestamps.

import WebSocket from "ws";
import { spawn } from "node:child_process";
import { meanColor, meanDelta } from "./jpeg.mjs";

const FRAME_HISTORY = 120;

/**
 * @param {{ wsUrl: string, name: string, ffmpeg?: string }} options
 */
export function createViewerClient(options) {
  const { wsUrl, name } = options;
  const ffmpeg = options.ffmpeg ?? "ffmpeg";

  /** @type {WebSocket | null} */
  let socket = null;
  /** @type {Record<string, unknown> | null} */
  let hello = null;
  /** @type {Record<string, unknown> | null} */
  let pendingHeader = null;

  /**
   * @typedef {{ seq: number, headerTs: number, arrivalTs: number, bytes: number, width: number, height: number, snapshot: boolean, jpeg?: Buffer, mean?: number[], regionMeans?: Record<string, number[]> }} FrameEntry
   */

  /** @type {FrameEntry[]} */
  const frames = [];
  /** @type {Array<{ t: string, ts: number, payload: Record<string, unknown> }>} */
  const messages = [];
  /** @type {Map<string, { x: number, y: number, width: number, height: number }>} */
  const regions = new Map();
  /** @type {Array<(frame: FrameEntry) => void>} */
  const frameWaiters = [];
  const state = { bytes: 0, connects: 0, stateMessages: 0, notes: [] };

  const stats = () => {
    const arrivals = frames.map((frame) => frame.arrivalTs);
    const gaps = [];
    for (let index = 1; index < arrivals.length; index += 1) {
      gaps.push(arrivals[index] - arrivals[index - 1]);
    }
    const sorted = [...gaps].sort((a, b) => a - b);
    return {
      frames: frames.length,
      bytes: state.bytes,
      stateMessages: state.stateMessages,
      notes: state.notes.length,
      meanGapMs: gaps.length > 0 ? Number((gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length).toFixed(1)) : null,
      maxGapMs: sorted.length > 0 ? sorted[sorted.length - 1] : null,
      p95GapMs: sorted.length > 0 ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : null,
      snapshotFrames: frames.filter((frame) => frame.snapshot).length,
      firstArrivalTs: arrivals[0] ?? null,
      lastArrivalTs: arrivals[arrivals.length - 1] ?? null,
    };
  };

  /** @param {string} regionName @param {{ x: number, y: number, width: number, height: number }} rect */
  function registerRegion(regionName, rect) {
    regions.set(regionName, rect);
  }

  /**
   * Decode one JPEG payload into raw RGB with a one-shot ffmpeg process, optionally
   * cropping to a region first.
   *
   * @param {Buffer} jpeg
   * @param {{ x: number, y: number, width: number, height: number } | null} [crop]
   * @returns {Promise<{ rgb: Buffer, width: number, height: number, ms: number }>}
   */
  async function decodeJpeg(jpeg, crop = null) {
    const started = Date.now();
    const args = ["-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "pipe:0", "-frames:v", "1"];
    if (crop) args.push("-vf", `crop=${crop.width}:${crop.height}:${crop.x}:${crop.y}`);
    args.push("-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1");
    const child = spawn(ffmpeg, args, { stdio: ["pipe", "pipe", "pipe"] });
    /** @type {Buffer[]} */
    const chunks = [];
    let stderr = "";
    child.stdout.on("data", (chunk) => chunks.push(chunk));
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    const exit = new Promise((resolve) => child.on("exit", resolve));
    child.stdin.end(jpeg);
    await exit;
    const rgb = Buffer.concat(chunks);
    if (rgb.length === 0) throw new Error(`jpeg decode produced no pixels: ${stderr.slice(-200)}`);
    return { rgb, width: crop?.width ?? 0, height: crop?.height ?? 0, ms: Date.now() - started };
  }

  /** Decode the whole frame and cache the mean colour on the entry. */
  async function ensureFrameMean(frame) {
    if (frame.mean) return frame.mean;
    const pixels = await ensureFramePixels(frame);
    frame.mean = meanColor(pixels, frame.width, frame.height);
    return frame.mean;
  }

  /** Decode the whole frame and cache the raw RGB pixels on the entry. */
  async function ensureFramePixels(frame) {
    if (frame.pixels) return frame.pixels;
    if (!frame.jpeg) throw new Error(`frame ${frame.seq} no longer has its payload`);
    const { rgb } = await decodeJpeg(frame.jpeg);
    frame.pixels = rgb;
    return rgb;
  }

  /** Decode a named region of a frame and cache its mean colour. */
  async function ensureRegionMean(frame, regionName) {
    const rect = regions.get(regionName);
    if (!rect) throw new Error(`unknown region ${regionName}`);
    frame.regionMeans = frame.regionMeans ?? {};
    if (frame.regionMeans[regionName]) return frame.regionMeans[regionName];
    if (!frame.jpeg) throw new Error(`frame ${frame.seq} no longer has its payload`);
    const { rgb } = await decodeJpeg(frame.jpeg, rect);
    const mean = meanColor(rgb, rect.width, rect.height);
    frame.regionMeans[regionName] = mean;
    return mean;
  }

  /** @param {(frame: FrameEntry) => boolean} predicate @param {number} timeoutMs */
  function waitForFrame(predicate, timeoutMs) {
    for (const frame of frames) {
      if (predicate(frame)) return Promise.resolve(frame);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const index = frameWaiters.indexOf(handler);
        if (index >= 0) frameWaiters.splice(index, 1);
        reject(new Error(`timeout after ${timeoutMs}ms waiting for a frame condition`));
      }, timeoutMs);
      /** @param {FrameEntry} frame */
      const handler = (frame) => {
        if (!predicate(frame)) return;
        clearTimeout(timer);
        const index = frameWaiters.indexOf(handler);
        if (index >= 0) frameWaiters.splice(index, 1);
        resolve(frame);
      };
      frameWaiters.push(handler);
    });
  }

  function handleMessage(raw) {
    /** @type {Record<string, unknown>} */
    const message = JSON.parse(raw);
    if (message.t === "hello") {
      hello = message;
      return;
    }
    if (message.t === "frame") {
      pendingHeader = { ...message, arrivalTs: Date.now() };
      return;
    }
    if (message.t === "state") {
      state.stateMessages += 1;
      messages.push({ t: "state", ts: Number(message.ts ?? Date.now()), payload: message });
      return;
    }
    if (message.t === "note") {
      state.notes.push({ ts: Number(message.ts ?? Date.now()), text: String(message.text ?? "") });
      return;
    }
    messages.push({ t: String(message.t), ts: Number(message.ts ?? Date.now()), payload: message });
  }

  return {
    name,
    frames,
    messages,
    stats,
    registerRegion,
    hello: () => hello,
    lastState: () => [...messages].reverse().find((entry) => entry.t === "state")?.payload ?? null,
    notes: () => state.notes,

    async connect({ timeoutMs = 8000 } = {}) {
      socket = new WebSocket(wsUrl, { perMessageDeflate: false });
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`viewer connect timeout ${timeoutMs}ms`)), timeoutMs);
        socket?.once("open", () => {
          clearTimeout(timer);
          state.connects += 1;
          resolve(null);
        });
        socket?.once("error", (error) => {
          clearTimeout(timer);
          reject(error);
        });
      });
      socket.on("message", (data, isBinary) => {
        if (isBinary) {
          const jpeg = Buffer.from(/** @type {Buffer} */ (data));
          const header = pendingHeader;
          pendingHeader = null;
          state.bytes += jpeg.byteLength;
          const entry = {
            seq: Number(header?.seq ?? frames.length + 1),
            headerTs: Number(header?.ts ?? Date.now()),
            arrivalTs: Number(header?.arrivalTs ?? Date.now()),
            bytes: Number(header?.bytes ?? jpeg.byteLength),
            width: Number(header?.width ?? hello?.frameSize?.width ?? 0),
            height: Number(header?.height ?? hello?.frameSize?.height ?? 0),
            snapshot: header?.snapshot === true,
            jpeg,
          };
          frames.push(entry);
          if (frames.length > FRAME_HISTORY) {
            for (let index = 0; index < frames.length - FRAME_HISTORY; index += 1) delete frames[index].jpeg;
            if (frames.length > 3000) frames.splice(0, frames.length - 3000);
          }
          for (const waiter of [...frameWaiters]) waiter(entry);
          return;
        }
        handleMessage(data.toString("utf8"));
      });
      const deadline = Date.now() + timeoutMs;
      while (!hello && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      if (!hello) throw new Error("viewer did not receive a hello message");
      return hello;
    },

    /** @param {Record<string, unknown>} input */
    sendInput(input) {
      socket?.send(JSON.stringify({ t: "input", ts: Date.now(), ...input }));
    },

    async requestSnapshot() {
      const before = frames.length;
      socket?.send(JSON.stringify({ t: "snapshot", ts: Date.now() }));
      return await waitForFrame((frame) => frame.snapshot && frames.indexOf(frame) >= before, 8000);
    },

    async ping(timeoutMs = 5000) {
      const sent = Date.now();
      socket?.send(JSON.stringify({ t: "ping", ts: sent }));
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const pong = messages.find((entry) => entry.t === "pong" && entry.payload.echo === sent);
        if (pong) return { rttMs: Date.now() - sent };
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      throw new Error("pong timeout");
    },

    /** @param {number} timeoutMs */
    async waitForFirstFrame(timeoutMs = 8000) {
      if (frames.length > 0) return frames[0];
      return await waitForFrame(() => true, timeoutMs);
    },

    /** @param {(frame: FrameEntry) => boolean} predicate @param {number} timeoutMs */
    async waitForFrame(predicate, timeoutMs = 8000) {
      return await waitForFrame(predicate, timeoutMs);
    },

    lastFrame() {
      return frames[frames.length - 1] ?? null;
    },

    currentJpeg() {
      return frames[frames.length - 1]?.jpeg ?? null;
    },

    async meanOf(frame) {
      return await ensureFrameMean(frame);
    },

    /** Raw RGB pixels of a frame (decoded on demand and cached). */
    async pixelsOf(frame) {
      return await ensureFramePixels(frame);
    },

    async regionMeanOf(frame, regionName) {
      return await ensureRegionMean(frame, regionName);
    },

    /** Mean colour of the newest frame (decoded on demand). */
    async currentMean() {
      const frame = this.lastFrame();
      return frame ? await ensureFrameMean(frame) : null;
    },

    /** Mean colour of a named region in the newest frame. */
    async currentRegionMean(regionName) {
      const frame = this.lastFrame();
      return frame ? await ensureRegionMean(frame, regionName) : null;
    },

    /**
     * Wait until a frame that arrived after `sinceTs` differs from `baseline`.
     *
     * Frames are decoded in arrival order in small concurrent batches so that a fast
     * stream (30 fps) does not fall behind. The returned frame is the earliest frame
     * whose decoded content differs; because decoding is batched, at most
     * (batch - 1) frames in between can be skipped, which is recorded in the result.
     *
     * @param {{ baseline: number[], threshold: number, timeoutMs?: number, sinceTs?: number, batch?: number }} options
     */
    async waitForMeanChange(options) {
      const { baseline, threshold, timeoutMs = 8000, sinceTs = 0, batch = 4 } = options;
      const deadline = Date.now() + timeoutMs;
      let cursor = frames.findIndex((frame) => frame.arrivalTs >= sinceTs);
      if (cursor < 0) cursor = frames.length;
      while (Date.now() < deadline) {
        const window = frames.slice(cursor, cursor + batch).filter((frame) => frame.arrivalTs >= sinceTs);
        if (window.length === 0) {
          try {
            await waitForFrame((frame) => frame.arrivalTs >= sinceTs && frames.indexOf(frame) >= cursor, 250);
          } catch {
            continue;
          }
          continue;
        }
        /** @type {Array<{ frame: FrameEntry, mean: number[], delta: number }>} */
        const results = [];
        for (const frame of window) {
          try {
            const mean = await ensureFrameMean(frame);
            results.push({ frame, mean, delta: meanDelta(mean, baseline) });
          } catch (error) {
            results.push({ frame, mean: [], delta: -1, error });
          }
        }
        for (const entry of results) {
          if (entry.delta >= threshold) {
            entry.frame.transitionDelta = entry.delta;
            entry.frame.decodedAt = Date.now();
            entry.frame.skippedFrames = results
              .slice(0, results.indexOf(entry))
              .map((item) => item.frame.seq);
            return entry.frame;
          }
        }
        cursor += window.length;
      }
      throw new Error(`no visible change within ${timeoutMs}ms (threshold ${threshold})`);
    },

    /**
     * Wait until a named region of an arriving frame differs from a baseline mean.
     *
     * @param {{ regionName: string, baseline: number[], threshold: number, timeoutMs?: number, sinceTs?: number }} options
     */
    async waitForRegionChange(options) {
      const { regionName, baseline, threshold, timeoutMs = 8000, sinceTs = 0 } = options;
      const deadline = Date.now() + timeoutMs;
      let cursor = frames.findIndex((frame) => frame.arrivalTs >= sinceTs);
      if (cursor < 0) cursor = frames.length;
      while (Date.now() < deadline) {
        const frame = frames[cursor];
        if (!frame) {
          try {
            await waitForFrame((candidate) => frames.indexOf(candidate) >= cursor, 250);
          } catch {
            continue;
          }
          continue;
        }
        const mean = await ensureRegionMean(frame, regionName);
        if (meanDelta(mean, baseline) >= threshold) return frame;
        cursor += 1;
      }
      throw new Error(`no change in region ${regionName} within ${timeoutMs}ms`);
    },

    async close() {
      if (!socket) return;
      const done = new Promise((resolve) => socket?.once("close", resolve));
      socket.close();
      await Promise.race([done, new Promise((resolve) => setTimeout(resolve, 3000))]);
      socket = null;
      hello = null;
      pendingHeader = null;
    },
  };
}
