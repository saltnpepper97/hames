# Embedded Browser — Milestone Plan

Status: proposed; no implementation milestones completed.

## Goal and product boundary

Let a user ask Hames to build and test a website, then watch and interact with the
same real browser the agent uses, inside a Browser tab in that chat. Hames remains
an agent harness. This feature adds a testing surface, not a workflow engine,
automation dashboard, or general-purpose replacement browser.

The initial target is a gateway-managed Chromium browser using Playwright, with
a live interactive view in the existing SolidJS chat frame. The first delivery
supports one browser session and one visible page per chat. The browser lives on
the gateway host, including when the Web client is on another device.

Everyday browser access remains a separate optional integration: the Playwright
extension can connect to a user's existing Chromium tabs. Embedded testing must
not silently attach to that browser, share its profile, or reconfigure the user's
personal MCP entry. Firefox support is outside the first delivery.

This is a feature track alongside the existing M00–M11 plan, not a renumbering or
claim that those milestones depend on this feature. Read
[AGENT-INSTRUCTIONS.md](AGENT-INSTRUCTIONS.md) when implementing; use the current
repository and runtime as authority where older roadmap descriptions are stale.

## Intended experience

1. The user asks: “Build this page and test it.”
2. The agent starts the project's development server through existing terminal
   tools and waits for evidence that it is ready.
3. The agent opens the resulting URL in the chat's browser. A Browser view tab
   becomes available without taking the user away from the transcript.
4. The user opens the tab and watches the actual page. The agent inspects page
   structure, acts on controls, and captures screenshots when appearance matters.
5. The user selects **Take control**, interacts directly, and selects **Let agent
   continue** when finished. Agent actions cannot race with manual input.
6. Switching view tabs, ending a model turn, or reconnecting Web preserves the
   browser. Explicit **Close browser** ends it. A crashed browser is reported as
   crashed, rather than silently replaced with a blank page.

## Architecture constraints

- The Python gateway owns browser identity, lifetime, authorization, and event
  provenance. Provider adapters and individual model turns do not own processes.
- Use a focused browser worker and existing gateway/plugin conventions where
  practical. Decide its packaging after the prototype; do not add a general
  orchestration framework or a speculative browser-provider abstraction.
- Playwright supplies semantic inspection and actions. The live view displays the
  same browser context/page, not a separately rendered iframe of the target URL.
- Use authenticated typed gateway commands and SSE for control state, following
  existing conventions. Select a separate bounded frame/input transport during
  the prototype; do not force continuous images into SSE or the event ledger.
- The browser view is a client of the gateway. No direct CDP, VNC, or browser-worker
  endpoint is exposed to Web clients or untrusted pages.
- Browser execution and streamed page content must be isolated from Hames's UI
  origin and credentials. A dedicated profile is separation of state, not an
  operating-system sandbox or a complete network security boundary.
- Browser launch, inspection, navigation, interaction, and shutdown failures have
  explicit states and useful errors. A successful MCP connection does not prove
  that a browser launched, remained alive, or appeared in the Web view.

Current implementation references to inspect before coding:

- [Web architecture](../architecture/web-ui.md), `web/src/chat/components/ChatFrame.tsx`,
  `ChatViewTabs.tsx`, `web/src/api/client.ts`, and `web/src/api/types.ts`.
- [MCP architecture](../architecture/mcp.md) and `src/hames/mcp_runtime.py` for
  transport ownership and bounded tool results. Existing external MCP policy must
  not be loosened globally to make this feature convenient.
- `src/hames/gateway.py`, existing session lifecycle and background-terminal APIs,
  [runtime policy](../architecture/runtime-policy.md), and
  [event ledger](../architecture/event-ledger.md).

## Completion and evidence rules

All boxes begin unchecked. Check an item only after its implementation and
verification are complete. Record commands, test results, relevant versions,
manual observations, and remaining limitations with each milestone's closure.
A source build, installed build, running gateway, and successful live interaction
are distinct evidence. Never restart a gateway with active work to validate this
feature. Use isolated HAMES_HOME and browser profiles for tests.

### B0 — Prove one shared browser and interactive view

Dependency: none. This is a bounded prototype, not production integration.

