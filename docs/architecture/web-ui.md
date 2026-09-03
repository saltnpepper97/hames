# Web UI architecture

Hames Web is a local presentation layer over the existing gateway. It does not
own an agent loop, persistence model, provider integration, or policy engine.
`hames web` uses the same gateway startup and protocol checks as the TUI and
classic REPL, then runs a separate loopback-only HTTP server for the browser.

## Runtime boundary

The browser never receives the durable gateway bearer token. The Rust web server
reads that token locally and acts as a narrow same-origin proxy for `/v1/*`.
Responses remain streamed, so gateway Server-Sent Events are not buffered, and
`Last-Event-ID` is forwarded for durable reconnection. The web process stopping
does not stop the gateway or active work.

The launch flow is intentionally short-lived:

1. Rust binds `127.0.0.1:7500` by default, outside Hames search's dynamic port
   range. `--port 0` requests a free port.
2. It creates a random, single-use launch URL and opens it, or prints it with
   `--no-open`.
3. A valid launch exchanges the URL token for an HttpOnly, SameSite cookie and
   redirects to `/chat`.
4. The authenticated bootstrap endpoint returns only the launch working
   directory, protocol versions, and a process-local CSRF token.

Every request must carry the exact loopback Host selected at launch. Mutating
proxy requests must also carry the exact Origin and CSRF header. The server does
not enable CORS, forward browser authorization or cookie headers, or return
provider credentials. A restrictive Content Security Policy and defensive
headers cover static, API, proxy, and error responses.

## Frontend shape

The client is a SolidJS single-page application under `web/`. Its components and
CSS are built for Hames rather than sourced from a UI framework. The route shell
reserves stable top-level areas for Chat, Runs, Agents, Memory, Skills, Scars,
Plugins, and Settings.

The foundation reads only gateway health and session projections. It filters
sessions by exact canonical launch directory and renders explicit connecting,
reconnecting, offline, retry, and empty states. Routes without a gateway-backed
vertical slice state what is planned and expose no pretend controls.

## Build and packaging

Run the pinned frontend toolchain with:

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web check
pnpm --dir web test
pnpm --dir web build
```

Vite writes the production bundle to `crates/hames-repl/assets/web/`. Those
generated files are committed and embedded at Rust compile time, so release
installation and `hames web` require no Node process, package installation, CDN,
or network-hosted frontend asset. Rebuild and commit the bundle whenever web
source changes.

## Current capability boundary

This slice is a secure application shell, live runtime summary, and workspace
session list. Chat input, session mutations, transcript/event reconstruction,
approvals, and all management editors remain future gateway-backed slices. See
[M10 Web Control](../implementation-plan/M10-WEB-CONTROL.md) for their acceptance
criteria.
