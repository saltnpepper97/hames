# Run the application you intend to inspect

## Discover the launch path

Read the nearest project instructions and README, then inspect the actual package:

- JS/TS: `package.json` scripts and `packageManager`, lockfile, workspace boundaries, Vite/framework
  configuration, and any browser-test configuration. Use the declared script from its package cwd.
- Rust/native: Cargo workspace, documented executable and feature flags, desktop/service launchers.
  Confirm whether the expected program is a built artifact, installed binary, or wrapper.
- Python: project entry points, virtual environment, service unit, configured port, and health route.
- Electron/Tauri: distinguish dev server plus native shell from a standalone browser page. Test the
  shell too when the defect involves native dragging, dialogs, decorations, or Wayland.

Use existing installed tools. A package runner may download missing packages; verify availability
before invoking it. Do not add browser automation packages, a browser, or an X server silently as a routine fallback.

Reuse a healthy target only when its project, served artifact, and state are known. Otherwise use
an unused loopback port and a task-owned process, retaining stdout/stderr and its process handle.
Wait for the startup log and a successful HTTP/health response. On failure, inspect the log and
listener ownership; do not kill an unknown process or blindly increment ports. Do not expose the
server on all interfaces merely to make automation convenient.

## Browser inspection without a connected browser surface

Check in this order, subject to the tools allowed in the session:

1. The available browser/UI control surface: inventory tabs, select the correct app, navigate and
   inspect its current state. Respect any tool-specific navigation, screenshot, and input rules.
2. Existing repository browser tests or a locally installed browser automation harness. Launch a
   task-owned browser/context, open the verified URL, and exercise the same real flow. Inspect
   console/network errors and screenshot the affected component or viewport. Use explicit hover
   before a hover screenshot, and preserve the same data and viewport for before/after comparison.
3. An installed browser's supported screenshot/headless mode for static page rendering only.
   Check that browser's `--help` and use isolated task state. Static capture alone does not test
   hover, menus, authenticated flows, or compositor behavior. Do not fabricate a placeholder page
   or substitute manually assembled markup for the actual application.
4. The existing GUI session and its compositor capture route. See `linux-capture.md`. Open the
   target only through available/allowed controls. Prefer a known app window over capturing an
   unrelated foreground terminal and calling it the application.

If control is unavailable but the user can open the target state, request that one concrete action
only after preparing the verified app URL and capture path. If image ingestion alone is missing,
report that independently; launching another browser will not give a text-only model vision.

## Native and terminal applications

Use the normal toolkit/backend and existing launch instructions. Record the target process/window
identity. `DISPLAY` being set does not imply a Wayland app is running under Xwayland; both display
variables often coexist. For a TUI, launch in a real terminal/PTTY at the target dimensions and
exercise the real view. Text snapshots supplement, but do not replace, screenshots for color,
clipping, terminal-font, or rendering claims. Do not restart the compositor to test an application.

## Hames-specific delivery

In the Hames checkout, inspect `web/package.json` and `web/vite.config.ts` before running commands.
The Web build currently uses `pnpm --dir web build` and writes to `src/hames/web_dist`. Verify the
configured output path rather than assuming this forever. `hames gateway status` identifies the
running service/URL; `/v1/health` reports readiness and active work. Do not print gateway tokens.

For Web-only changes, compare fetched index/assets with the built files and reload the browser.
Do not restart the gateway just for static assets. For Python/runtime changes, verify the gateway's
actual command/cwd and use the authorized idle-only restart procedure. For native client changes,
compare installed/build artifacts and reopen only the affected task-owned client as appropriate.
A hash match proves delivery, not a correct render.

Built-in Hames skills are loaded into the registry at gateway startup. Editing the bundled skill
package changes source; a safe authorized gateway restart is needed to expose the new version in
that running registry. Check the live skill endpoint afterward instead of claiming source edits
alone activated a skill. Already-running turns may retain a previously loaded skill snapshot.