Status: pending. An isolated prototype exists at `prototypes/embedded-browser-b0/`.
Automated checks are recorded in
[EMBEDDED-BROWSER-B0-EVIDENCE.md](EMBEDDED-BROWSER-B0-EVIDENCE.md). These boxes stay
unchecked: a human has not operated the live view, and the ten-minute endurance
record is an automated frame-stream run that this review did not repeat.

- [ ] Create a disposable local test website with links, a form, scrolling,
  keyboard input, a canvas, and a deliberately failing request.
- [ ] Launch one dedicated Chromium context and identify its page from Playwright.
- [ ] Compare a Chromium frame-stream approach with a virtual-display/remote-view
  approach using the same fixture. Record dependency cost, supported interactions,
  image quality, idle/active CPU, bandwidth, input latency, and reconnect behavior.
- [ ] Choose one transport and document its limitations. A stream of page images
  does not automatically provide browser chrome, native dialogs, IME, or downloads.
- [ ] Render a live page view and forward pointer, keyboard, and scroll input.
- [ ] Show that manual typing is visible to Playwright and a Playwright action is
  visible in the view. Verify page identity, URL, and field values in both paths.
- [ ] Keep the page alive for at least ten minutes across several actions and an
  idle interval; confirm no disappearing window or silent reset to about:blank.
- [ ] Disconnect and reconnect the viewer while preserving the same page.
- [ ] Record a go/no-go decision and remove or clearly isolate prototype code.

Acceptance: a human can operate the test site through the view while the agent
adapter observes the same page. No production UI commitment until this passes.
If streaming/input is unreliable, stop and document the missing capability rather
than shipping an iframe as though it were an equivalent shared browser.

### B1 — Gateway ownership and browser lifecycle

Dependency: B0 transport decision.

- [ ] Define opaque browser-session and page identifiers bound to a Hames chat;
  keep readable titles and URLs separate from identity.
- [ ] Define typed states: absent, starting, ready, disconnected/recovering,
  failed, closing, and closed, with actionable failure reasons.
- [ ] Add idempotent create/status/close operations, bounded launch timeouts,
  cancellation, and process cleanup. Concurrent creates must not spawn duplicates.
- [ ] Give each chat its own profile directory; prevent profile locking collisions
  and accidental sharing with personal Chromium or external MCP browsers.
- [ ] Keep the browser alive across model turns and frontend connections.
  Closing a viewer or changing an agent/model must not close the browser.
- [ ] Specify gateway restart behavior: persist metadata, reconcile old workers,
  and report lost live state honestly. Do not promise restored forms or logins.
- [ ] Define cleanup on explicit close, chat deletion, gateway shutdown, and
  crashes. Document profile retention; first delivery should default to disposable
  browsing state after explicit close, with artifacts retained separately.
- [ ] Add an explicit configurable resource limit and visible behavior when it is
  reached. Do not silently evict a browser in use by a user or agent.
- [ ] Prove two chats cannot read, control, or close one another's browser.

Acceptance: lifecycle integration tests cover concurrent create, partial launch,
worker crash, shutdown, and cross-chat denial. A chat can finish a turn and later
resume using the same live page.

### B2 — Authenticated display and input transport

Dependencies: B0 and B1.

- [ ] Implement the selected transport with authentication, origin validation,
  session authorization, revocation, and short-lived connection credentials where
  required. Never put the gateway's long-lived token in a frame URL or logs.
- [ ] Keep browser control sockets private to the gateway/worker boundary.
- [ ] Bound frame size, frame rate, queued input, and client backpressure. Drop
  obsolete display frames rather than accumulating latency or memory.
- [ ] Pause or reduce streaming when the Browser view is hidden while preserving
  page execution and agent tools; resuming must show the current page.
- [ ] Distinguish a stale display from a live one. Show reconnecting/failed states
  and disable input until a valid connection/control grant is restored.
- [ ] Map input correctly through scaling, scrolling, device pixel ratio, and
  viewport changes. Reject input that targets an obsolete page or viewport.
- [ ] Specify text/IME behavior and keyboard shortcut handling. Keep Hames chat
  shortcuts from firing while the browser surface has input focus.
- [ ] Handle multiple viewers with one manual controller at a time; disconnecting
  a viewer must release its control grant without duplicating pending actions.
- [ ] Test local and remote-device access: target-site localhost always means the
  gateway host, not the phone/laptop displaying Hames.

Acceptance: reconnect, resize, slow connection, and unauthorized-connection tests
pass. Live typing, dragging, scrolling, and focus work in the supported browser
matrix. Unsupported interactions are documented before release.

