// The B0 interaction script.
//
// One script drives both candidates. Every step either
//   (a) acts through the viewer transport only (as a human would), then verifies the
//       effect from Playwright's side, or
//   (b) acts through Playwright, then verifies the effect is visible in the streamed
//       frames — the two halves of the bidirectional proof.
//
// Together they check: pointer, typing, form submit, keyboard shortcuts, canvas
// interaction, wheel scrolling, link navigation (same and cross origin), a failing
// request, latency, image-fidelity agreement between the two paths, and viewer
// disconnect/reconnect without touching the browser.

import { jpegToPng, measurePsnr, changedRegion, compareRegionInk, meanDelta } from "./jpeg.mjs";
import { PROBE_COLORS, PROBE_MEAN_DELTA } from "./config.mjs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Record a value with a bounded number of decimals where relevant. */
const round = (value, digits = 2) => (typeof value === "number" ? Number(value.toFixed(digits)) : value);

/**
 * @param {{
 *   viewer: ReturnType<typeof import("./viewer-client.mjs").createViewerClient>,
 *   control: {
 *     info: () => Promise<Record<string, any>>,
 *     state: () => Promise<Record<string, any>>,
 *     action: (type: string, body?: Record<string, unknown>) => Promise<Record<string, any>>,
 *     evaluate: (expression: string) => Promise<any>,
 *     screenshot: (path: string) => Promise<any>,
 *   },
 *   viewport: { width: number, height: number },
 *   artifactsDir: string,
 *   log: (message: string) => void,
 *   stopOnFailure?: boolean,
 * }} options
 */
