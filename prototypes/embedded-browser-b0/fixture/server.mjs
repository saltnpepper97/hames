// Deterministic local fixture site for the B0 shared-browser prototype.
//
// Two plain Node HTTP servers on loopback ephemeral ports (no dependencies):
//   * main     - the fixture itself plus its JSON endpoints
//   * external - a second origin used for "external-style" link navigation
//
// Nothing here is production code; B0 must not touch Hames modules.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = join(HERE, "public");

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

const STATIC_FILES = new Set([
  "index.html",
  "second.html",
  "third.html",
  "fixture.js",
  "style.css",
]);

function send(res, status, body, type = "text/plain; charset=utf-8") {
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function sendJson(res, status, value) {
  send(res, status, JSON.stringify(value), CONTENT_TYPES[".json"]);
}

/**
 * @param {import("node:http").IncomingMessage} req
 * @returns {Promise<{method: string, path: string, status: number, query: string}>}
 */
export function createRequestLog() {
  /** @type {Array<{method: string, path: string, status: number, ts: number}>} */
  const entries = [];
  return {
    entries,
    record(method, path, status) {
      entries.push({ method, path, status, ts: Date.now() });
    },
    counts() {
      /** @type {Record<string, number>} */
      const out = {};
      for (const entry of entries) {
        const key = `${entry.method} ${entry.path}`;
        out[key] = (out[key] ?? 0) + 1;
      }
      return out;
    },
  };
}

function makeStaticHandler(log, { allowAll, htmlSubstitutions }) {
  return async (req, res, url) => {
    const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    if (!STATIC_FILES.has(name) || (!allowAll && name === "third.html")) {
      log.record(req.method, url.pathname, 404);
      send(res, 404, "not found");
      return;
    }
    try {
      let body = await readFile(join(PUBLIC, name));
      if (name.endsWith(".html")) {
        let text = body.toString("utf8");
        for (const [placeholder, value] of Object.entries(htmlSubstitutions())) {
          text = text.split(placeholder).join(value);
        }
        body = Buffer.from(text, "utf8");
      }
      log.record(req.method, url.pathname, 200);
      res.writeHead(200, {
        "content-type": CONTENT_TYPES[name.slice(name.lastIndexOf("."))] ?? "application/octet-stream",
        "cache-control": "no-store",
        "content-length": body.byteLength,
      });
      res.end(body);
    } catch {
      log.record(req.method, url.pathname, 500);
      send(res, 500, "read error");
    }
  };
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Starts the fixture. Returns handles with resolved loopback ports.
 *
 * @param {{ host?: string }} [options]
 */
export async function startFixture(options = {}) {
  const host = options.host ?? "127.0.0.1";
  const mainLog = createRequestLog();
  const externalLog = createRequestLog();
  const state = {
    submitCount: 0,
    failCount: 0,
    failOnceCount: 0,
    lastSubmitQ: null,
    lastSubmitAt: null,
  };

  // Filled in once both ports are known; HTML responses substitute these so that
  // links cross the two loopback origins deterministically.
  const substitutions = { externalOrigin: "about:invalid", mainOrigin: "about:invalid" };
  substitutions.externalPage = () => ({
    __EXTERNAL_ORIGIN__: substitutions.externalOrigin,
    __MAIN_ORIGIN__: substitutions.mainOrigin,
  });
  const serveStatic = makeStaticHandler(mainLog, {
    allowAll: false,
    htmlSubstitutions: () => substitutions.externalPage(),
  });
  const main = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${host}`);
    if (url.pathname === "/api/submit") {
      const raw = await readBody(req);
      /** @type {{q?: string}} */
      let parsed = {};
      try {
        parsed = JSON.parse(raw || "{}");
      } catch {
        parsed = { q: raw };
      }
      state.submitCount += 1;
      state.lastSubmitQ = parsed.q ?? "";
      state.lastSubmitAt = Date.now();
      mainLog.record(req.method, url.pathname, 200);
      sendJson(res, 200, { ok: true, q: state.lastSubmitQ, submitCount: state.submitCount });
      return;
    }
    if (url.pathname === "/api/fail") {
      state.failCount += 1;
      mainLog.record(req.method, url.pathname, 500);
      sendJson(res, 500, { error: "deliberate-failure", endpoint: "/api/fail" });
      return;
    }
    if (url.pathname === "/api/fail-once") {
      state.failOnceCount += 1;
      mainLog.record(req.method, url.pathname, 500);
      sendJson(res, 500, { error: "deliberate-failure", endpoint: "/api/fail-once" });
      return;
    }
    if (url.pathname === "/api/stats") {
      mainLog.record(req.method, url.pathname, 200);
      sendJson(res, 200, {
        state,
        counts: mainLog.counts(),
        externalCounts: externalLog.counts(),
        requestCount: mainLog.entries.length,
        externalRequestCount: externalLog.entries.length,
      });
      return;
    }
    if (url.pathname === "/healthz") {
      send(res, 200, "ok");
      return;
    }
    await serveStatic(req, res, url);
  });

  const external = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${host}`);
    externalLog.record(req.method, url.pathname, 200);
    if (url.pathname === "/" || url.pathname === "/third.html") {
      let body = await readFile(join(PUBLIC, "third.html"), "utf8");
      for (const [placeholder, value] of Object.entries(substitutions.externalPage())) {
        body = body.split(placeholder).join(value);
      }
      const payload = Buffer.from(body, "utf8");
      res.writeHead(200, {
        "content-type": CONTENT_TYPES[".html"],
        "cache-control": "no-store",
        "content-length": payload.byteLength,
      });
      res.end(payload);
      return;
    }
    if (url.pathname === "/style.css") {
      const body = await readFile(join(PUBLIC, "style.css"));
      res.writeHead(200, {
        "content-type": CONTENT_TYPES[".css"],
        "cache-control": "no-store",
        "content-length": body.byteLength,
      });
      res.end(body);
      return;
    }
    send(res, 404, "not found");
  });

  const listen = (server) =>
    new Promise((resolve) => {
      server.listen(0, host, () => {
        const address = server.address();
        if (address === null || typeof address === "string") {
          throw new Error("fixture server did not bind a TCP port");
        }
        resolve(address.port);
      });
    });

  const [mainPort, externalPort] = await Promise.all([listen(main), listen(external)]);
  const baseUrl = `http://${host}:${mainPort}`;
  const externalUrl = `http://${host}:${externalPort}`;
  substitutions.externalOrigin = externalUrl;
  substitutions.mainOrigin = baseUrl;

  return {
    host,
    mainPort,
    externalPort,
    baseUrl,
    externalUrl,
    indexUrl: `${baseUrl}/index.html`,
    secondUrl: `${baseUrl}/second.html`,
    thirdUrl: `${externalUrl}/third.html`,
    async stats() {
      const response = await fetch(`${baseUrl}/api/stats`);
      return await response.json();
    },
    async stop() {
      await Promise.all([
        new Promise((resolve) => main.close(resolve)),
        new Promise((resolve) => external.close(resolve)),
      ]);
    },
  };
}
