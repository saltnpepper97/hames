// JPEG transport helpers. The measurement harness decodes streamed frames with
// ffmpeg so that both candidates are measured with exactly the same decoder.

import { spawn } from "node:child_process";

/**
 * Mean colour of an RGB24 buffer, sampled on a coarse grid (cheap, and enough for
 * the probe colour change which covers a large page area).
 *
 * @param {Buffer} rgb
 * @param {number} width
 * @param {number} height
 * @param {{ step?: number, region?: { x: number, y: number, width: number, height: number } }} [options]
 */
export function meanColor(rgb, width, height, options = {}) {
  const step = options.step ?? 4;
  const region = options.region ?? { x: 0, y: 0, width, height };
  let r = 0;
  let g = 0;
  let b = 0;
  let count = 0;
  for (let y = region.y; y < region.y + region.height; y += step) {
    for (let x = region.x; x < region.x + region.width; x += step) {
      const index = (y * width + x) * 3;
      r += rgb[index];
      g += rgb[index + 1];
      b += rgb[index + 2];
      count += 1;
    }
  }
  if (count === 0) return [0, 0, 0];
  return [r / count, g / count, b / count];
}

/** Largest absolute channel difference between two mean colours. */
export function meanDelta(a, b) {
  return Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
}

/**
 * Compare two RGB buffers of the same size and describe where they differ.
 *
 * @param {Buffer} a
 * @param {Buffer} b
 * @param {number} width
 * @param {number} height
 * @param {{ step?: number, tolerance?: number }} [options]
 */
