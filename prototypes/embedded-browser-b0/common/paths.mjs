// Filesystem layout helpers for the B0 prototype. Everything stays inside the
// prototype directory; no Hames state directory is used or touched.

import { mkdtemp, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Absolute path of prototypes/embedded-browser-b0 */
export const PROTOTYPE_DIR = dirname(dirname(fileURLToPath(import.meta.url)));

/** Vendored tools (Xvfb) fetched by tools/fetch-xvfb.mjs; gitignored. */
export const VENDOR_DIR = join(PROTOTYPE_DIR, "vendor");

/** Disposable per-run scratch (profiles, temp homes, logs); gitignored. */
export const RUNS_DIR = join(PROTOTYPE_DIR, ".run");

/** Measurement JSON and sample frames that are kept as evidence. */
export const MEASUREMENTS_DIR = join(PROTOTYPE_DIR, "measurements");

export const FRAMES_DIR = join(MEASUREMENTS_DIR, "frames");

/**
 * Create a fresh disposable run directory.
 *
 * @param {string} prefix
 */
export async function createRunDir(prefix) {
  await mkdir(RUNS_DIR, { recursive: true });
  return await mkdtemp(join(RUNS_DIR, `${prefix}-`));
}

/** @param {string} path */
export async function ensureDir(path) {
  await mkdir(path, { recursive: true });
  return path;
}

/**
 * The repository root, used only for reporting which tree the prototype lived in.
 */
export const REPO_ROOT = join(PROTOTYPE_DIR, "..", "..");
