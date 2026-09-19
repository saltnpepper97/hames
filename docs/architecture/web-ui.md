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

## Workspace model

A Hames workspace is a durable host-side registration over one canonical
directory. It has a stable id and editable display title, but it does not own the
directory, its files, or any session history. Removing a registration therefore
never removes user data. Only a deliberate add-folder action creates or
authorizes a registration. Starting the gateway, launching Web from a directory,
and creating a session do not populate the workspace sidebar. A migration hides
registrations created by the earlier automatic-discovery behavior; explicitly
adding one of those paths authorizes the existing record again.

Session membership is not duplicated in the workspace record. A session belongs
to the workspace whose canonical path exactly equals the session's immutable
`working_directory`. That same session path remains the execution, context,
memory, skill, Scar, and trust scope. Registering a workspace does not trust it,
and switching the Web UI never changes an existing session's directory.

The launch directory authenticates the local browser handoff but does not become
a workspace or select one. The active selection is stored per browser tab; when
no authorized workspace exists, Chat opens in an explicit choose-workspace state
and creates no session. Workspace-aware surfaces use the selection after the
user adds a folder; global Agents, Plugins, and Settings do not acquire workspace
ownership. The TUI and classic REPL retain their terminal-native contract: the
canonical process directory remains their session working directory without
adding it to the Web workspace registry.

The Chat sidebar is the persistent workspace browser. It lists every registered
workspace as an expandable folder with that workspace's open, non-empty chats
nested beneath it. Its header owns chat search and the add-folder action; adding
a folder asks the local gateway to open the host's native folder chooser and
registers the selected directory. Hosts without a supported native chooser fall
back to the in-app directory browser, which can also create one direct child
folder. Directory APIs validate canonical existing parents and reject nested or
ambiguous child names. The control above a fresh chat composer is deliberately
only a workspace dropdown. When the registry is empty, its “Choose workspace”
trigger opens that same native add-folder flow directly; workspace management
remains in the sidebar.

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
The shared Spinner and Skeleton are SolidJS ports of the Still UI primitives:
short or shape-unknown waits use the labelled rounded-square spinner, while
directories and detail routes pair it with shape-preserving skeletons. Both
retain reduced-motion and forced-color behavior.

The icon-pack contract lets application components request semantic names such
as `nav.chat` or `state.empty`; the selected pack maps those names to assets.
The default pack uses the Hames artwork for its brand mark, Phosphor for agent
and memory symbols, and Tabler for the rest of the interface. Product
components do not import either library directly. The surface registry composes
route, adaptive-navigation, and contextual-directory contributions from web
plugins. The built-in areas are the first `hames.core` plugin rather than
hard-coded shell navigation.
Conversation messages, reasoning, tool activity, and notices use the same
registry for their renderers, keeping the transcript itself composable.
The resident chat frame is assembled from a focused session bar, view tabs,
viewport, event explorer, composer, menus, and contribution-seat components.
Web plugins can add ordered controls to the composer's typed left and right
seats without reaching into its markup or
owning draft submission. The core plugin currently contributes command
discovery, gateway-backed interaction mode, and a unified model and thinking
selector. Beneath the fixed header, the transcript scroll viewport
fills the remaining chat pane through its bottom edge, so its scrollbar does not
stop above the composer. The composer is anchored over that viewport and its
measured height becomes transcript bottom clearance, keeping messages out from
underneath the input. The composer retains send, queue, and cancel because those
actions belong to its input state machine. This follows the useful
contribution-seat shape of the DeepSeek Harness reference while keeping Hames's
SolidJS and HTTP/SSE runtime boundary.

The composer's leading plus control opens one shared slash-command completion
surface. Typing a leading slash opens and filters that same surface above the
input; Up and Down move its active option, Enter inserts it into the draft, and
Escape dismisses it without discarding text. The catalog includes the core
client commands and any visible Skill whose invocation permits direct user use.

One composer button displays the current model and thinking level. Its root menu
contains Model and Thinking rows which drill into provider-grouped models or the
current model's supported effort levels, following the useful DeepSeek Harness
interaction instead of presenting separate toolbar buttons. Provider probes run
concurrently and remain available during active runs. Selecting a
reasoning-capable model advances to the effort pane and commits provider, model,
and effort together only after explicit confirmation. Models without advertised
reasoning support commit with reasoning off; reasoning models without graduated
levels offer the explicit on/off choice used by the TUI.