export function changedRegion(a, b, width, height, options = {}) {
  const step = options.step ?? 2;
  const tolerance = options.tolerance ?? 8;
  let count = 0;
  let sampled = 0;
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < height; y += step) {
    for (let x = 0; x < width; x += step) {
      const index = (y * width + x) * 3;
      sampled += 1;
      const delta = Math.max(
        Math.abs(a[index] - b[index]),
        Math.abs(a[index + 1] - b[index + 1]),
        Math.abs(a[index + 2] - b[index + 2]),
      );
      if (delta <= tolerance) continue;
      count += 1;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  return {
    changedSamples: count,
    sampled,
    changedFraction: sampled > 0 ? Number((count / sampled).toFixed(5)) : 0,
    boundingBox: count > 0 ? { x: minX, y: minY, width: maxX - minX + step, height: maxY - minY + step } : null,
    step,
    tolerance,
  };
}

/**
 * @param {Buffer} jpeg
 * @param {string} outPath
 * @param {{ ffmpeg?: string }} [options]
 */
export async function jpegToPng(jpeg, outPath, options = {}) {
  const ffmpeg = options.ffmpeg ?? "ffmpeg";
  const child = spawn(
    ffmpeg,
    ["-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "pipe:0", "-frames:v", "1", "-y", outPath],
    { stdio: ["pipe", "ignore", "pipe"] },
  );
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });
  await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg png exit ${code}: ${stderr.slice(0, 400)}`))));
    child.stdin.end(jpeg);
  });
  return outPath;
}

/**
 * Decode one image (optionally cropped) to an 8-bit grayscale buffer.
 *
 * @param {{ path: string, crop?: { x: number, y: number, width: number, height: number }, ffmpeg?: string }} options
 */
async function decodeGray(options) {
  const ffmpeg = options.ffmpeg ?? "ffmpeg";
  const args = ["-hide_banner", "-loglevel", "error", "-i", options.path, "-frames:v", "1"];
  if (options.crop) {
    args.push("-vf", `crop=${options.crop.width}:${options.crop.height}:${options.crop.x}:${options.crop.y}`);
  }
  args.push("-f", "rawvideo", "-pix_fmt", "gray", "pipe:1");
  const child = spawn(ffmpeg, args, { stdio: ["ignore", "pipe", "pipe"] });
  /** @type {Buffer[]} */
  const chunks = [];
  let stderr = "";
  child.stdout.on("data", (chunk) => chunks.push(chunk));
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });
  await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`gray decode exit ${code}: ${stderr.slice(-200)}`))));
  });
  return Buffer.concat(chunks);
}

/** Ink mask for a grayscale buffer: pixels clearly darker than the local background. */
function inkMask(gray) {
  let sum = 0;
  for (const value of gray) sum += value;
  const mean = sum / gray.length;
  let variance = 0;
  for (const value of gray) variance += (value - mean) ** 2;
  const std = Math.sqrt(variance / gray.length);
  const threshold = mean - 0.4 * std;
  const mask = new Uint8Array(gray.length);
  for (let index = 0; index < gray.length; index += 1) mask[index] = gray[index] < threshold ? 1 : 0;
  return { mask, threshold };
}

/**
 * Compare the same on-screen region between the streamed frame and Playwright's
 * render. Whole-region PSNR is dominated by sub-pixel text differences, so the
 * primary metric here is the overlap (IoU) of "ink" pixels: identical text scores
 * high even after JPEG compression, different text does not.
 *
 * @param {{ referencePath: string, framePath: string, referenceCrop: { x: number, y: number, width: number, height: number }, testCrop: { x: number, y: number, width: number, height: number }, ffmpeg?: string }} options
 */
export async function compareRegionInk(options) {
  const [reference, test] = await Promise.all([
    decodeGray({ path: options.referencePath, crop: options.referenceCrop, ffmpeg: options.ffmpeg }),
    decodeGray({ path: options.framePath, crop: options.testCrop, ffmpeg: options.ffmpeg }),
  ]);
  if (reference.length === 0 || test.length === 0 || reference.length !== test.length) {
    return { ok: false, reason: `size mismatch reference=${reference.length} test=${test.length}` };
  }
  let squaredError = 0;
  for (let index = 0; index < reference.length; index += 1) {
    squaredError += (reference[index] - test[index]) ** 2;
  }
  const mse = squaredError / reference.length;
  const grayPsnrDb = mse === 0 ? Infinity : 10 * Math.log10((255 * 255) / mse);

  const a = inkMask(reference);
  const b = inkMask(test);
  let intersection = 0;
  let union = 0;
  for (let index = 0; index < a.mask.length; index += 1) {
    const inA = a.mask[index] === 1;
    const inB = b.mask[index] === 1;
    if (inA && inB) intersection += 1;
    if (inA || inB) union += 1;
  }
  return {
    ok: true,
    grayPsnrDb: Number(grayPsnrDb.toFixed(2)),
    inkIoU: union === 0 ? 1 : Number((intersection / union).toFixed(3)),
    inkPixelsReference: a.mask.reduce((sum, value) => sum + value, 0),
    inkPixelsTest: b.mask.reduce((sum, value) => sum + value, 0),
    pixels: reference.length,
  };
}

/**
 * Objective image-fidelity metric: PSNR of the streamed frame (optionally cropped to
 * the page content area) against Playwright's own PNG screenshot of the same page at
 * the same viewport.
 *
 * The psnr filter requires both inputs to have identical dimensions, so both sides are
 * cropped to the same rectangle before comparison.
 *
 * @param {{ referencePath: string, testPath: string, testCrop?: { x: number, y: number, width: number, height: number }, referenceCrop?: { x: number, y: number, width: number, height: number }, ffmpeg?: string }} options
 */
export async function measurePsnr(options) {
  const { referencePath, testPath, testCrop, referenceCrop } = options;
  const ffmpeg = options.ffmpeg ?? "ffmpeg";
  const parts = [];
  if (testCrop) {
    parts.push(`[0:v]crop=${testCrop.width}:${testCrop.height}:${testCrop.x}:${testCrop.y}[a]`);
  }
  if (referenceCrop) {
    parts.push(`[1:v]crop=${referenceCrop.width}:${referenceCrop.height}:${referenceCrop.x}:${referenceCrop.y}[b]`);
  }
  const testLabel = testCrop ? "[a]" : "[0:v]";
  const referenceLabel = referenceCrop ? "[b]" : "[1:v]";
  const filter = parts.length > 0 ? `${parts.join(";")};${testLabel}${referenceLabel}psnr` : "[0:v][1:v]psnr";
  const args = [
    "-hide_banner",
    "-i",
    testPath,
    "-i",
    referencePath,
    "-lavfi",
    filter,
    "-f",
    "null",
    "-",
  ];
  const child = spawn(ffmpeg, args, { stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });
  await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg psnr exit ${code}: ${stderr.slice(-300)}`))));
  });
  const match = /average:\s*([0-9.]+|inf)/.exec(stderr);
  return {
    psnrDb: match ? (match[1] === "inf" ? Infinity : Number(match[1])) : null,
    stderrTail: stderr.split("\n").filter(Boolean).slice(-4).join(" | "),
  };
}