### B3 — Browser tab in the existing chat frame

Dependencies: B1 and B2.

- [ ] Add Browser using the current chat view-tab and component/icon contracts.
  Do not add a new global navigation area or persistent side panel.
- [ ] Provide a discoverable Open browser action before a browser exists. Once
  opened, retain the tab for that chat until explicit closure; render recovery
  state there after a failure. Switching chats must never show the previous page.
- [ ] Include address, page title, back, forward, reload/stop loading, connection
  state, control ownership, maximize/restore, and explicit Close browser.
- [ ] Keep stop loading, cancel agent work, release manual control, and close
  browser distinct. Use clear labels/tooltips and suitable existing icon mappings.
- [ ] Do not auto-switch away from a transcript or steal focus when an agent opens
  a page. Indicate availability quietly in the Browser tab.
- [ ] Preserve the selected chat view across ordinary renders and reconnects;
  prevent unsolicited collapsing or changing panel size.
- [ ] Use a responsive full-area view on narrow screens. Test toolbar wrapping,
  touch scrolling, software keyboard, landscape, and maximize/restore.
- [ ] Support accessible toolbar controls, focus return, reduced motion, and an
  alternative text inspection surface where a streamed image is inaccessible.
- [ ] Keep loading/errors inside the browser surface, not sticky composer badges.
- [ ] Confirm potentially disruptive closure when an agent/browser operation is
  active; a routine view-tab change must never produce that confirmation.

Acceptance: inspect the rendered UI at desktop and mobile widths using actual
browser content. Verify three-pane layouts, long URLs/titles, keyboard focus, and
all error/empty/loading states. Build success alone does not close this milestone.

### B4 — Agent tools and manual takeover

Dependencies: B1–B3.

- [ ] Provide a compact tool surface for open/status, inspect, navigate, act,
  screenshot, console/request diagnostics, and close. Final names follow current
  Hames tool naming conventions; do not expose a large duplicate MCP tool set.
- [ ] Route every tool to the browser owned by the requesting chat. Delegated
  agents use an explicit parent-authorized browser reference, never implicit
  access to every session. Serialize competing worker actions.
- [ ] Prefer bounded structured page content with stable element references;
  invalidate stale references after navigation or relevant DOM changes.
- [ ] Keep screenshots opt-in for visual reasoning. Viewing the stream must not
  automatically send frames to the model or expand its context each turn.
- [ ] Integrate existing Plan/Manual/Auto policy. Distinguish inspection from
  actions; allow policy to authorize a bounded local-testing scope rather than
  repeatedly prompting for every click. No blanket bypass for external tools.
- [ ] Treat page text as untrusted tool data; it cannot grant permissions, change
  agent instructions, or authorize uploads, purchases, or filesystem access.
- [ ] Implement Take control as an exclusive input grant: stop scheduling agent
  actions, settle or cancel an in-flight action, then enable manual input.
- [ ] Implement Let agent continue with fresh page inspection. Do not replay
  clicks queued before takeover or automatically resubmit uncertain form actions.
- [ ] Define cancellation: stopping an agent stops its pending actions and child
  work, but leaves the browser available to the user. Explicit browser closure
  cancels browser operations and returns a clear tool error.
- [ ] Return successful results only after confirming the intended action/state;
  an open transport or blank tab is not proof of visible usable content.

Acceptance: deterministic agent fixtures complete a form, observe manual edits,
resume after takeover, and handle stale elements/cancellation without duplicates.
Measure context consumption for text inspection versus screenshot-based steps.

### B5 — Local development and testing workflow

Dependency: B4.

- [ ] Reuse existing terminal lifecycle APIs to start and track a development
  server. Record its process/terminal reference and confirmed URL; do not assume
  a fixed port or kill unrelated processes when a port is occupied.
- [ ] Wait for readiness with a bounded timeout and show useful startup logs on
  failure. Handle servers binding IPv4, IPv6, or a host inaccessible to the worker.
- [ ] Open the confirmed URL in the browser and exercise a full build/edit/reload
  loop. Hot reload should preserve the view and manual control semantics.
- [ ] Separate dev-server lifetime from browser lifetime. Closing a preview does
  not silently kill a user's server; provide explicit cleanup for Hames-owned work.
- [ ] Capture bounded console errors, failed requests, and screenshots as testing
  evidence linked to the originating chat/run and tested source revision when known.