On wide screens, the shell uses one 280-pixel adaptive sidebar beside the active
surface. Its expanded state contains the brand, compact surface navigation, and
the active plugin's contextual directory in one visual column;
Settings remains pinned at the bottom. Collapsing it produces a 56-pixel icon
rail from the same controls rather than leaving a second sidebar behind. The
rail stays collapsed until its explicit expand control is used. The contextual
directory is the only scrolling region, while the shell itself stays fixed.
Collapse motion is isolated to the sidebar shell so complex detail surfaces do
not relayout on every animation frame.
Chat keeps New chat beside its contextual heading instead of presenting
the action on unrelated surfaces; Agents, Memory, Skills, Scars, and Plugins
contribute matching create or add actions in their own headings. Wide screens start expanded and retain an
explicit collapse preference;
small screens use the expanded column as a closed-by-default drawer. The mobile
bar uses the shared three-line menu icon and keeps the connection-state label
visible beside its dot. In Chat, the session bar below it retains the title,
working directory, view tabs, and agent avatar/name rather than reducing them to
unlabelled icons. A fresh mobile chat anchors its welcome prompt immediately
in the remaining conversation space while the composer stays anchored at the
bottom. The chat-session bar remains a single compact row at wider mobile sizes
and moves its centered view switch to a second row only on narrow phones.
Intermediate desktop widths keep the full sidebar until the user explicitly
collapses it; viewport compression never chooses the icon rail on the user's
behalf.

Chat contributes every registered workspace and its real, open sessions to the
contextual directory, matching each session to the exact canonical
`working_directory`; empty sessions and closed historical sessions remain out
of the list. Selecting a chat first activates its workspace and then routes its
gateway metadata into the main surface. Search matches workspace titles, paths,
and chat titles. When at least one chat in a workspace is pinned, that folder
exposes a distinct Pinned group above its remaining chats; the group disappears
when empty. Unpinned chats are grouped under compact date separators. Rows show
the complete title at rest and reveal pin/delete actions on hover or keyboard
focus, ellipsizing only while those actions occupy the row. Chats can be removed
after confirmation.
Removal closes the
session and its active runtime resources but retains the append-only local audit
history; archive and restore controls are not exposed. Agents contributes a
component-rendered capsule directory to the same region; selecting an avatar
opens its editor directly in the main surface, and the bare Agents route selects
the first real capsule. The contextual directory names the active surface
without repeating the repository name. The compact Chat bar avoids a second
workspace banner: it keeps only the session title, Chat/Events tabs, meaningful
live-work state, and the selected agent. Areas without a gateway-backed slice
contribute no controls.

Web plugins remain presentation modules. They call authorized gateway APIs and
subscribe to gateway events; they do not gain direct filesystem access,
provider secrets, an independent agent runtime, or implicit backend plugin
permissions. Initially they are compile-time modules in the locally packaged
bundle. Loading independently installed JavaScript requires a later signed
package and permission design rather than arbitrary runtime script injection.

The shell asks the gateway for open, non-empty sessions across registered
workspaces for its browser. The chat surface rebuilds the selected session's
transcript from durable gateway events and then follows transient assistant
output over the same SSE connection. Sending and cancellation call gateway
mutations with browser-session CSRF protection. It renders explicit connecting,
reconnecting, offline, expired-session, retry, and empty states. Routes without
a gateway-backed vertical slice state what is planned and expose no pretend
controls.

Chat and Events are two views over that one resident session stream. Events
maps semantic milestones from the durable history into a compact
sequence-or-time trajectory with Input, Model, and Tools lanes. Its searchable
two-column ledger still exposes every durable record, but windows the rows in
the scroll viewport so large sessions do not mount thousands of controls. The
shared `ResizableTable` shell owns an accessible, persisted Event/Content
divider that supports pointer dragging, arrow-key adjustment, and reset.
Selecting a timeline span or ledger row reveals the exact stored event without
generating summaries or timing data that the gateway did not provide. Chat and
Events remain resident after Events is first opened, avoiding repeated teardown
and reconstruction. The composer also remains mounted so its draft and controls
survive a view change, but it is hidden entirely while Events is selected.
The session bar shows the exact working directory beneath its title, retaining
the full path as hover text when the compact header truncates it.

