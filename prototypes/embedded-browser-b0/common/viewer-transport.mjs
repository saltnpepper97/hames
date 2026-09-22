// Loopback-only viewer transport shared by both candidates.
//
// The transport is deliberately identical for both candidates: an HTTP server that
// serves the viewer page plus a WebSocket carrying
//   * JSON control messages from the viewer (input, ping, snapshot)
//   * a JSON frame header followed by one binary JPEG frame per update
//
// So a measurement difference between candidates comes from the capture/input
// mechanism, not from a different wire protocol.
//
// Prototype-only: no authentication, bound to 127.0.0.1, no CDP/VNC passthrough.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { WebSocketServer } from "ws";

/**
 * @param {{ host?: string, port?: number, viewerDir: string, hello: () => Record<string, unknown> }} options
 */
export async function createViewerTransport(options) {
  const { host = "127.0.0.1", port = 0, viewerDir, hello } = options;
  const assets = new Map([
    ["/", join(viewerDir, "viewer.html")],
    ["/viewer.html", join(viewerDir, "viewer.html")],
    ["/viewer.js", join(viewerDir, "viewer.js")],
    ["/viewer.css", join(viewerDir, "viewer.css")],
  ]);

  const server = createServer(async (req, res) => {
    const path = (req.url ?? "/").split("?")[0];
    if (path === "/healthz") {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end("ok");
      return;
    }
    const file = assets.get(path ?? "/");
    if (!file) {
      res.writeHead(404, { "content-type": "text/plain" });
      res.end("not found");
      return;
    }
    const body = await readFile(file);
    const contentType = file.endsWith(".html")
      ? "text/html; charset=utf-8"
      : file.endsWith(".css")
        ? "text/css; charset=utf-8"
        : "text/javascript; charset=utf-8";
    res.writeHead(200, {
      "content-type": contentType,
      "cache-control": "no-store",
      "content-length": body.byteLength,
    });
    res.end(body);
  });

  const wss = new WebSocketServer({ server, path: "/ws" });
  /** @type {Set<import("ws").WebSocket>} */
  const clients = new Set();
  /** @type {Array<(message: Record<string, unknown>) => void>} */
  const inputHandlers = [];
  /** @type {Array<(client: import("ws").WebSocket) => void>} */
  const connectHandlers = [];
  /** @type {Array<(remaining: number) => void>} */
  const disconnectHandlers = [];

  const stats = {
    framesSent: 0,
    frameBytes: 0,
    stateMessages: 0,
    connects: 0,
    disconnects: 0,
    inputs: 0,
    lastFrameTs: null,
    startedAt: Date.now(),
  };

  wss.on("connection", (socket) => {
    clients.add(socket);
    stats.connects += 1;
    socket.send(JSON.stringify({ t: "hello", ts: Date.now(), ...hello() }));
    for (const handler of connectHandlers) handler(socket);

    socket.on("message", (data, isBinary) => {
      if (isBinary) return;
      /** @type {Record<string, unknown>} */
      let message;
      try {
        message = JSON.parse(data.toString("utf8"));
      } catch {
        return;
      }
      if (message.t === "ping") {
        socket.send(JSON.stringify({ t: "pong", ts: Date.now(), echo: message.ts ?? null }));
        return;
      }
      stats.inputs += 1;
      for (const handler of inputHandlers) handler(message);
    });

    socket.on("close", () => {
      clients.delete(socket);
      stats.disconnects += 1;
      for (const handler of disconnectHandlers) handler(clients.size);
    });
  });

  await new Promise((resolve) => server.listen(port, host, resolve));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("viewer transport failed to bind");
  const boundPort = address.port;

  return {
    host,
    port: boundPort,
    url: `http://${host}:${boundPort}/viewer.html`,
    wsUrl: `ws://${host}:${boundPort}/ws`,
    server,
    stats,
    clientCount: () => clients.size,
    /** @param {(message: Record<string, unknown>) => void} handler */
    onInput(handler) {
      inputHandlers.push(handler);
    },
    /** @param {(client: import("ws").WebSocket) => void} handler */
    onConnect(handler) {
      connectHandlers.push(handler);
    },
    /** @param {(remaining: number) => void} handler */
    onDisconnect(handler) {
      disconnectHandlers.push(handler);
    },
    /**
     * @param {Buffer} jpeg
     * @param {{ seq: number, width: number, height: number, ts: number, snapshot?: boolean, note?: string }} info
     */
    sendFrame(jpeg, info) {
      if (clients.size === 0) return false;
      const header = JSON.stringify({
        t: "frame",
        seq: info.seq,
        width: info.width,
        height: info.height,
        ts: info.ts,
        snapshot: info.snapshot === true,
        note: info.note ?? null,
        bytes: jpeg.byteLength,
      });
      for (const client of clients) {
        if (client.readyState !== client.OPEN) continue;
        client.send(header);
        client.send(jpeg, { binary: true });
      }
      stats.framesSent += 1;
      stats.frameBytes += jpeg.byteLength;
      stats.lastFrameTs = info.ts;
      return true;
    },
    /** @param {Record<string, unknown>} state */
    sendState(state) {
      if (clients.size === 0) return;
      const message = JSON.stringify({ t: "state", ts: Date.now(), ...state });
      for (const client of clients) {
        if (client.readyState === client.OPEN) client.send(message);
      }
      stats.stateMessages += 1;
    },
    /** @param {string} text */
    sendNote(text) {
      if (clients.size === 0) return;
      const message = JSON.stringify({ t: "note", ts: Date.now(), text });
      for (const client of clients) {
        if (client.readyState === client.OPEN) client.send(message);
      }
    },
    async close() {
      for (const client of clients) client.terminate();
      await new Promise((resolve) => wss.close(resolve));
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
