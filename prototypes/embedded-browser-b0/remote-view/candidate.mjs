// Candidate 2 — "remote-view".
//
// A headed Chromium (connected to Playwright) runs inside an isolated Xvfb virtual
// display. The display is captured with ffmpeg/x11grab and the resulting JPEG frames
// are streamed to the viewer; viewer input is injected back into the display with
// xdotool (XTEST). No raw VNC or CDP endpoint leaves the prototype.
//
// Not covered by B0: a window manager. Without one, focus follows the pointer and
// there are no native window decorations of our own; Chromium draws its own client
// side chrome. This is a deliberate limitation recorded in the evidence report.

import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { chromium } from "playwright";
import { isolatedEnv } from "../common/env.mjs";
import { createPageTools } from "../common/page-tools.mjs";
import { CAPTURE_FPS, ENCODING, VIEWPORT, XVFB_SCREEN } from "../common/config.mjs";
import { keysymForEvent, modifierMask } from "../common/keys.mjs";
import { createScreenCapture, findOriginMarker, grabRawRgb } from "./capture.mjs";
import { startXvfb } from "./xvfb.mjs";

const execFileAsync = promisify(execFile);

/** @param {string} display */
async function listWindows(env, display) {
  /** @type {Array<{ id: string, name: string, width: number, height: number }>} */
  const windows = [];
  /** @type {Set<string>} */
  const ids = new Set();
  for (const search of [
    ["search", "--onlyvisible", "--class", "chromium"],
    ["search", "--onlyvisible", "--class", "Chromium"],
    ["search", "--onlyvisible", "--class", "google-chrome"],
    ["search", "--onlyvisible", "--name", "Fixture"],
  ]) {
    try {
      const { stdout } = await execFileAsync("xdotool", search, { env: { ...env, DISPLAY: display } });
      for (const id of stdout.trim().split("\n").filter(Boolean)) ids.add(id);
    } catch {
      // no match for this search
    }
  }
  for (const id of ids) {
    try {
      const { stdout } = await execFileAsync("xdotool", ["getwindowgeometry", "--shell", id], {
        env: { ...env, DISPLAY: display },
      });
      const values = Object.fromEntries(
        stdout
          .trim()
          .split("\n")
          .map((line) => line.split("="))
          .filter((parts) => parts.length === 2),
      );
      windows.push({
        id,
        name: values.WINDOW ?? id,
        width: Number(values.WIDTH ?? 0),
        height: Number(values.HEIGHT ?? 0),
      });
    } catch {
      // window disappeared
    }
  }
  return windows.sort((a, b) => b.width * b.height - a.width * a.height);
}

/**
 * @param {{ runDir: string, fixture: Awaited<ReturnType<typeof import("../fixture/server.mjs").startFixture>>, viewport?: { width: number, height: number }, transport: Awaited<ReturnType<typeof import("../common/viewer-transport.mjs").createViewerTransport>> }} options
 */