- [ ] Add desktop/mobile viewport presets after resize behavior is proven; changing
  device size must change the actual page viewport, not only shrink its image.
- [ ] Define first-release popup, file-upload, download, clipboard, certificate,
  and modal-dialog behavior. Support or reject each explicitly without hanging.
  Keep downloads in a dedicated area and mediate uploads through existing policy.
- [ ] State that browser smoke tests complement project tests and do not establish
  full accessibility, cross-browser compatibility, or correctness by themselves.

Acceptance: from an isolated sample project, a fixture-driven agent starts the
server, finds a deliberate UI bug, changes it, reloads, and records passing
interaction and visual evidence. A human repeats the workflow in the real Web UI.

### B6 — Recovery, resource limits, and data handling

Dependencies: B1–B5; security boundaries also apply from the first prototype.

- [ ] Exercise worker exit, renderer crash, connection loss, gateway restart,
  missing browser binary, dependency failure, and profile corruption.
- [ ] Surface failures with retry/relaunch actions. Relaunch is explicitly a new
  live page/context; preserve failure evidence instead of pretending continuity.
- [ ] Test disconnect during navigation, form submission, takeover, and close.
  Uncertain side effects are reported and never automatically replayed.
- [ ] Verify process trees and temporary files are cleaned after repeated cycles;
  measure resource use with several chat browsers and a hidden view.
- [ ] Establish browser sandbox and network boundaries appropriate to the host.
  Test hostile target pages attempting to reach gateway control endpoints or
  private services; loopback targets needed for development require a deliberate
  policy, not an assumption that all localhost access is safe.
- [ ] Audit URL/query, page-content, console, screenshot, and form-value handling
  for credentials and private data. Do not promise complete screenshot redaction.
- [ ] Store only material lifecycle/action events and explicitly retained
  artifacts. Do not persist the live video stream or every manual keystroke.
- [ ] Document profile/artifact retention, deletion, backup, and export behavior.
- [ ] Verify disabled browser support adds no running browser, required download,
  or broken behavior to normal Web, TUI, and REPL use.

Acceptance: recovery and boundary tests pass without paid APIs; a manual fault
exercise demonstrates honest failure states, retained evidence, and clean recovery.

### B7 — Packaging, documentation, and release acceptance

Dependency: B6.

- [ ] Pin compatible browser/Playwright/worker versions and document dependency
  installation, disk use, updates, and supported Linux hosts.
- [ ] Make the feature opt-in and report missing dependencies with an actionable
  setup path. Do not silently download browsers during ordinary gateway startup.
- [ ] Document user actions: open a project preview, watch, take control, resume,
  stop loading, close, recover, and inspect retained testing evidence.
- [ ] Explain the difference between embedded testing and the optional extension
  for everyday tabs; keep the existing personal MCP configuration intact.
- [ ] Document architecture, protocol, policy, profile lifecycle, troubleshooting,
  and the tested browser/client support matrix; update the changelog on delivery.
- [ ] Run relevant Python, Web, and affected Rust checks plus release-required
  suites. Test fresh state and any schema upgrades with isolated HAMES_HOME.
- [ ] Install the candidate and verify served assets/runtime versions. Activate
  runtime changes only when gateway work is idle; report activation separately.
- [ ] Complete a human-observed desktop and remote/mobile smoke test of the entire
  intended experience, including a ten-minute idle interval and reconnection.
- [ ] Record unresolved limitations and the explicit release decision. Leave any
  unperformed live acceptance items unchecked.

Acceptance: a user can ask Hames to build and test a web page, watch and take over
in the same chat, resume agent work, and close/recover predictably. No default
agent changes, Flows, Automation tab, or everyday-browser migration are required.

## Deferred until the first version proves useful

Multiple browser tabs/windows per chat; shared browsers across unrelated chats;
saved login profiles; session recording and replay; a DevTools workbench; native
browser-menu automation; Firefox/WebKit; audio/video streaming; and hosted browser
pools. Add these only in response to demonstrated needs, with separate milestones.

## Suggested implementation slices

B0 is the feasibility gate. B1–B3 deliver a usable manually controlled preview.
B4–B5 make it an agent testing surface. B6–B7 make it releasable. Boundary tests,
error handling, and documentation accompany each slice; they are not postponed to
hardening. Avoid presenting an early prototype as a completed browser feature.
