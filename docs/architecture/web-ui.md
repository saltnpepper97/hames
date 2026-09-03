# Web UI architecture

Hames Web is a local presentation layer owned by the persistent gateway. It does
not own an agent loop, persistence model, provider integration, or policy
engine. `hames web` uses the same gateway startup and protocol checks as the TUI
and classic REPL, requests a one-time launch URL, opens it, and exits.

## Runtime boundary

The browser never receives the durable gateway bearer token. The gateway accepts
either that token from trusted local clients or a process-local browser session
cookie. The browser and API share one origin, so gateway Server-Sent Events keep
their native streaming and `Last-Event-ID` behavior without another proxy or
listener. Closing the terminal that invoked `hames web` has no effect on the
site, gateway, or active work.

The launch flow is intentionally short-lived:

1. The gateway serves the API and web application from its configured loopback
   origin, `127.0.0.1:7411` by default.
2. Rust authenticates to the gateway, requests a random single-use launch URL
   for the current directory, and opens it or prints it with `--no-open`.
3. A valid launch exchanges the URL token for an HttpOnly, SameSite cookie and
   redirects to `/chat`.
4. The authenticated bootstrap endpoint returns only the launch working
   directory, protocol versions, and a process-local CSRF token.

Every browser-authenticated request must carry the gateway's exact loopback Host.
Mutations must also carry the exact Origin and CSRF header. The server does not
enable CORS or return provider credentials. A restrictive Content Security
Policy and defensive headers cover the application, bootstrap, and web errors.

## Frontend shape

The client is a SolidJS single-page application under `web/`. Its components and
CSS are built for Hames rather than sourced from a UI framework. The shell owns
only authentication, gateway connectivity, layout regions, routing,
accessibility primitives, and typed contribution registries. Product surfaces
are first-party web plugins using the same contracts available to later optional
plugins.

Shared controls are application components rather than incidental page markup.
Buttons, checkboxes, switches, form fields, Markdown renderers and editors,
selectable capability rows, settings sections, dialogs, the chat frame, and
agent avatars each own their interaction and accessibility contract. Pages
compose those primitives and do not render native buttons directly.

The icon-pack contract lets application components request semantic names such
as `nav.chat` or `state.empty`; the selected pack maps those names to assets.
The default pack uses the Hames artwork for its brand mark, Phosphor for agent
and memory symbols, and Tabler for the rest of the interface. Product
components do not import either library directly. The surface registry composes
route, icon-rail, and context-sidebar contributions from web plugins. The
built-in areas are the first `hames.core` plugin rather than hard-coded shell navigation.
Conversation messages, reasoning, tool activity, and notices use the same
registry for their renderers, keeping the transcript itself composable.
The resident chat frame is assembled from focused header, viewport, composer,
menu, and contribution-seat components. Web plugins can add ordered controls to
the composer's typed left and right seats without reaching into its markup or
owning draft submission. The core plugin currently contributes the disabled
attachment affordance, gateway-backed interaction mode, and a unified model and
thinking selector. Beneath the fixed header, the transcript scroll viewport
fills the remaining chat pane through its bottom edge, so its scrollbar does not
stop above the composer. The composer is anchored over that viewport and its
measured height becomes transcript bottom clearance, keeping messages out from
underneath the input. The composer retains send, queue, and cancel because those
actions belong to its input state machine. This follows the useful
contribution-seat shape of the DeepSeek Harness reference while keeping Hames's
SolidJS and HTTP/SSE runtime boundary.

One composer button displays the current model and thinking level. Its root menu
contains Model and Thinking rows which drill into provider-grouped models or the
current model's supported effort levels, following the useful DeepSeek Harness
interaction instead of presenting separate toolbar buttons. Provider probes run
concurrently and remain available during active runs. Selecting a
reasoning-capable model advances to the effort pane and commits provider, model,
and effort together only after explicit confirmation. Models without advertised
reasoning support commit with reasoning off; reasoning models without graduated
levels offer the explicit on/off choice used by the TUI.

On wide screens, the shell uses a narrow global activity rail, a contextual
sidebar, and the active surface. Chat contributes real, open workspace sessions
to the contextual sidebar, matching the TUI's resumable-history boundary;
empty sessions, closed historical sessions, and sessions from other workspaces
remain out of the list. Selecting one routes its gateway metadata into the main
surface. Agents contributes a component-rendered capsule directory to the same
sidebar region; selecting an avatar opens its editor directly in the main
surface, and the bare Agents route selects the first real capsule. The
contextual sidebar names the active surface without repeating the repository
name. Workspace identity remains in the Chat surface bar, where it is relevant
to session scope, and is omitted from management surfaces. Areas without a
gateway-backed slice contribute no controls. The two navigation layers become
one combined drawer on small screens.