export async function createRemoteViewCandidate(options) {
  const { runDir, fixture, transport } = options;
  const viewport = options.viewport ?? VIEWPORT;
  const qscale = ENCODING["remote-view"].quality;

  /** @type {import("playwright").Browser | null} */
  let browser = null;
  /** @type {import("playwright").BrowserContext | null} */
  let context = null;
  /** @type {import("playwright").Page | null} */
  let page = null;
  /** @type {import("playwright").CDPSession | null} */
  let cdp = null;
  /** @type {Awaited<ReturnType<typeof startXvfb>> | null} */
  let xvfb = null;
  /** @type {NodeJS.ProcessEnv | null} */
  let childEnv = null;
  /** @type {ReturnType<typeof createScreenCapture> | null} */
  let capture = null;

  const calibration = {
    expectedContentOrigin: null,
    detectedContentOrigin: null,
    chromeHeight: 0,
    windowGeometry: null,
    displayGeometry: null,
    captureRegion: null,
    windowId: null,
    markerFound: false,
    deltaPx: null,
  };
  const frameStats = { seq: 0, width: 0, height: 0, bytes: 0, lastFrameAt: null, snapshots: 0 };

  let connectionCount = 0;
  /** Serializes viewer input: xdotool calls must not overtake each other. */
  let inputQueue = Promise.resolve();

  const pageTools = createPageTools({
    getPage: () => {
      if (!page) throw new Error("page not started");
      return page;
    },
    getContext: () => {
      if (!context) throw new Error("context not started");
      return context;
    },
    getCdp: () => cdp,
  });

  /** @param {string[]} args */
  async function xdotool(args) {
    if (!childEnv || !xvfb) throw new Error("display not started");
    return await execFileAsync("xdotool", args, {
      env: { ...childEnv, DISPLAY: xvfb.display },
      timeout: 10000,
    });
  }

  /** Page coordinates -> screen coordinates. */
  function toScreen(x, y) {
    const origin = calibration.detectedContentOrigin ?? { x: 0, y: 0 };
    return { x: Math.round(origin.x + x), y: Math.round(origin.y + y) };
  }

  async function focusWindow() {
    const windows = await listWindows(childEnv ?? {}, xvfb?.display ?? ":0");
    const target = windows.find((entry) => entry.width >= viewport.width * 0.9) ?? windows[0];
    if (!target) return null;
    calibration.windowId = target.id;
    await xdotool(["windowraise", target.id]);
    await xdotool(["windowfocus", "--sync", target.id]);
    return target;
  }

  async function sendSnapshot(note) {
    if (!childEnv || !xvfb || !calibration.captureRegion) return;
    const region = calibration.captureRegion;
    const rgb = await grabRawRgb({ display: xvfb.display, region, env: childEnv });
    if (rgb.length < region.width * region.height * 3) return;
    // Re-encode through ffmpeg so the snapshot has the same codec/quality as frames.
    const jpeg = await new Promise((resolve, reject) => {
      const child = spawn(
        "ffmpeg",
        [
          "-hide_banner",
          "-loglevel",
          "error",
          "-f",
          "rawvideo",
          "-pix_fmt",
          "rgb24",
          "-s",
          `${region.width}x${region.height}`,
          "-i",
          "pipe:0",
          "-frames:v",
          "1",
          "-c:v",
          "mjpeg",
          "-q:v",
          String(qscale),
          "-f",
          "image2pipe",
          "pipe:1",
        ],
        { env: childEnv, stdio: ["pipe", "pipe", "pipe"] },
      );
      /** @type {Buffer[]} */
      const chunks = [];
      child.stdout.on("data", (chunk) => chunks.push(chunk));
      child.on("error", reject);
      child.on("exit", (code) =>
        code === 0 ? resolve(Buffer.concat(chunks)) : reject(new Error(`snapshot encode exit ${code}`)),
      );
      child.stdin.end(rgb);
    });
    frameStats.seq += 1;
    frameStats.snapshots += 1;
    frameStats.bytes += jpeg.byteLength;
    transport.sendFrame(jpeg, {
      seq: frameStats.seq,
      width: region.width,
      height: region.height,
      ts: Date.now(),
      snapshot: true,
      note,
    });
  }

  async function startCapture() {
    if (!childEnv || !xvfb || !calibration.captureRegion) return;
    if (!capture) {
      capture = createScreenCapture({
        display: xvfb.display,
        region: calibration.captureRegion,
        fps: CAPTURE_FPS,
        qscale,
        env: childEnv,
        onFrame: (jpeg, meta) => {
          frameStats.seq += 1;
          frameStats.width = calibration.captureRegion?.width ?? 0;
          frameStats.height = calibration.captureRegion?.height ?? 0;
          frameStats.bytes += jpeg.byteLength;
          frameStats.lastFrameAt = meta.ts;
          transport.sendFrame(jpeg, {
            seq: frameStats.seq,
            width: frameStats.width,
            height: frameStats.height,
            ts: meta.ts,
          });
        },
      });
    }
    capture.start();
  }

  async function stopCapture() {
    if (!capture) return;
    await capture.stop();
  }

  transport.onConnect(async (socket) => {
    connectionCount += 1;
    try {
      await focusWindow();
      await startCapture();
      await sendSnapshot(`snapshot for client ${connectionCount}`);
      transport.sendState({ kind: "page", ...(await pageTools.snapshot()) });
    } catch (error) {
      socket.send(JSON.stringify({ t: "note", ts: Date.now(), text: `remote-view attach failed: ${String(error)}` }));
    }
  });

  transport.onDisconnect(async (remaining) => {
    if (remaining === 0) await stopCapture();
  });

  transport.onInput((message) => {
    // Serialized on purpose: each event costs one or more xdotool process launches,
    // and letting them run concurrently reorders pointer/click/scroll events.
    inputQueue = inputQueue.then(async () => {
      if (message.t === "snapshot") {
        await sendSnapshot("viewer requested");
        return;
      }
      if (message.t !== "input") return;
      const kind = message.kind;

      if (kind === "pointer") {
        const { x, y } = toScreen(Number(message.x ?? 0), Number(message.y ?? 0));
        await xdotool(["mousemove", "--sync", String(x), String(y)]);
        if (message.action === "down") await xdotool(["mousedown", String(message.button === "right" ? 3 : 1)]);
        else if (message.action === "up") await xdotool(["mouseup", String(message.button === "right" ? 3 : 1)]);
        return;
      }

      if (kind === "wheel") {
        const { x, y } = toScreen(Number(message.x ?? 0), Number(message.y ?? 0));
        await xdotool(["mousemove", "--sync", String(x), String(y)]);
        const deltaY = Number(message.deltaY ?? 0);
        const button = deltaY > 0 ? "5" : "4";
        const clicks = Math.min(8, Math.max(1, Math.round(Math.abs(deltaY) / 53) || 1));
        await xdotool(["click", "--repeat", String(clicks), "--delay", "12", button]);
        return;
      }

      if (kind === "key") {
        const keysym = keysymForEvent({ key: String(message.key ?? ""), code: String(message.code ?? "") });
        const mask = modifierMask(/** @type {never} */ (message.modifiers ?? {}));
        const modifierKeys = [];
        if (mask & 2) modifierKeys.push("ctrl");
        if (mask & 1) modifierKeys.push("alt");
        if (mask & 4) modifierKeys.push("super");
        if (mask & 8) modifierKeys.push("shift");
        if (message.action === "up") {
          await xdotool(["keyup", "--clearmodifiers", keysym]);
          for (const modifier of [...modifierKeys].reverse()) await xdotool(["keyup", modifier]);
        } else {
          for (const modifier of modifierKeys) await xdotool(["keydown", modifier]);
          await xdotool(["keydown", keysym]);
        }
        return;
      }

      if (kind === "text") {
        const text = String(message.text ?? "");
        if (text.length === 0) return;
        await xdotool(["type", "--clearmodifiers", "--delay", "0", "--", text]);
      }
    });
    inputQueue = inputQueue.catch((error) => {
      transport.sendNote(`input failed: ${String(error)}`);
    });
    return inputQueue;
  });

  return {
    name: "remote-view",
    frameStats,
    tools: pageTools,
    calibration,
    captureStats: () => capture?.stats ?? null,
    async start() {
      childEnv = await isolatedEnv({ runDir, display: null });
      xvfb = await startXvfb({ screen: XVFB_SCREEN, env: childEnv, firstDisplay: 90 });
      childEnv = { ...childEnv, DISPLAY: xvfb.display };

      browser = await chromium.launch({
        headless: false,
        channel: "chromium",
        env: childEnv,
        args: [
          "--ozone-platform=x11",
          "--no-first-run",
          "--no-default-browser-check",
          "--window-position=0,0",
          `--window-size=${viewport.width},${Math.min(XVFB_SCREEN.height, viewport.height + 120)}`,
          "--disable-features=Translate,MediaRouter,OptimizationHints",
          "--disable-background-networking",
          "--mute-audio",
        ],
      });
      context = await browser.newContext({ viewport: null, locale: "en-US", timezoneId: "UTC" });
      page = context.pages()[0] ?? (await context.newPage());
      cdp = await context.newCDPSession(page);
      await cdp.send("Page.enable");

      const session = `remote-view-${Date.now().toString(36)}`;
      await page.goto(`${fixture.indexUrl}?session=${session}`, { waitUntil: "load", timeout: 30000 });
      await page.waitForFunction(() => typeof window.__FIXTURE_STATE__ === "function", null, { timeout: 15000 });

      await this.fitWindow();
      await this.calibrate();
      return await this.info();
    },
    /** Resize the browser window so the page viewport matches the frame-stream candidate. */
    async fitWindow() {
      if (!page || !cdp) return null;
      const { windowId } = await cdp.send("Browser.getWindowForTarget");
      /** @type {{ x: number, y: number, width: number, height: number }} */
      let geometry = await page.evaluate(() => ({
        x: window.screenX,
        y: window.screenY,
        width: window.innerWidth,
        height: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
      }));
      for (let attempt = 0; attempt < 4; attempt += 1) {
        const chromeHeight = Math.max(0, geometry.outerHeight - geometry.height);
        await cdp.send("Browser.setWindowBounds", {
          windowId,
          bounds: {
            windowState: "normal",
            left: 0,
            top: 0,
            width: viewport.width,
            height: viewport.height + chromeHeight,
          },
        });
        await page.waitForTimeout(300);
        geometry = await page.evaluate(() => ({
          x: window.screenX,
          y: window.screenY,
          width: window.innerWidth,
          height: window.innerHeight,
          outerWidth: window.outerWidth,
          outerHeight: window.outerHeight,
        }));
        if (geometry.width === viewport.width && geometry.height === viewport.height) break;
      }
      calibration.windowGeometry = geometry;
      calibration.chromeHeight = Math.max(0, geometry.outerHeight - geometry.height);
      calibration.expectedContentOrigin = {
        x: geometry.x,
        y: geometry.y + calibration.chromeHeight,
      };
      await focusWindow();
      return geometry;
    },
    /** Locate the page origin in the display so input/frame coordinates can be mapped. */
    async calibrate() {
      if (!xvfb || !childEnv) throw new Error("display not started");
      const region = { x: 0, y: 0, width: XVFB_SCREEN.width, height: XVFB_SCREEN.height };
      const rgb = await grabRawRgb({ display: xvfb.display, region, env: childEnv });
      const marker = findOriginMarker(rgb, { width: region.width, height: region.height });
      calibration.markerFound = marker !== null;
      if (marker) {
        calibration.detectedContentOrigin = marker;
        calibration.deltaPx = calibration.expectedContentOrigin
          ? Math.hypot(marker.x - calibration.expectedContentOrigin.x, marker.y - calibration.expectedContentOrigin.y)
          : null;
        calibration.captureRegion = {
          x: 0,
          y: 0,
          width: Math.min(XVFB_SCREEN.width, marker.x + viewport.width),
          height: Math.min(XVFB_SCREEN.height, marker.y + viewport.height),
        };
      } else {
        calibration.detectedContentOrigin = calibration.expectedContentOrigin ?? { x: 0, y: 0 };
        calibration.captureRegion = {
          x: 0,
          y: 0,
          width: viewport.width,
          height: viewport.height + calibration.chromeHeight,
        };
      }
      calibration.displayGeometry = xvfb.geometry;
      return calibration;
    },
    async info() {
      const identity = await pageTools.identity();
      let commandLine = null;
      try {
        const browserSession = await browser?.newBrowserCDPSession();
        if (browserSession) {
          const result = await browserSession.send("Browser.getBrowserCommandLine");
          commandLine = result.arguments;
          await browserSession.detach().catch(() => {});
        }
      } catch {
        commandLine = null;
      }
      return {
        candidate: this.name,
        browserVersion: browser?.version() ?? null,
        executablePath: browser?.browserType().executablePath() ?? null,
        headless: false,
        display: xvfb?.display ?? null,
        xvfbBinary: xvfb?.binary ?? null,
        profileDir: commandLine?.find((arg) => arg.startsWith("--user-data-dir="))?.slice("--user-data-dir=".length) ?? null,
        usesRemoteDebuggingPipe: commandLine?.some((arg) => arg.startsWith("--remote-debugging-pipe")) ?? null,
        remoteDebuggingPortArgument:
          commandLine?.find((arg) => arg.startsWith("--remote-debugging-port")) ?? null,
        viewport,
        chromeHeight: calibration.chromeHeight,
        contentOrigin: calibration.detectedContentOrigin,
        calibration,
        frameSize: calibration.captureRegion ?? { width: viewport.width, height: viewport.height },
        targetId: identity.targetId,
        url: identity.url,
        title: identity.title,
        transport: {
          url: transport.url,
          wsUrl: transport.wsUrl,
          port: transport.port,
        },
        commandLine,
      };
    },
    async stop() {
      await stopCapture().catch(() => {});
      if (browser) await browser.close().catch(() => {});
      browser = null;
      context = null;
      page = null;
      cdp = null;
      if (xvfb) await xvfb.stop().catch(() => {});
      xvfb = null;
    },
  };
}
