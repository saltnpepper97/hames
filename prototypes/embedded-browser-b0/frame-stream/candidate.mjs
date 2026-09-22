// Candidate 1 — "frame-stream".
//
// A dedicated Playwright Chromium context whose page is streamed as CDP
// Page.startScreencast JPEG frames, with viewer input translated into CDP input
// events. The viewer is only an observer/controller of the stream: disconnecting it
// never touches the browser, context, or page.

import { chromium } from "playwright";
import { isolatedEnv } from "../common/env.mjs";
import { createPageTools } from "../common/page-tools.mjs";
import {
  codeForChar,
  keyForChar,
  modifierMask,
  textForEvent,
  virtualKeyForChar,
  virtualKeyForEvent,
} from "../common/keys.mjs";
import { ENCODING, VIEWPORT } from "../common/config.mjs";

/**
 * @param {{ runDir: string, fixture: Awaited<ReturnType<typeof import("../fixture/server.mjs").startFixture>>, viewport?: { width: number, height: number }, transport: Awaited<ReturnType<typeof import("../common/viewer-transport.mjs").createViewerTransport>> }} options
 */
export async function createFrameStreamCandidate(options) {
  const { runDir, fixture, transport } = options;
  const viewport = options.viewport ?? VIEWPORT;
  const quality = ENCODING["frame-stream"].quality;

  /** @type {import("playwright").Browser | null} */
  let browser = null;
  /** @type {import("playwright").BrowserContext | null} */
  let context = null;
  /** @type {import("playwright").Page | null} */
  let page = null;
  /** @type {import("playwright").CDPSession | null} */
  let cdp = null;

  const frameStats = {
    events: 0,
    sent: 0,
    bytes: 0,
    snapshots: 0,
    ackErrors: 0,
    width: viewport.width,
    height: viewport.height,
    lastFrameAt: null,
    intervals: /** @type {number[]} */ ([]),
  };
  let frameSeq = 0;
  let streaming = false;
  let lastSentAt = 0;
  /** Serializes viewer input so events cannot be reordered. */
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

  async function sendSnapshot(note) {
    if (!cdp) return;
    const shot = await cdp.send("Page.captureScreenshot", { format: "jpeg", quality, captureBeyondViewport: false });
    const jpeg = Buffer.from(shot.data, "base64");
    frameSeq += 1;
    frameStats.snapshots += 1;
    frameStats.sent += 1;
    frameStats.bytes += jpeg.byteLength;
    const ts = Date.now();
    lastSentAt = ts;
    transport.sendFrame(jpeg, {
      seq: frameSeq,
      width: viewport.width,
      height: viewport.height,
      ts,
      snapshot: true,
      note,
    });
  }

  async function startStream() {
    if (!cdp || streaming) return;
    streaming = true;
    await cdp.send("Page.startScreencast", {
      format: "jpeg",
      quality,
      maxWidth: viewport.width,
      maxHeight: viewport.height,
      everyNthFrame: 1,
    });
  }

  async function stopStream() {
    if (!cdp || !streaming) return;
    streaming = false;
    try {
      await cdp.send("Page.stopScreencast");
    } catch {
      // page may already be gone during shutdown
    }
  }

  /** @param {{ action: "move" | "down" | "up", x: number, y: number, button?: string, clickCount?: number }} message */
  async function pointer(message) {
    if (!cdp) return;
    const button = message.button ?? "left";
    const x = Math.max(0, Math.min(viewport.width - 1, message.x));
    const y = Math.max(0, Math.min(viewport.height - 1, message.y));
    const buttons = message.action === "down" ? 1 : message.action === "up" ? 0 : message.action === "move" ? (message.clickCount ? 1 : 0) : 0;
    const type = message.action === "move" ? "mouseMoved" : message.action === "down" ? "mousePressed" : "mouseReleased";
    await cdp.send("Input.dispatchMouseEvent", {
      type,
      x,
      y,
      button,
      buttons,
      clickCount: message.clickCount ?? 1,
    });
  }

  /** @param {{ x: number, y: number, deltaX?: number, deltaY?: number }} message */
  async function wheel(message) {
    if (!cdp) return;
    await cdp.send("Input.dispatchMouseEvent", {
      type: "mouseWheel",
      x: Math.max(0, Math.min(viewport.width - 1, message.x)),
      y: Math.max(0, Math.min(viewport.height - 1, message.y)),
      deltaX: message.deltaX ?? 0,
      deltaY: message.deltaY ?? 0,
      button: "none",
      buttons: 0,
    });
  }

  /**
   * @param {"down" | "up"} action
   * @param {{ key: string, code?: string, text?: string | null, modifiers?: Record<string, boolean> }} event
   */
  async function key(action, event) {
    if (!cdp) return;
    const mask = modifierMask(event.modifiers ?? {});
    const text = event.text === undefined ? textForEvent(event) : event.text;
    const keyDownType = action === "up" ? "keyUp" : text ? "keyDown" : "rawKeyDown";
    await cdp.send("Input.dispatchKeyEvent", {
      type: keyDownType,
      key: event.key,
      code: event.code ?? "",
      text: action === "down" && text ? text : undefined,
      unmodifiedText: action === "down" && text ? text : undefined,
      windowsVirtualKeyCode: virtualKeyForEvent({ key: event.key, code: event.code }),
      nativeVirtualKeyCode: virtualKeyForEvent({ key: event.key, code: event.code }),
      modifiers: mask,
    });
  }

  /** Type a string as real key events so the page sees keydown/keypress/input. */
  async function typeText(text) {
    for (const char of text) {
      await key("down", { key: keyForChar(char), code: codeForChar(char), text: char, modifiers: {} });
      await key("up", { key: keyForChar(char), code: codeForChar(char), text: null, modifiers: {} });
    }
  }

  transport.onConnect(async (socket) => {
    // A viewer can attach at any time; the page never depends on this.
    await startStream();
    try {
      await sendSnapshot(`snapshot for client ${transport.stats.connects}`);
      transport.sendState({ kind: "page", ...(await pageTools.snapshot()) });
    } catch (error) {
      socket.send(JSON.stringify({ t: "note", ts: Date.now(), text: `snapshot failed: ${String(error)}` }));
    }
  });

  transport.onDisconnect(async (remaining) => {
    if (remaining === 0) await stopStream();
  });

  transport.onInput((message) => {
    // Input is serialized: a later event must not overtake an earlier one (the
    // virtual-display candidate spends milliseconds in xdotool per event, so
    // concurrent handling would reorder pointer and click events).
    inputQueue = inputQueue.then(async () => {
      const t = message.t;
      if (t === "snapshot") {
        await sendSnapshot("viewer requested");
        return;
      }
      if (t !== "input") return;
      switch (message.kind) {
        case "pointer":
          await pointer(/** @type {never} */ (message));
          break;
        case "wheel":
          await wheel(/** @type {never} */ (message));
          break;
        case "key":
          await key(message.action === "up" ? "up" : "down", /** @type {never} */ (message));
          break;
        case "text":
          await typeText(/** @type {string} */ (message.text ?? ""));
          break;
        default:
          break;
      }
    });
    inputQueue = inputQueue.catch((error) => {
      transport.sendNote(`input failed: ${String(error)}`);
    });
    return inputQueue;
  });

  return {
    name: "frame-stream",
    frameStats,
    tools: pageTools,
    async start() {
      const env = await isolatedEnv({ runDir });
      browser = await chromium.launch({
        headless: true,
        channel: "chromium",
        env,
        args: [
          "--no-first-run",
          "--no-default-browser-check",
          "--disable-features=Translate,MediaRouter,OptimizationHints",
          "--disable-background-networking",
          "--mute-audio",
        ],
      });
      context = await browser.newContext({
        viewport,
        deviceScaleFactor: 1,
        locale: "en-US",
        timezoneId: "UTC",
        reducedMotion: "no-preference",
      });
      page = await context.newPage();
      cdp = await context.newCDPSession(page);
      await cdp.send("Page.enable");
      cdp.on("Page.screencastFrame", async (event) => {
        frameStats.events += 1;
        const ts = Date.now();
        if (streaming) {
          const jpeg = Buffer.from(event.data, "base64");
          frameSeq += 1;
          frameStats.sent += 1;
          frameStats.bytes += jpeg.byteLength;
          if (lastSentAt > 0) frameStats.intervals.push(ts - lastSentAt);
          lastSentAt = ts;
          frameStats.lastFrameAt = ts;
          frameStats.width = event.metadata.deviceWidth;
          frameStats.height = event.metadata.deviceHeight;
          transport.sendFrame(jpeg, {
            seq: frameSeq,
            width: event.metadata.deviceWidth,
            height: event.metadata.deviceHeight,
            ts,
          });
        }
        try {
          await cdp?.send("Page.screencastFrameAck", { sessionId: event.sessionId });
        } catch {
          frameStats.ackErrors += 1;
        }
      });

      // The overlay scrollbar/keyboard behaviour is the browser's business; we only
      // need the page focused so CDP key events reach it.
      page.on("framenavigated", (frame) => {
        if (frame === page?.mainFrame()) transport.sendNote(`page navigated to ${frame.url()}`);
      });

      const session = `frame-stream-${Date.now().toString(36)}`;
      await page.goto(`${fixture.indexUrl}?session=${session}`, { waitUntil: "load", timeout: 30000 });
      await page.waitForFunction(() => typeof window.__FIXTURE_STATE__ === "function", null, { timeout: 15000 });
      await page.bringToFront();
      return await this.info();
    },
    async info() {
      const identity = await pageTools.identity();
      return {
        candidate: this.name,
        browserVersion: browser?.version() ?? null,
        executablePath: browser?.browserType().executablePath() ?? null,
        headless: true,
        display: null,
        profileDir: null,
        viewport,
        chromeHeight: 0,
        contentOrigin: { x: 0, y: 0 },
        frameSize: { width: viewport.width, height: viewport.height },
        targetId: identity.targetId,
        url: identity.url,
        title: identity.title,
        transport: {
          url: transport.url,
          wsUrl: transport.wsUrl,
          port: transport.port,
        },
      };
    },
    async stop() {
      await stopStream().catch(() => {});
      if (browser) await browser.close().catch(() => {});
      browser = null;
      context = null;
      page = null;
      cdp = null;
    },
  };
}