Web plugins remain presentation modules. They call authorized gateway APIs and
subscribe to gateway events; they do not gain direct filesystem access,
provider secrets, an independent agent runtime, or implicit backend plugin
permissions. Initially they are compile-time modules in the locally packaged
bundle. Loading independently installed JavaScript requires a later signed
package and permission design rather than arbitrary runtime script injection.

The chat surface filters sessions by exact canonical launch directory, rebuilds
the transcript from durable gateway events, and then follows transient assistant
output over the same SSE connection. Sending and cancellation call gateway
mutations with browser-session CSRF protection. It renders explicit connecting,
reconnecting, offline, expired-session, retry, and empty states. Routes without
a gateway-backed vertical slice state what is planned and expose no pretend
controls.

User, assistant, and reasoning text is parsed as GitHub-flavored Markdown with
Marked and sanitized through a restrictive DOMPurify HTML allow-list before it
enters the document. Executable/embed elements, style attributes, and remote
images are not permitted. A shared Markdown presentation component owns prose,
heading, list, blockquote, code, link, and table styling, while literal tool
payloads remain escaped preformatted text.

The session stream is keyed by the stable session identifier, so a periodic
dashboard metadata refresh cannot clear and replay an unchanged conversation.
Durable replay and transient deltas are folded at most once per animation frame,
and live output is layered over the durable projection without rescanning the
entire event history for every token.

The Agents surface reads the live capsule registry and contains no sample
agents. Its plugin-supplied contextual sidebar shows each capsule's animated
avatar and identity; the selected capsule's reusable settings editor occupies
the main surface without an intermediate overview. The editor changes the
display name, `AGENT.md` instructions, tool access, skill access, and pinned
skills. The slug is shown but remains immutable. A shared agent-directory
component keeps sidebar identity current after a save. A workspace-aware
capability endpoint supplies real tools and visible skills; saving uses the
registry's atomic structured update and preserves `AGENT.md` as the source of
truth. Editor grids collapse before their contents overflow, and the workspace
never uses page-level horizontal scrolling. The instructions field is a shared
Markdown editor with source and sanitized preview modes rather than a page-local
textarea.

Its reusable SVG `AgentAvatar` component draws five robot shapes (circle, soft
square, triangle, scalloped cloud, and hex), three eye styles (dots, visor, and
vertical pills), and a solid or disabled face plate. The face assembly
looks around as a unit, the body and antenna add subtle independent motion, and
reduced-motion clients receive a static fallback. The avatar editor is a focused
portaled dialog with previews, a suggested palette, and a keyboard-operable hue
and saturation/value picker. Saving writes validated avatar metadata to the
agent's `AGENT.md` through the same gateway update path.

The Settings surface owns browser-local appearance preferences. Its shared
switch component selects the neutral light or dark palette, persists the choice
in browser storage, and updates the browser color scheme and theme color. A
small same-origin initializer applies the saved mode before the application
bundle paints without weakening the gateway's script policy.

## Build and packaging

Run the pinned frontend toolchain with:

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web check
pnpm --dir web test
pnpm --dir web build
```

Vite writes the production bundle to `src/hames/web_dist/`. Those generated
files are committed and included in the Python gateway package, so release
installation and `hames web` require no Node process, package installation, CDN,
or network-hosted frontend asset. Rebuild and commit the bundle whenever web
source changes.

## Current capability boundary

This slice is a secure dual-sidebar application shell, workspace chat list,
semantic icon-pack contract, composable surface and conversation-renderer
registry, componentized chat frame and composer-control seats, durable
transcript reconstruction, live assistant output, message submission, run
cancellation, and gateway-backed session creation. A newly
created empty session opens directly in the composer but enters sidebar history
only after its first message. Pending approvals and agent questions render as
composable transcript cards and resolve through the existing gateway controls.
Provider, model, interaction mode, and reasoning effort are real session
settings. Entering bare `/chat` creates and opens a fresh durable session rather
than presenting a selection prompt; it remains absent from the history list
until its first message. The Agents slice lists real capsules and supports
atomic display-name, instructions, tool, skill, pinned-skill, and avatar edits.
Agent creation, retirement, usage, and deeper policy summaries remain planned.
The attachment control remains visibly disabled until its gateway contract exists.
The browser-local light/dark appearance setting is functional. Commands,
gateway-backed settings contributions, and the remaining management editors
are future slices. See
[M10 Web Control](../implementation-plan/M10-WEB-CONTROL.md) for their acceptance
criteria.
