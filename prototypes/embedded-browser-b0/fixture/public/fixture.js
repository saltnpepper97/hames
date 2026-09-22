// Fixture behavior. Deterministic, dependency-free, loopback-only.
//
// Shared by index.html, second.html, and third.html. Every element lookup is
// guarded so a page that omits a widget still exposes the identity contract:
//   window.__FIXTURE_STATE__() -> { nonce, generation, heartbeat, ... }

(() => {
  /** @param {string} id */
  const el = (id) => document.getElementById(id);

  /** @param {string} id @param {string} text */
  const setText = (id, text) => {
    const node = el(id);
    if (node) node.textContent = text;
  };

  /**
   * @param {string} id
   * @param {string} type
   * @param {(event: Event) => void} handler
   * @param {AddEventListenerOptions} [options]
   */
  const on = (id, type, handler, options) => {
    const node = el(id);
    if (node) node.addEventListener(type, handler, options);
  };

  const GENERATION_KEY = "hames-b0-generation";
  let generation = 0;
  try {
    generation = Number.parseInt(sessionStorage.getItem(GENERATION_KEY) ?? "0", 10) || 0;
    generation += 1;
    sessionStorage.setItem(GENERATION_KEY, String(generation));
  } catch {
    generation = -1;
  }

  const state = {
    nonce: crypto.randomUUID(),
    sessionId: new URL(location.href).searchParams.get("session") ?? "(none)",
    generation,
    loadTs: Date.now(),
    heartbeat: 0,
    heartbeatAt: Date.now(),
    probeState: "a",
    probeCount: 0,
    fieldValue: "",
    submitCount: 0,
    lastSubmitQ: "(none)",
    submitStatus: "idle",
    keyCount: 0,
    lastKey: "(none)",
    shortcutCount: 0,
    canvasCount: 0,
    pointer: "-",
    scrollAreaY: 0,
    docScrollY: 0,
    scrollEvents: 0,
    failCount: 0,
    failStatus: "pending",
  };

  window.__FIXTURE_STATE__ = () => ({
    ...state,
    url: location.href,
    title: document.title,
    viewport: `${innerWidth}x${innerHeight}`,
    devicePixelRatio,
    iframes: document.querySelectorAll("iframe").length,
    topLevel: window.self === window.top,
    agentMarker: el("agent-marker")?.textContent ?? "(no marker element)",
    heartbeatAt: state.heartbeatAt,
    now: Date.now(),
  });

  const renderIdentity = () => {
    setText("page-nonce", state.nonce);
    setText("url-display", location.href);
    setText("title-display", document.title);
    setText("session-id", state.sessionId);
    setText("generation", String(state.generation));
    setText("viewport-size", `${innerWidth}x${innerHeight}`);
    setText("heartbeat", String(state.heartbeat));
    setText("uptime", String(Math.round((Date.now() - state.loadTs) / 1000)));
    setText("load-ts", new Date(state.loadTs).toISOString());
  };

  const renderInteractive = () => {
    setText("probe-state", state.probeState.toUpperCase());
    setText("probe-count", String(state.probeCount));
    setText("field-value", state.fieldValue === "" ? "(empty)" : state.fieldValue);
    setText("submit-count", String(state.submitCount));
    setText("last-submit-q", state.lastSubmitQ);
    setText("submit-status", state.submitStatus);
    setText("key-count", String(state.keyCount));
    setText("last-key", state.lastKey);
    setText("shortcut-count", String(state.shortcutCount));
    setText("canvas-count", String(state.canvasCount));
    setText("canvas-pointer", state.pointer);
    setText("scroll-area-y", String(state.scrollAreaY));
    setText("doc-scroll-y", String(state.docScrollY));
    setText("scroll-events", String(state.scrollEvents));
    setText("fail-status", state.failStatus);
    setText("fail-count", String(state.failCount));
    document.body.classList.toggle("probe-a", state.probeState === "a");
    document.body.classList.toggle("probe-b", state.probeState === "b");
  };

  // ---- latency probe -------------------------------------------------------
  const toggleProbe = () => {
    state.probeState = state.probeState === "a" ? "b" : "a";
    state.probeCount += 1;
    renderInteractive();
  };
  on("probe-btn", "click", toggleProbe);

  // ---- keyboard ------------------------------------------------------------
  window.addEventListener("keydown", (event) => {
    state.keyCount += 1;
    const mods = [
      event.ctrlKey ? "Ctrl" : null,
      event.altKey ? "Alt" : null,
      event.metaKey ? "Meta" : null,
      event.shiftKey ? "Shift" : null,
    ].filter(Boolean);
    state.lastKey = [...mods, event.key].join("+");
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "k") {
      state.shortcutCount += 1;
    }
    const plainProbe = !event.ctrlKey && !event.altKey && !event.metaKey && event.key === "p";
    const inField = event.target instanceof HTMLInputElement;
    if (plainProbe && !inField) toggleProbe();
    renderInteractive();
  });

  // ---- form ----------------------------------------------------------------
  const field = el("text-field");
  if (field instanceof HTMLInputElement) {
    field.addEventListener("input", () => {
      state.fieldValue = field.value;
      renderInteractive();
    });
  }

  const form = el("form");
  if (form instanceof HTMLFormElement) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = field instanceof HTMLInputElement ? field.value : "";
      try {
        const response = await fetch("/api/submit", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ q: value }),
        });
        const payload = await response.json();
        state.submitCount = payload.submitCount ?? state.submitCount + 1;
        state.lastSubmitQ = payload.q ?? value;
        state.submitStatus = response.ok ? "ok 200" : `error ${response.status}`;
      } catch (error) {
        state.submitStatus = `network error: ${String(error)}`;
      }
      renderInteractive();
    });
  }

  // ---- canvas --------------------------------------------------------------
  const canvas = el("canvas");
  const ctx = canvas instanceof HTMLCanvasElement ? canvas.getContext("2d") : null;
  if (canvas instanceof HTMLCanvasElement && ctx) {
    const width = canvas.width;
    const height = canvas.height;
    const drawBackground = () => {
      ctx.fillStyle = "#101014";
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = "#242433";
      ctx.lineWidth = 1;
      for (let x = 0; x <= width; x += 40) {
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, height);
        ctx.stroke();
      }
      for (let y = 0; y <= height; y += 40) {
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(width, y + 0.5);
        ctx.stroke();
      }
    };
    drawBackground();

    const canvasPoint = (event) => {
      const rect = canvas.getBoundingClientRect();
      return { x: Math.round(event.clientX - rect.left), y: Math.round(event.clientY - rect.top) };
    };
    const paint = (point) => {
      ctx.fillStyle = "#f5d90a";
      ctx.beginPath();
      ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
      ctx.fill();
      state.canvasCount += 1;
      state.pointer = `${point.x},${point.y}`;
      renderInteractive();
    };

    let dragging = false;
    canvas.addEventListener("pointermove", (event) => {
      const point = canvasPoint(event);
      state.pointer = `${point.x},${point.y}`;
      if (dragging) {
        paint(point);
      } else {
        drawBackground();
        ctx.strokeStyle = "#4ec9b0";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(point.x - 8, point.y);
        ctx.lineTo(point.x + 8, point.y);
        ctx.moveTo(point.x, point.y - 8);
        ctx.lineTo(point.x, point.y + 8);
        ctx.stroke();
      }
      renderInteractive();
    });
    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      dragging = true;
      paint(canvasPoint(event));
    });
    window.addEventListener("pointerup", (event) => {
      if (event.button === 0) dragging = false;
    });
  }

  // ---- scrolling -----------------------------------------------------------
  const items = el("scroll-items");
  if (items) {
    for (let index = 1; index <= 60; index += 1) {
      const item = document.createElement("li");
      item.textContent = `fixture row ${String(index).padStart(2, "0")} — deterministic content`;
      items.appendChild(item);
    }
  }
  const onScroll = (event) => {
    state.scrollEvents += 1;
    if (event.target === document) state.docScrollY = Math.round(window.scrollY);
    const area = el("scroll-area");
    if (area) state.scrollAreaY = Math.round(area.scrollTop);
    renderInteractive();
  };
  on("scroll-area", "scroll", onScroll);
  document.addEventListener("scroll", onScroll, { passive: true });

  // ---- deliberate failures -------------------------------------------------
  on("fail-btn", "click", async () => {
    try {
      const response = await fetch("/api/fail");
      state.failStatus = `manual request: HTTP ${response.status} (deliberate failure)`;
    } catch (error) {
      state.failStatus = `manual request failed: ${String(error)}`;
    }
    state.failCount += 1;
    renderInteractive();
  });

  if (el("fail-status")) {
    (async () => {
      try {
        const response = await fetch("/api/fail-once");
        state.failCount += 1;
        state.failStatus = `on load: HTTP ${response.status} (deliberate failure)`;
      } catch (error) {
        state.failStatus = `on load network error: ${String(error)}`;
      }
      renderInteractive();
    })();
  }

  // ---- heartbeat -----------------------------------------------------------
  setInterval(() => {
    state.heartbeat += 1;
    state.heartbeatAt = Date.now();
    renderIdentity();
  }, 1000);

  // The fixture clock ticks once per second on purpose: it keeps the idle comparison
  // about the transport, not about how fast the fixture churns pixels.
  setInterval(() => {
    setText("heartbeat-age", String(Date.now() - state.heartbeatAt));
    setText("uptime", String(Math.round((Date.now() - state.loadTs) / 1000)));
  }, 1000);

  window.addEventListener("resize", renderIdentity);
  renderIdentity();
  renderInteractive();

  // The fixture must be a single top-level document; an iframe here would make the
  // "one shared page" claim false, so say so loudly rather than silently.
  if (document.querySelectorAll("iframe").length > 0) {
    setText("agent-marker", "ERROR: fixture unexpectedly contains an iframe");
  }
})();