The selected session agent is changed through the gateway rather than stored in
browser state. The chat-bar picker reads the shared live agent directory, shows
the capsule avatars, and prevents changes during an active run. Its contained
creation dialog writes a real `AGENT.md` capsule with a permanent slug,
authority, and optional Markdown instructions, then assigns that capsule to the
current chat.

A newly created chat is not represented by a passive blank page or a
session-selection prompt. The selected agent's animated avatar, a short prompt,
and the real composer form a centered fresh-work state. The composer instance
stays mounted and moves to its bottom dock when the first durable conversation
node arrives, preserving draft and control state through the transition. The
fresh prompt and composer share a height-aware grid rather than independent
absolute offsets, and the identity mark yields first on especially short
windows so the prompt and input cannot cover each other. A
browser refresh resolves the route's session directly before rendering it, so
an empty durable session can reopen even though it is intentionally absent from
resumable history. Unknown, closed, and out-of-workspace session routes are
replaced by a real fresh session instead of leaving a pending phantom view.

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
textarea. Non-default capsules can be retired after confirmation; the default
capsule has no delete control and existing session attribution remains intact.

The Memory sidebar owns explicit creation for relationship, semantic, and
episodic records. Its form selects visibility, captures the structured subject,
predicate, value, and summary, and writes an immediately active record through
the current workspace session. Memory details can permanently remove a record
after confirmation while retaining the ledger audit event.

Its reusable SVG `AgentAvatar` component draws five robot shapes (circle, soft
square, triangle, scalloped cloud, and hex) and three eye styles (dots, visor, and
vertical pills). The eyes look around as a unit, the body and antenna add subtle
independent motion, and reduced-motion clients receive a static fallback. The
avatar editor is a focused portaled dialog with previews, a suggested palette,
and a keyboard-operable hue and saturation/value picker. Saving writes validated
avatar metadata to the agent's `AGENT.md` through the same gateway update path.

The Skills surface can start a gateway-owned asynchronous authoring job from a
focused creation dialog. The user describes the repeatable outcome and chooses
workspace or current-agent scope; the sidebar reports the job state and refreshes
the catalog after authoring completes rather than creating browser-local Skill
state. A confirmed detail action can remove Hames-created Skills from the active
catalog while retaining immutable versions and ledger evidence. Portable
`.agents` packages and shipped built-ins remain read-only and never expose that
action.

The Settings surface owns browser-local appearance preferences. Its shared
switch component selects the neutral light or dark palette, persists the choice
in browser storage, and updates the browser color scheme and theme color. A
small same-origin initializer applies the saved mode before the application
bundle paints without weakening the gateway's script policy.

The Scars surface reads visible workspace Scars through an existing session and
derives each detail view from the gateway's ledger-backed inspection endpoint.
Its collapsible sidebar separates records needing attention, active guards, and
history. Its contextual Create Scar action records a manually described failure,
recurrence cue, expected behavior, severity, and global/workspace/agent scope,
then opens the new record in the Needs attention group. The main surface leads
with the diagnosis and expected behavior, then
progressively exposes trigger conditions, repair proposals and evaluations,
lifecycle transitions, source evidence, and record provenance. Proposal and
event payloads remain available in disclosures rather than overwhelming the
human explanation. A detail-level trash action can permanently remove a Scar
and its repair records after confirmation while retaining the deletion audit
event.

The Plugins surface reads the installed registry directly from the gateway and
separates enabled and disabled packages in its contextual directory. Details
show the stored manifest capabilities, broker permissions, package fingerprint,
worker state, registered tools, and any isolation warning. Adding a local
package uses a contained inspect-first dialog: the gateway validates the
package, the user reviews and acknowledges requested permissions, and install
leaves it disabled. Enable, disable, and confirmed removal remain gateway-owned
lifecycle operations. Installation starts only from the sidebar action; the
empty main surface centers concise guidance back to that control.

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

