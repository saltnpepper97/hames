#!/usr/bin/env node
// Fetch and unpack the Xvfb binary used by the remote-view candidate.
//
// Why this exists: this host has no Xvfb and installing it system-wide needs root.
// The prototype therefore downloads the Arch package once, records its checksum, and
// extracts it into prototype-local vendor/ (gitignored). No root, no system change.
//
// Usage:
//   node tools/fetch-xvfb.mjs                 # download if missing
//   node tools/fetch-xvfb.mjs --verify        # check recorded checksum only
//   node tools/fetch-xvfb.mjs --force         # re-download

import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";
import { VENDOR_DIR } from "../common/paths.mjs";

const execFileAsync = promisify(execFile);

// Pinned so the recorded version is reproducible; the mirror serves the same file.
const PACKAGE = {
  name: "xorg-server-xvfb",
  version: "21.1.24-1",
  file: "xorg-server-xvfb-21.1.24-1-x86_64.pkg.tar.zst",
  url: "https://geo.mirror.pkgbuild.com/extra/os/x86_64/xorg-server-xvfb-21.1.24-1-x86_64.pkg.tar.zst",
};

const args = new Set(process.argv.slice(2));

async function exists(path) {
  try {
    await access(path, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

async function sha256(path) {
  const data = await readFile(path);
  return createHash("sha256").update(data).digest("hex");
}

async function main() {
  const downloadDir = join(VENDOR_DIR, "download");
  const extractDir = VENDOR_DIR;
  const lockPath = join(VENDOR_DIR, "xvfb.lock.json");
  const packagePath = join(downloadDir, PACKAGE.file);
  const binaryPath = join(extractDir, "usr", "bin", "Xvfb");

  await mkdir(downloadDir, { recursive: true });

  /** @type {{ package: typeof PACKAGE, sha256: string, fetchedAt: string } | null} */
  let lock = null;
  if (await exists(lockPath)) lock = JSON.parse(await readFile(lockPath, "utf8"));

  if (args.has("--verify")) {
    if (!lock) {
      console.error("no vendor/xvfb.lock.json; run `node tools/fetch-xvfb.mjs` first");
      process.exit(2);
    }
    if (!(await exists(packagePath))) {
      console.error(`recorded package missing at ${packagePath}`);
      process.exit(2);
    }
    const actual = await sha256(packagePath);
    if (actual !== lock.sha256) {
      console.error(`checksum mismatch: recorded ${lock.sha256}, actual ${actual}`);
      process.exit(3);
    }
    console.log(`ok ${PACKAGE.file} sha256=${actual}`);
    return;
  }

  if (await exists(binaryPath) && !args.has("--force")) {
    console.log(`Xvfb already extracted at ${binaryPath}${lock ? ` (sha256=${lock.sha256})` : ""}`);
    return;
  }

  if (!(await exists(packagePath)) || args.has("--force")) {
    console.log(`downloading ${PACKAGE.url}`);
    await execFileAsync("curl", ["-fsSL", "-o", packagePath, PACKAGE.url], { timeout: 180000 });
  }
  const digest = await sha256(packagePath);
  if (lock && !args.has("--force") && lock.sha256 !== digest) {
    console.error(`refusing to use package: checksum mismatch (recorded ${lock.sha256}, actual ${digest})`);
    process.exit(3);
  }

  console.log(`extracting into ${extractDir}`);
  await execFileAsync("bsdtar", ["-xf", packagePath, "-C", extractDir], { timeout: 120000 });
  await writeFile(
    lockPath,
    `${JSON.stringify({ package: PACKAGE, sha256: digest, fetchedAt: new Date().toISOString() }, null, 2)}\n`,
  );
  console.log(`done: ${binaryPath} (sha256=${digest})`);
}

await main();
