#!/usr/bin/env node
// Install the browser this prototype needs, using the pinned local Playwright version.
//
//   node tools/fetch-browsers.mjs
//
// Playwright keeps browsers in a shared cache (on Linux ~/.cache/ms-playwright) and
// reuses an already-downloaded matching build. This prototype never downloads anything
// at run time: without a browser present, tools/serve.mjs fails with Playwright's own
// error naming the missing build and this command.
//
// Nothing here needs root, and no package manager is invoked.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { access } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";
import { PROTOTYPE_DIR } from "../common/paths.mjs";

const execFileAsync = promisify(execFile);
const cli = join(PROTOTYPE_DIR, "node_modules", "playwright", "cli.js");

try {
  await access(cli, constants.R_OK);
} catch {
  console.error(`playwright is not installed. Run first: npm install --no-audit --no-fund`);
  process.exit(1);
}

const { stdout: version } = await execFileAsync(process.execPath, [cli, "--version"], { timeout: 60000 });
console.log(`using ${version.trim()} from node_modules/playwright`);

const { stdout, stderr } = await execFileAsync(process.execPath, [cli, "install", "chromium"], {
  timeout: 600000,
  maxBuffer: 16 * 1024 * 1024,
});
process.stdout.write(stdout);
process.stderr.write(stderr);
console.log("chromium is installed in the shared Playwright browser cache.");