This slice is a secure adaptive-sidebar application shell, workspace chat list,
semantic icon-pack contract, composable surface and conversation-renderer
registry, componentized chat frame, Chat/Events views, event overview and
virtualized ledger, chat-level agent selection and creation, composer-control seats, durable
transcript reconstruction, live assistant output, message submission, run
cancellation, and gateway-backed session creation. Chats can be durably pinned
or explicitly closed from their directory rows. Every registered workspace offers
one New chat row at the front of its Today section. Opening it creates a durable
empty session in the centered fresh-work composer and consumes that row while the
conversation remains active. Its first submitted message supplies an immediate
provisional title, later `session.title.changed` events replace that title live,
and pin/delete actions appear only once it is titled. The next New chat row appears
only after the workspace's explicit `+` action. Leaving and returning to Chat
reopens the last conversation without creating or restoring a draft row. A
workspace without established conversations says that there are no sessions yet.
Pending approvals and agent questions render as
composable transcript cards and resolve through the existing gateway controls.
Questions support one-of-many choices, constrained checkbox selections, and
direct text answers; older option-only events continue to render as single-choice
questions.
Pending tool approvals use a focus-trapped permission dialog with explicit
Allow once, Allow for session, and Deny actions. Escape, the close control, and
backdrop dismissal all resolve as Deny; a permission request cannot disappear
without recording a decision.
The compact stats pill below an established chat's composer is conversation
local. At rest it reports model requests, cache-hit percentage, separate input
and output totals, and current context pressure. Its anchored popover breaks
down total, prompt, output, cached input, cache-hit percentage, reasoning,
compiled input, request count, latest context pressure, input budget, response
reserve, and provider-reported cost when available. Settings > Usage deliberately
does not repeat those per-chat details: it shows overall ChatGPT account windows
and an 84-day token heatmap pooled from locally owned events across every chat
and registered or unregistered workspace. Branch ancestry is not replayed into
that aggregate, so forks do not double-count their inherited history.
Provider, model, interaction mode, and reasoning effort are real session
settings. Entering bare `/chat` creates and opens a fresh durable session rather
than presenting a selection prompt. The Agents slice lists real capsules and supports
atomic display-name, instructions, tool, skill, pinned-skill, and avatar edits.
Deeper agent policy summaries remain planned.
The Scars slice lists real visible records and presents their complete detection,
repair, evaluation, guard, regression, evidence, and lifecycle breakdown.
The Plugins slice lists real installed packages and supports manifest inspection,
permission review, installation, runtime enable/disable, and confirmed removal.
The attachment drawer accepts model-compatible images and ordinary files,
persists unsent drafts in the browser, and opens attached content in the shared
lightbox. Settings presents its categories as sections of one continuous page;
the contextual sidebar routes are durable quick-jumps to Appearance and Usage
rather than mutually exclusive pages. Appearance hosts the browser-local
light/dark toggle and Usage hosts the account and pooled-history view. Commands,
further gateway-backed settings
contributions, and the remaining management editors are future slices. See
[M10 Web Control](../implementation-plan/M10-WEB-CONTROL.md) for their acceptance
criteria.

### Plan review

Web shows a durable plan review panel directly above the message composer after
`plan.proposed`. The plan stays in the conversation; the panel survives replay
when the chat is reopened. **Request changes** focuses the composer and sends a
`plan_note` through the shared message API, keeping the session in Plan mode.
Approval stays disabled while a revision is pending or a draft remains unsent.
**Execute plan** calls the existing plan execution endpoint with the current
conversation context, then displays execution status as the session enters Auto.
The Web mode menu directs a ready Plan-to-Auto transition to this explicit review
instead of using the gateway's legacy mode-change approval behavior.


### API connections

Settings > Connections exposes **OpenAI (API)** and **Grok (API)** separately
from **Codex** and **Grok Build**. The API connections use their own API keys;
subscription authentication is not reused. Hames verifies a key through model
discovery before saving it in a private local credential file. The same saved
provider profiles are available to Web and TUI and survive gateway restarts.
Environment-provided keys remain externally managed. No model list is claimed
until that account has been queried.

### Reconnecting during a response

The event broker retains only the current in-progress response for each active
session. A new SSE subscription receives an atomic `response.snapshot` before
history replay, then subsequent deltas in order. The snapshot carries a durable
sequence watermark so replay of earlier messages in the same run cannot erase
the restored prefix. Events published during the history read retain their
ordering with queued deltas. Completed reasoning/text are cleared independently;
terminal runs release their buffers. Web applies durable and transient updates
together to avoid briefly rendering both the final message and its live copy.