export function createInteractionScript(options) {
  const { viewer, control, viewport, artifactsDir, log } = options;
  const stopOnFailure = options.stopOnFailure ?? true;

  /** Resolve once, before the first step: how to map page coordinates into frames. */
  let layout = { contentOrigin: { x: 0, y: 0 }, frameSize: { ...viewport }, chromeHeight: 0, fixture: null, info: null };

  /** Retry helper: navigation invalidates the JS execution context for a moment. */
  async function retry(work, options = {}) {
    const attempts = options.attempts ?? 8;
    const delayMs = options.delayMs ?? 150;
    let lastError = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await work();
      } catch (error) {
        lastError = error;
        await sleep(delayMs);
      }
    }
    throw lastError ?? new Error("retry exhausted");
  }

  /** Page-side state, tolerating the execution-context gap during navigation. */
  async function pageState() {
    return await retry(() => control.state(), { attempts: 12, delayMs: 150 });
  }

  /** Page-side evaluate, tolerating the same gap. */
  async function pageEvaluate(expression) {
    return await retry(() => control.evaluate(expression), { attempts: 12, delayMs: 150 });
  }

  async function loadLayout() {
    const info = await control.info();
    layout = {
      contentOrigin: info.info?.contentOrigin ?? { x: 0, y: 0 },
      frameSize: info.info?.frameSize ?? { ...viewport },
      chromeHeight: info.info?.chromeHeight ?? 0,
      fixture: info.fixture,
      info,
    };
    for (const [name, rect] of Object.entries(PROBE_REGIONS)) {
      // Regions are expressed in page coordinates; frames of the virtual-display
      // candidate also contain the browser chrome, so the content origin is added.
      viewer.registerRegion(name, {
        x: rect.x + layout.contentOrigin.x,
        y: rect.y + layout.contentOrigin.y,
        width: rect.width,
        height: rect.height,
      });
    }
    return layout;
  }

  /** Regions inside the page (not the frame) used for pixel checks. */
  const PROBE_REGIONS = {
    // A 10px column on the far left shows only the page background, i.e. the probe colour.
    "probe-background": { x: 2, y: 320, width: 8, height: 60 },
  };

  async function rectOf(selector) {
    const rect = await pageEvaluate(
      `(() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
          left: Math.round(r.left), top: Math.round(r.top),
          width: Math.round(r.width), height: Math.round(r.height),
          centerX: Math.round(r.left + r.width / 2),
          centerY: Math.round(r.top + r.height / 2),
          viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
        };
      })()`,
    );
    return rect;
  }

  /**
   * Scroll the element into view and wait for scrolling to settle. Chromium animates
   * wheel scrolling, so a previous step's scroll can still be running here.
   */
  async function prepare(selector) {
    let lastRect = null;
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await pageEvaluate(
        `(() => {
          const el = document.querySelector(${JSON.stringify(selector)});
          if (el) el.scrollIntoView({ block: "center", inline: "center" });
          return true;
        })()`,
      );
      await sleep(180);
      const before = await pageEvaluate(
        `(() => { const a = document.getElementById("scroll-area"); return { y: Math.round(window.scrollY), inner: a ? Math.round(a.scrollTop) : 0 }; })()`,
      );
      await sleep(180);
      const after = await pageEvaluate(
        `(() => { const a = document.getElementById("scroll-area"); return { y: Math.round(window.scrollY), inner: a ? Math.round(a.scrollTop) : 0 }; })()`,
      );
      const rect = await rectOf(selector);
      if (!rect) throw new Error(`element not found: ${selector}`);
      lastRect = rect;
      const inside =
        rect.centerX >= 0 && rect.centerY >= 0 && rect.centerX < viewport.width && rect.centerY < viewport.height;
      const settled = before.y === after.y && before.inner === after.inner;
      if (inside && settled) return rect;
    }
    throw new Error(`element ${selector} did not settle inside the viewport: ${JSON.stringify(lastRect)}`);
  }

  /** Click through the viewer; returns the timestamp of the last event sent. */
  async function clickViaViewer(x, y) {
    viewer.sendInput({ kind: "pointer", action: "move", x, y });
    await sleep(30);
    viewer.sendInput({ kind: "pointer", action: "down", x, y, button: "left", clickCount: 1 });
    await sleep(40);
    viewer.sendInput({ kind: "pointer", action: "up", x, y, button: "left", clickCount: 1 });
    return { sentAt: Date.now() };
  }

  async function clickSelector(selector) {
    const rect = await prepare(selector);
    await clickViaViewer(rect.centerX, rect.centerY);
    return rect;
  }

  /**
   * Latency probe: act through the viewer, then time the visible frame change.
   * The clock starts when the action's last input event was sent, so the harness's own
   * inter-event delays do not inflate the measurement.
   */
  async function latencyProbe(label, action) {
    const baseline = await viewer.currentMean();
    if (!baseline) throw new Error("no baseline frame for latency probe");
    const actionResult = await action();
    const startedAt = actionResult?.sentAt ?? Date.now();
    const frame = await viewer.waitForMeanChange({
      baseline,
      threshold: PROBE_MEAN_DELTA,
      timeoutMs: 6000,
      sinceTs: startedAt,
    });
    const latencyMs = frame.arrivalTs - startedAt;
    log(`${label}: input-to-visible-update ${latencyMs} ms (frame seq ${frame.seq})`);
    return { label, latencyMs, frameSeq: frame.seq, baseline, changed: frame.mean };
  }

  async function waitForFixture(predicate, timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const state = await pageState();
        last = state;
        if (predicate(state)) return state;
      } catch (error) {
        // A navigation can invalidate the execution context for a moment; keep polling.
        lastError = error;
      }
      await sleep(150);
    }
    throw new Error(
      `fixture condition not met within ${timeoutMs}ms: ${JSON.stringify(last?.fixture ?? last?.url ?? last)}` +
        (lastError ? ` lastError=${String(lastError)}` : ""),
    );
  }

  /** Compare the streamed frame against Playwright's own render of the same page. */
  async function imageAgreement(label) {
    // The identity panel sits at the top of the page; reset scrolling so that the
    // compared regions are actually on screen in both paths.
    await pageEvaluate(
      `(() => {
        window.scrollTo(0, 0);
        const area = document.getElementById("scroll-area");
        if (area) area.scrollTop = 0;
        return true;
      })()`,
    );
    await sleep(400);
    const snapshot = await viewer.requestSnapshot();
    const referencePath = `${artifactsDir}/${label}-reference.png`;
    const framePath = `${artifactsDir}/${label}-frame.png`;
    // The reference screenshot is taken right after the snapshot frame arrived, so
    // both describe the same instant (only the fixture clock keeps ticking).
    await control.screenshot(referencePath);
    if (!snapshot.jpeg) throw new Error("snapshot frame did not keep its JPEG payload");
    await jpegToPng(snapshot.jpeg, framePath);
    const origin = layout.contentOrigin;
    // Calibration for the virtual-display candidate is verified by the origin marker
    // during startup (deltaPx 0 in the recorded runs), so the comparison uses the exact
    // offset. The best content PSNR over a small +/-3px window is recorded for context
    // but is not used to pick a crop: a shifted crop can score higher while no longer
    // showing the same pixels.
    const baseOffset = { x: 0, y: 0 };
    const contentPsnr = await measurePsnr({
      referencePath,
      testPath: framePath,
      testCrop: { x: origin.x, y: origin.y, width: viewport.width, height: viewport.height },
      referenceCrop: { x: 0, y: 0, width: viewport.width, height: viewport.height },
    });
    const best = await bestAlignmentPsnr({ framePath, referencePath, region: viewport });
    const regions = {};
    for (const [name, selector] of Object.entries(IDENTITY_TEXT_REGIONS)) {
      const rect = await rectOf(selector);
      if (!rect) continue;
      if (rect.top < 2 || rect.top + rect.height > viewport.height - 2) {
        regions[name] = { selector, rect, skipped: "outside the viewport" };
        continue;
      }
      const padded = {
        x: Math.max(0, rect.left - 12),
        y: Math.max(0, rect.top - 6),
        width: Math.min(viewport.width - Math.max(0, rect.left - 12), rect.width + 24),
        height: Math.min(viewport.height - Math.max(0, rect.top - 6), rect.height + 12),
      };
      const result = await measurePsnr({
        referencePath,
        testPath: framePath,
        testCrop: {
          x: padded.x + origin.x + baseOffset.x,
          y: padded.y + origin.y + baseOffset.y,
          width: padded.width,
          height: padded.height,
        },
        referenceCrop: { x: padded.x, y: padded.y, width: padded.width, height: padded.height },
      });
      // Ink overlap is measured at the exact offset and at +/-2 px; the best value is
      // reported together with the offset it came from.
      let ink = null;
      let inkOffset = baseOffset;
      for (let dy = -2; dy <= 2; dy += 1) {
        for (let dx = -2; dx <= 2; dx += 1) {
          const candidateInk = await compareRegionInk({
            referencePath,
            framePath,
            referenceCrop: { x: padded.x, y: padded.y, width: padded.width, height: padded.height },
            testCrop: {
              x: padded.x + origin.x + dx,
              y: padded.y + origin.y + dy,
              width: padded.width,
              height: padded.height,
            },
          });
          if (candidateInk.ok && (ink === null || candidateInk.inkIoU > ink.inkIoU)) {
            ink = candidateInk;
            inkOffset = { x: dx, y: dy };
          }
        }
      }
      regions[name] = {
        selector,
        rect,
        psnrDb: round(result.psnrDb ?? -1),
        grayPsnrDb: ink?.grayPsnrDb ?? null,
        inkIoU: ink?.inkIoU ?? null,
        inkOffset,
      };
    }
    return {
      label,
      snapshotSeq: snapshot.seq,
      framePath,
      referencePath,
      contentOriginUsed: origin,
      contentPsnrDb: round(contentPsnr.psnrDb ?? -1),
      bestOffsetPsnr: { offset: best.offset, psnrDb: round(best.psnrDb ?? -1) },
      regionPsnrDb: regions,
    };
  }

  /**
   * PSNR of the streamed content area against Playwright's screenshot, searching a
   * small +/-2px window for the best alignment (the virtual-display candidate maps a
   * real window into the display, so sub-pixel window offsets are expected).
   */
  async function bestAlignmentPsnr(input) {
    let best = { offset: { x: 0, y: 0 }, psnrDb: null };
    const search = 3;
    for (let dy = -search; dy <= search; dy += 1) {
      for (let dx = -search; dx <= search; dx += 1) {
        const result = await measurePsnr({
          referencePath: input.referencePath,
          testPath: input.framePath,
          testCrop: {
            x: layout.contentOrigin.x + dx,
            y: layout.contentOrigin.y + dy,
            width: input.region.width,
            height: input.region.height,
          },
          referenceCrop: { x: 0, y: 0, width: input.region.width, height: input.region.height },
        });
        if (result.psnrDb !== null && (best.psnrDb === null || result.psnrDb > best.psnrDb)) {
          best = { offset: { x: dx, y: dy }, psnrDb: result.psnrDb };
        }
      }
    }
    return best;
  }

  const IDENTITY_TEXT_REGIONS = {
    nonce: "#page-nonce",
    url: "#url-display",
    fieldValue: "#field-value",
  };

  /**
   * Region over the agent marker, in frame coordinates.
   *
   * Page coordinates plus the content origin: frames of the virtual-display candidate
   * include the browser chrome above the page. The rectangle is clamped to the frame
   * actually being streamed, so a marker near the viewport edge cannot produce an
   * out-of-frame crop.
   *
   * @param {{ left: number, top: number, width: number, height: number }} rect
   */
  function registerMarkerRegion(rect) {
    const frame = viewer.lastFrame();
    const origin = layout.contentOrigin;
    const frameWidth = frame?.width ?? origin.x + viewport.width;
    const frameHeight = frame?.height ?? origin.y + viewport.height;
    const x = Math.max(0, Math.min(frameWidth - 8, Math.max(0, rect.left - 4) + origin.x));
    const y = Math.max(0, Math.min(frameHeight - 6, Math.max(0, rect.top - 2) + origin.y));
    const region = {
      x,
      y,
      width: Math.max(8, Math.min(rect.width + 8, frameWidth - x)),
      height: Math.max(6, Math.min(rect.height + 4, frameHeight - y)),
    };
    viewer.registerRegion("agent-marker", region);
    return { ...region, contentOrigin: origin, pageRect: rect, frameSize: { width: frameWidth, height: frameHeight } };
  }

  /** Highlight the marker element so the DOM change also changes pixels. */
  async function highlightMarker() {
    await control.evaluate(
      `(() => { const el = document.getElementById("agent-marker"); if (el) el.style.background = "#ffdd00"; return true; })()`,
    );
  }

  /** Steps are defined once and reused by measurement and endurance runs. */
  const steps = [
    {
      id: "connect",
      title: "Viewer connects and receives the shared page stream",
      critical: true,
      async run() {
        const hello = viewer.hello();
        const firstFrame = await viewer.waitForFirstFrame(10000);
        await loadLayout();
        const state = await pageState();
        return {
          ok: hello !== null,
          evidence: {
            hello: {
              candidate: hello.candidate,
              frameSize: hello.frameSize,
              pageOrigin: hello.pageOrigin,
              protocol: hello.protocol,
            },
            firstFrame: {
              seq: firstFrame.seq,
              bytes: firstFrame.bytes,
              snapshot: firstFrame.snapshot,
              width: firstFrame.width,
              height: firstFrame.height,
            },
            pageIdentity: {
              url: state.url,
              title: state.title,
              targetId: state.targetId,
              contextPageCount: state.contextPageCount,
              pageTargetCount: state.pageTargets?.length ?? null,
              iframes: state.fixture?.iframes,
              topLevel: state.fixture?.topLevel,
            },
          },
        };
      },
    },
    {
      id: "pointer-move-canvas",
      title: "Viewer pointer movement reaches the page (canvas hover feedback)",
      async run() {
        const rect = await prepare("#canvas");
        const before = (await pageState()).fixture.pointer;
        viewer.sendInput({ kind: "pointer", action: "move", x: rect.left + 40, y: rect.top + 40 });
        const state = await waitForFixture((snapshot) => snapshot.fixture.pointer !== before);
        const [gotX, gotY] = String(state.fixture.pointer).split(",").map(Number);
        const delta = Math.max(Math.abs(gotX - 40), Math.abs(gotY - 40));
        return {
          // A streamed view maps pixels through a display; +/-2 px is the documented
          // accuracy of the remote-view candidate's calibration.
          ok: Number.isFinite(delta) && delta <= 2,
          evidence: { before, after: state.fixture.pointer, expected: "40,40", deltaPx: delta },
        };
      },
    },
    {
      id: "pointer-click-probe",
      title: "Viewer click toggles the latency probe (pointer path)",
      async run() {
        const before = await pageState();
        const rect = await prepare("#probe-btn");
        const latency = await latencyProbe("pointer-click-probe", async () => {
          await clickViaViewer(rect.centerX, rect.centerY);
        });
        const after = await waitForFixture(
          (snapshot) => snapshot.fixture.probeState !== before.fixture.probeState,
        );
        return {
          ok: after.fixture.probeState !== before.fixture.probeState,
          evidence: {
            probeBefore: before.fixture.probeState,
            probeAfter: after.fixture.probeState,
            latency,
          },
        };
      },
    },
    {
      id: "viewer-typing",
      title: "Viewer typing reaches the form field",
      async run() {
        const text = "viewer typed 123";
        await clickSelector("#text-field");
        await sleep(120);
        viewer.sendInput({ kind: "text", text });
        const state = await waitForFixture((snapshot) => snapshot.fixture.fieldValue === text);
        return {
          ok: state.fixture.fieldValue === text,
          evidence: {
            typed: text,
            fieldValue: state.fixture.fieldValue,
            keyEvents: state.fixture.keyCount,
            lastKey: state.fixture.lastKey,
          },
        };
      },
    },
    {
      id: "viewer-submit",
      title: "Viewer submits the form and the same page shows the result",
      async run() {
        const before = await pageState();
        await clickSelector("#submit-btn");
        const state = await waitForFixture((snapshot) => snapshot.fixture.submitCount > before.fixture.submitCount);
        return {
          ok: state.fixture.submitStatus === "ok 200" && state.fixture.lastSubmitQ === "viewer typed 123",
          evidence: {
            submitStatus: state.fixture.submitStatus,
            submitCount: state.fixture.submitCount,
            lastSubmitted: state.fixture.lastSubmitQ,
            url: state.url,
          },
        };
      },
    },
    {
      id: "viewer-key-probe",
      title: "Viewer keystroke toggles the latency probe (keyboard path)",
      async run() {
        const before = await pageState();
        const latency = await latencyProbe("keyboard-key-probe", async () => {
          viewer.sendInput({ kind: "key", action: "down", key: "p", code: "KeyP", modifiers: {} });
          viewer.sendInput({ kind: "key", action: "up", key: "p", code: "KeyP", modifiers: {} });
        });
        const after = await waitForFixture((snapshot) => snapshot.fixture.probeState !== before.fixture.probeState);
        return {
          ok: after.fixture.probeState !== before.fixture.probeState,
          evidence: { latency, probeAfter: after.fixture.probeState },
        };
      },
    },
    {
      id: "viewer-shortcut",
      title: "Viewer keyboard shortcut reaches the page",
      async run() {
        const before = await pageState();
        viewer.sendInput({
          kind: "key",
          action: "down",
          key: "K",
          code: "KeyK",
          modifiers: { ctrl: true, shift: true },
        });
        viewer.sendInput({
          kind: "key",
          action: "up",
          key: "K",
          code: "KeyK",
          modifiers: { ctrl: true, shift: true },
        });
        const state = await waitForFixture(
          (snapshot) => snapshot.fixture.shortcutCount > before.fixture.shortcutCount,
        );
        return {
          ok: state.fixture.shortcutCount > before.fixture.shortcutCount,
          evidence: {
            shortcutBefore: before.fixture.shortcutCount,
            shortcutAfter: state.fixture.shortcutCount,
            lastKey: state.fixture.lastKey,
          },
        };
      },
    },
    {
      id: "viewer-canvas-drag",
      title: "Viewer drag paints on the page canvas",
      async run() {
        const rect = await prepare("#canvas");
        const before = await pageState();
        viewer.sendInput({ kind: "pointer", action: "move", x: rect.left + 40, y: rect.top + 60 });
        viewer.sendInput({ kind: "pointer", action: "down", x: rect.left + 40, y: rect.top + 60, button: "left", clickCount: 1 });
        for (const step of [60, 80, 100]) {
          viewer.sendInput({ kind: "pointer", action: "move", x: rect.left + step, y: rect.top + 80, clickCount: 1 });
          await sleep(40);
        }
        viewer.sendInput({ kind: "pointer", action: "up", x: rect.left + 100, y: rect.top + 80, button: "left", clickCount: 1 });
        const state = await waitForFixture(
          (snapshot) => snapshot.fixture.canvasCount >= before.fixture.canvasCount + 2,
        );
        return {
          ok: state.fixture.canvasCount >= before.fixture.canvasCount + 2,
          evidence: {
            canvasCountBefore: before.fixture.canvasCount,
            canvasCountAfter: state.fixture.canvasCount,
          },
        };
      },
    },
    {
      id: "viewer-scroll",
      title: "Viewer wheel scrolls inner content and the document",
      async run() {
        const before = await pageState();
        const area = await prepare("#scroll-area");
        viewer.sendInput({ kind: "wheel", x: area.centerX, y: area.centerY, deltaX: 0, deltaY: 240 });
        const innerState = await waitForFixture((snapshot) => snapshot.fixture.scrollAreaY > 0);
        const body = await prepare(".long-content");
        viewer.sendInput({ kind: "wheel", x: 400, y: body.centerY, deltaX: 0, deltaY: 320 });
        const state = await waitForFixture((snapshot) => snapshot.fixture.docScrollY > 0);
        return {
          ok: innerState.fixture.scrollAreaY > 0 && state.fixture.docScrollY > 0,
          evidence: {
            innerScrollBefore: before.fixture.scrollAreaY,
            innerScrollAfter: state.fixture.scrollAreaY,
            documentScrollAfter: state.fixture.docScrollY,
            scrollEvents: state.fixture.scrollEvents,
          },
        };
      },
    },
    {
      id: "viewer-internal-link",
      title: "Viewer click follows an internal link in the same tab and page",
      async run() {
        const before = await pageState();
        const targetId = before.targetId;
        await clickSelector("#internal-link");
        const state = await waitForFixture(
          (snapshot) => snapshot.url === layout.fixture.secondUrl,
          8000,
        );
        return {
          ok: state.url === layout.fixture.secondUrl && state.targetId === targetId && state.contextPageCount === 1,
          evidence: {
            urlBefore: before.url,
            urlAfter: state.url,
            targetIdBefore: targetId,
            targetIdAfter: state.targetId,
            generationBefore: before.fixture.generation,
            generationAfter: state.fixture.generation,
            contextPageCount: state.contextPageCount,
          },
        };
      },
    },
    {
      id: "viewer-back-to-index",
      title: "Viewer returns to the fixture index",
      async run() {
        await clickSelector("#back-link");
        const state = await waitForFixture((snapshot) => snapshot.url === layout.fixture.indexUrl, 8000);
        const firstFrame = await viewer.waitForFrame(() => true, 5000);
        return {
          ok: state.url === layout.fixture.indexUrl && state.contextPageCount === 1,
          evidence: { url: state.url, generation: state.fixture.generation, lastFrameSeq: firstFrame.seq },
        };
      },
    },
    {
      id: "viewer-external-link",
      title: "Viewer follows an external-style link on a second origin and returns",
      async run() {
        await clickSelector("#external-link");
        const state = await waitForFixture((snapshot) => snapshot.url === layout.fixture.thirdUrl, 8000);
        const crossOriginOk = state.url.startsWith(layout.fixture.externalUrl);
        await clickSelector("#back-to-index");
        const back = await waitForFixture((snapshot) => snapshot.url === layout.fixture.indexUrl, 8000);
        return {
          ok: crossOriginOk && back.url === layout.fixture.indexUrl,
          evidence: {
            crossOriginUrl: state.url,
            crossOriginOrigin: new URL(state.url).origin,
            mainOrigin: new URL(layout.fixture.indexUrl).origin,
            urlAfterReturn: back.url,
          },
        };
      },
    },
    {
      id: "viewer-failing-request",
      title: "Viewer triggers the deliberately failing request",
      async run() {
        const before = await pageState();
        await clickSelector("#fail-btn");
        const state = await waitForFixture(
          (snapshot) => snapshot.fixture.failCount > before.fixture.failCount,
        );
        const failure = await pageEvaluate(
          `(() => { const el = document.getElementById("fail-status"); return el ? el.textContent : null; })()`,
        );
        return {
          ok: /500/.test(String(failure ?? "")),
          evidence: {
            failStatus: failure,
            failCount: state.fixture.failCount,
            fixtureRequestStats: (await (await fetch(`${layout.fixture.baseUrl}/api/stats`)).json()).state,
          },
        };
      },
    },
    {
      id: "agent-action-visible",
      title: "Playwright acts on the page and the viewer sees it",
      async run() {
        const baseline = await viewer.currentMean();
        const before = await pageState();
        const startedAt = Date.now();
        await control.action("click", { selector: "#probe-btn" });
        const after = await pageState();
        const frame = await viewer.waitForMeanChange({
          baseline,
          threshold: PROBE_MEAN_DELTA,
          timeoutMs: 6000,
          sinceTs: startedAt,
        });
        return {
          ok: after.fixture.probeState !== before.fixture.probeState,
          evidence: {
            probeBefore: before.fixture.probeState,
            probeAfter: after.fixture.probeState,
            visibleAfterMs: frame.arrivalTs - startedAt,
            frameSeq: frame.seq,
          },
        };
      },
    },
    {
      id: "agent-marker-visible",
      title: "Playwright's DOM change appears in the streamed pixels",
      async run() {
        const token = `AGENT-MARKER-${Math.random().toString(36).slice(2, 10).toUpperCase()}`;
        const rect = await prepare("#agent-marker");
        const region = registerMarkerRegion(rect);
        await sleep(400);
        const regionBaseline = await viewer.currentRegionMean("agent-marker");
        const startedAt = Date.now();
        // Playwright writes text and highlights the element: both are DOM changes on the
        // shared page that a viewer can only see through the streamed pixels.
        await control.action("marker", { text: token });
        await highlightMarker();
        /** @type {{ seq: number, arrivalTs: number } | null} */
        let frame = null;
        let waitError = null;
        try {
          frame = await viewer.waitForRegionChange({
            regionName: "agent-marker",
            baseline: regionBaseline,
            threshold: 5,
            timeoutMs: 12000,
            sinceTs: startedAt,
          });
        } catch (error) {
          waitError = String(error);
        }
        // Bounded fallback: ask the transport for one fresh frame of the same page and
        // read the same region from it. A frame burst can be missed while the host is
        // loaded; that is a timing artefact of the stream, not an invisible DOM change,
        // and the fallback is recorded so the distinction stays visible in the evidence.
        let fallback = null;
        if (!frame) {
          const snapshot = await viewer.requestSnapshot();
          const mean = await viewer.regionMeanOf(snapshot, "agent-marker");
          const delta = round(meanDelta(mean, regionBaseline), 1);
          fallback = { seq: snapshot.seq, delta, mean: mean.map((value) => round(value, 1)), threshold: 5 };
          if (delta >= 5) frame = snapshot;
        }
        const state = await pageState();
        const domChanged = state.fixture.agentMarker === token;
        return {
          ok: domChanged && frame !== null,
          evidence: {
            token,
            markerInDom: state.fixture.agentMarker,
            domChanged,
            region,
            regionBaseline,
            visibleAfterMs: frame ? frame.arrivalTs - startedAt : null,
            frameSeq: frame?.seq ?? null,
            usedSnapshotFallback: fallback !== null && frame?.seq === fallback.seq,
            waitError,
            fallback,
          },
        };
      },
    },
    {
      id: "identity-agreement",
      title: "URL, title, nonce, and form values agree between the two paths",
      async run() {
        const agreement = await imageAgreement("identity-agreement");
        const state = await pageState();
        const expectedProbe = PROBE_COLORS[state.fixture.probeState];
        const backgroundMean = await viewer.currentRegionMean("probe-background");
        const backgroundDelta = backgroundMean
          ? Math.max(...expectedProbe.map((channel, index) => Math.abs(channel - backgroundMean[index])))
          : null;
        const ok =
          (agreement.contentPsnrDb ?? -1) >= 18 &&
          Object.values(agreement.regionPsnrDb).every(
            (region) => region.skipped !== undefined || (region.inkIoU ?? -1) >= 0.45,
          ) &&
          backgroundDelta !== null &&
          backgroundDelta <= 26;
        return {
          ok,
          evidence: {
            domPath: {
              url: state.url,
              title: state.title,
              nonce: state.fixture.nonce,
              fieldValue: state.fixture.fieldValue,
              submitCount: state.fixture.submitCount,
              probeState: state.fixture.probeState,
            },
            pixelPath: {
              screenshot: agreement.referencePath,
              frame: agreement.framePath,
              contentPsnrDb: agreement.contentPsnrDb,
              bestOffsetPsnr: agreement.bestOffsetPsnr,
              regionPsnrDb: agreement.regionPsnrDb,
              probeBackgroundMean: backgroundMean?.map((value) => round(value, 1)),
              expectedProbeColor: expectedProbe,
              probeBackgroundDelta: round(backgroundDelta ?? -1, 1),
            },
          },
        };
      },
    },
    {
      id: "viewer-disconnect-reconnect",
      title: "Viewer disconnects and reconnects with the page preserved",
      async run() {
        const before = await pageState();
        await viewer.close();
        await sleep(3000);
        const during = await pageState();
        const connectionInfo = await control.info();
        const reconnected = await viewer.connect({ timeoutMs: 8000 });
        const snapshot = await viewer.waitForFrame((frame) => frame.snapshot, 8000);
        const after = await pageState();
        const alive = during.fixture.heartbeat > before.fixture.heartbeat;
        const preserved =
          during.fixture.nonce === before.fixture.nonce &&
          after.fixture.nonce === before.fixture.nonce &&
          during.url === before.url &&
          after.url === before.url &&
          after.contextPageCount === 1 &&
          during.closed === false &&
          after.targetId === before.targetId &&
          during.fixture.heartbeat >= before.fixture.heartbeat;
        return {
          ok: alive && preserved && snapshot !== null,
          evidence: {
            heartbeatAtDisconnect: before.fixture.heartbeat,
            heartbeatAfterReconnect: after.fixture.heartbeat,
            heartbeatDuringGap: during.fixture.heartbeat,
            nonceBefore: before.fixture.nonce,
            nonceAfterReconnect: after.fixture.nonce,
            urlDuringGap: during.url,
            urlAfterReconnect: after.url,
            pageClosed: during.closed,
            targetIdStable: after.targetId === before.targetId,
            reconnectSnapshotSeq: snapshot.seq,
            reconnectedHelloFrameSize: reconnected.frameSize,
            viewerDisconnectedIndependentOfBrowser: connectionInfo !== null,
          },
        };
      },
    },
    {
      id: "idle",
      title: "Idle interval with the page untouched",
      phase: "idle",
      async run() {
        const before = await pageState();
        const framesBefore = viewer.frames.length;
        const statsBefore = viewer.stats();
        await sleep(10000);
        const after = await pageState();
        const statsAfter = viewer.stats();
        // Evidence for what, if anything, keeps changing while nobody acts: two
        // snapshots ~400 ms apart are decoded and compared pixel by pixel.
        let churn = null;
        try {
          const first = await viewer.requestSnapshot();
          const firstPixels = await viewer.pixelsOf(first);
          await sleep(400);
          const second = await viewer.requestSnapshot();
          const secondPixels = await viewer.pixelsOf(second);
          churn = {
            msApart: second.arrivalTs - first.arrivalTs,
            ...changedRegion(firstPixels, secondPixels, second.width, second.height, { step: 2, tolerance: 8 }),
          };
        } catch (error) {
          churn = { error: String(error) };
        }
        return {
          ok: after.fixture.heartbeat > before.fixture.heartbeat && after.fixture.nonce === before.fixture.nonce,
          evidence: {
            idleMs: 10000,
            heartbeatBefore: before.fixture.heartbeat,
            heartbeatAfter: after.fixture.heartbeat,
            framesDuringIdle: viewer.frames.length - framesBefore,
            bytesDuringIdle: statsAfter.bytes - statsBefore.bytes,
            nonceStable: after.fixture.nonce === before.fixture.nonce,
            idleChurn: churn,
          },
        };
      },
    },
  ];

  /**
   * Run steps in order. Each step records its own evidence; failures are collected
   * instead of aborting unless the step is marked critical.
   *
   * @param {{ stepIds?: string[], record: (name: string, value: unknown) => void }} input
   */
  async function run(input) {
    const wanted = input.stepIds ? new Set(input.stepIds) : null;
    const results = [];
    for (const step of steps) {
      if (wanted && !wanted.has(step.id)) continue;
      const startedAt = Date.now();
      let outcome;
      try {
        outcome = await step.run();
      } catch (error) {
        outcome = { ok: false, evidence: { error: String(error) } };
        if (step.critical && stopOnFailure) {
          results.push({ id: step.id, title: step.title, ok: false, ms: Date.now() - startedAt, evidence: outcome.evidence });
          input.record("step", results[results.length - 1]);
          throw error;
        }
      }
      const result = {
        id: step.id,
        title: step.title,
        phase: step.phase ?? "active",
        ok: outcome.ok === true,
        ms: Date.now() - startedAt,
        evidence: outcome.evidence,
      };
      log(`step ${step.id}: ${result.ok ? "ok" : "FAILED"} (${result.ms} ms)`);
      results.push(result);
      input.record("step", result);
    }
    return results;
  }

  return {
    version: "b0-script-1",
    steps: steps.map((step) => ({ id: step.id, title: step.title, phase: step.phase ?? "active" })),
    layout: () => layout,
    loadLayout,
    run,
    helpers: { rectOf, prepare, clickSelector, latencyProbe, waitForFixture, imageAgreement },
  };
}
