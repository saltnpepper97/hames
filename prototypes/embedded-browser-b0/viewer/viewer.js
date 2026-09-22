// Human-facing live view client.
//
// Deliberately plain: connect to the prototype WebSocket, paint JPEG frames into a
// canvas, forward pointer / wheel / keyboard input back to the same page.
//
// This file contains no iframe, no CDP, and no direct browser endpoint: the only
// thing it can reach is the loopback transport of the prototype.

(() => {
  const canvas = document.getElementById("view");
  const ctx = canvas.getContext("2d");
  const el = (id) => document.getElementById(id);

  /** @type {WebSocket | null} */
  let socket = null;
  let frameWidth = canvas.width;
  let frameHeight = canvas.height;
  let expectedBinary = false;
  let lastFrameAt = 0;
  let framesInWindow = 0;
  let windowStartedAt = Date.now();
  let bytesInWindow = 0;
  let painting = false;
  let dragging = false;
  let pageOrigin = { x: 0, y: 0 };
  let candidate = "-";

  const reconnectDelay = { current: 500 };

  /** Map canvas-relative pixel coordinates to frame (page) coordinates. */
  function toFrame(event) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = frameWidth / rect.width;
    const scaleY = frameHeight / rect.height;
    return {
      x: Math.round((event.clientX - rect.left) * scaleX),
      y: Math.round((event.clientY - rect.top) * scaleY),
    };
  }

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }

  function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = value === undefined || value === null ? "-" : String(value);
  }

  function handleText(raw) {
    const message = JSON.parse(raw);
    if (message.t === "hello") {
      candidate = message.candidate;
      setText("candidate", message.candidate);
      frameWidth = message.frameSize?.width ?? canvas.width;
      frameHeight = message.frameSize?.height ?? canvas.height;
      canvas.width = frameWidth;
      canvas.height = frameHeight;
      pageOrigin = message.pageOrigin ?? { x: 0, y: 0 };
      setText("frame-size", `${frameWidth}x${frameHeight}`);
      setText("page-origin", `${pageOrigin.x},${pageOrigin.y} (chrome ${message.chromeHeight ?? 0}px)`);
      setText("connection", "connected");
      notes(`attached to ${message.candidate}${message.display ? ` on ${message.display}` : ""}`);
      return;
    }
    if (message.t === "frame") {
      expectedBinary = true;
      return;
    }
    if (message.t === "state") {
      if (message.page?.url) setText("page-url", message.page.url);
      if (message.page?.title) setText("page-title", message.page.title);
      if (message.page?.fixture) {
        const fixture = message.page.fixture;
        setText("page-nonce", fixture.nonce);
        setText("page-heartbeat", `${fixture.heartbeat} (${fixture.viewport})`);
        setText("page-field", fixture.fieldValue);
        setText("page-probe", `${fixture.probeState.toUpperCase()} · submits ${fixture.submitCount} · keys ${fixture.keyCount}`);
      }
      return;
    }
    if (message.t === "pong") {
      notes(`transport round trip ${Date.now() - Number(message.echo)} ms`);
      return;
    }
    if (message.t === "note") {
      notes(message.text);
    }
  }

  function notes(text) {
    setText("notes", text);
  }

  async function paintFrame(buffer) {
    expectedBinary = false;
    bytesInWindow += buffer.byteLength;
    const started = performance.now();
    try {
      const bitmap = await createImageBitmap(new Blob([buffer], { type: "image/jpeg" }));
      if (bitmap.width !== canvas.width || bitmap.height !== canvas.height) {
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        frameWidth = bitmap.width;
        frameHeight = bitmap.height;
        setText("frame-size", `${frameWidth}x${frameHeight}`);
      }
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close?.();
      painting = true;
      lastFrameAt = Date.now();
      framesInWindow += 1;
      setText("paint", `${Math.round(performance.now() - started)} ms`);
    } catch (error) {
      notes(`frame decode failed: ${String(error)}`);
    }
  }

  function connect() {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    setText("connection", "connecting…");
    socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      reconnectDelay.current = 500;
      setText("connection", "connected");
      notes("viewer connected; the page keeps running if you close this tab");
    };
    socket.onclose = () => {
      setText("connection", `disconnected (retry in ${reconnectDelay.current} ms)`);
      const delay = reconnectDelay.current;
      reconnectDelay.current = Math.min(5000, delay * 2);
      setTimeout(connect, delay);
    };
    socket.onerror = () => notes("transport error");
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        handleText(event.data);
        return;
      }
      const buffer = event.data instanceof ArrayBuffer ? event.data : null;
      if (buffer && expectedBinary) paintFrame(buffer);
    };
  }

  // ---- input forwarding -----------------------------------------------------
  canvas.addEventListener("pointermove", (event) => {
    const point = toFrame(event);
    send({ t: "input", kind: "pointer", action: "move", x: point.x, y: point.y, clickCount: dragging ? 1 : 0 });
  });
  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus();
    const point = toFrame(event);
    dragging = true;
    send({ t: "input", kind: "pointer", action: "move", x: point.x, y: point.y });
    send({
      t: "input",
      kind: "pointer",
      action: "down",
      x: point.x,
      y: point.y,
      button: event.button === 2 ? "right" : event.button === 1 ? "middle" : "left",
      clickCount: 1,
    });
  });
  canvas.addEventListener("pointerup", (event) => {
    const point = toFrame(event);
    dragging = false;
    send({
      t: "input",
      kind: "pointer",
      action: "up",
      x: point.x,
      y: point.y,
      button: event.button === 2 ? "right" : event.button === 1 ? "middle" : "left",
      clickCount: 1,
    });
  });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const point = toFrame(event);
      send({ t: "input", kind: "wheel", x: point.x, y: point.y, deltaX: event.deltaX, deltaY: event.deltaY });
    },
    { passive: false },
  );

  const modifierState = (event) => ({
    alt: event.altKey,
    ctrl: event.ctrlKey,
    meta: event.metaKey,
    shift: event.shiftKey,
  });

  canvas.addEventListener("keydown", (event) => {
    // Nothing is swallowed silently: printable keys, shortcuts, and navigation keys
    // all go to the shared page while the view has focus.
    send({
      t: "input",
      kind: "key",
      action: "down",
      key: event.key,
      code: event.code,
      modifiers: modifierState(event),
    });
    if (event.key.length === 1 || event.key === "Enter" || event.key === "Tab" || event.key === "Backspace") {
      event.preventDefault();
    }
  });
  canvas.addEventListener("keyup", (event) => {
    send({
      t: "input",
      kind: "key",
      action: "up",
      key: event.key,
      code: event.code,
      modifiers: modifierState(event),
    });
  });

  // ---- controls -------------------------------------------------------------
  el("snapshot")?.addEventListener("click", () => send({ t: "snapshot", ts: Date.now() }));
  el("ping")?.addEventListener("click", () => send({ t: "ping", ts: Date.now() }));
  el("reconnect")?.addEventListener("click", () => {
    socket?.close();
    notes("viewer closed by hand; the browser page keeps running");
  });

  setInterval(() => {
    const seconds = (Date.now() - windowStartedAt) / 1000;
    setText("frames", `${framesInWindow} total`);
    setText("fps", seconds > 0 ? (framesInWindow / seconds).toFixed(1) : "0");
    setText("rate", seconds > 0 ? `${(bytesInWindow / 1024 / seconds).toFixed(0)} kB/s` : "0");
    setText("age", lastFrameAt ? `${Date.now() - lastFrameAt} ms` : "-");
    framesInWindow = 0;
    bytesInWindow = 0;
    windowStartedAt = Date.now();
  }, 1000);

  connect();
})();
