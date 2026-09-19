---
id: visual-verification
name: Visual Verification
description: Launch the correct application, reproduce visible states, capture browser or compositor screenshots including Halley, inspect the images, and iterate on visual defects.
version: 2
scope: global
tools:
- read_file
- list_dir
- write_file
- edit_file
- shell
triggers:
- visual change
- UI layout
- screenshot
- responsive design
- clipping
- overflow
- spacing
- window rendering
- Halley
- hover
- visual verification
- Tauri
- Electron
- GTK
- Qt
requires: []
scripts: []
---
# Visual verification v2

Close the loop: identify the app -> run the right artifact -> reproduce the state -> capture ->
inspect pixels -> fix -> repeat. Compilation, DOM assertions, asset hashes, and screenshot file
existence support this loop; none independently proves appearance.

## Establish the target and available capabilities

- Identify the actual application, repository/package, route or window, data, backend, and visual
  claim. For a hover defect, reproduce the hovered state; an initial-page screenshot is insufficient.
- Inspect project instructions, launch scripts, lockfiles, framework/toolkit configuration, existing
  tests, and running services before choosing commands. Do not assume every project uses `npm dev`.
- Distinguish launch, interaction, capture, and image inspection. A browser connector may be absent
  while a local browser runner or compositor capture works. A screenshot utility may work while
  the model cannot see its output. Check each capability separately.
- Prefer the provided UI control tools when available. Respect tool/session restrictions: an absent
  connector is not permission to bypass a required control surface or alter security settings.
  Where host tools are allowed, try the relevant installed fallback before declaring a blocker.
- For startup, browser fallbacks, app identity, and Hames delivery, read
  `references/run-and-inspect.md`. For Linux capture/backend selection, especially Halley, read
  `references/linux-capture.md`. Resolve these paths relative to this skill's package directory.

## Run and reproduce

1. Reuse the correct healthy instance, or launch a task-owned instance with recorded command,
   working directory, PID/process handle, URL/socket, and logs. Use the repository's configured
   package manager and installed dependencies. Confirm readiness, not just successful spawning.
2. Verify the changed artifact reaches that instance. Distinguish source, build output, served
   assets/installed executable, and the already-running process. Reload the page for frontend
   assets; a running executable cannot pick up a new binary merely by refreshing its UI.
3. Navigate to the affected route/window and reproduce the reported state with representative
   data. Check the relevant hover/focus/selection/open state and window sizes. Do not replace a
   long populated table with a convenient empty page, or a native Wayland bug with an X11 render.
4. Keep authorization proportional to the action. Routine task-owned launches and reversible
   verification stay within the requested task. Do not restart the user's compositor, interrupt
   active work, kill unrelated listeners, install dependencies, or change capture permissions
   merely to obtain a screenshot. Use existing authorization rather than asking repeatedly.

## Capture and inspect

- Capture the real browser viewport, application window, or compositor output at a legible scale.
  Use a focused capture with enough surrounding context to see clipping, layout and layering.
  Keep the original frame alongside any crop; note scale and capture scope.
- For compositor bugs, capture the native compositor path. Browser/headless screenshots validate
  page rendering, not desktop presentation, window decorations, GPU scanout, or native input.
- Verify freshness and success: record the command/state, inspect the returned path or artifact,
  check that it is a nonempty decodable image, and confirm it depicts the intended current state.
  A cancelled picker, stale "latest" screenshot, or empty file is not success.
- Pass the actual image to an available image-view/vision capability and inspect it. A filesystem
  path, textual tool result, OCR transcript, or base64 dump does not mean the model saw pixels.
  Hames `read_file` reads UTF-8 text; do not use it as an image viewer. If the current model/tool
  surface cannot ingest images, retain the capture and explicitly separate captured from inspected.
- Look for overflow, clipping, overlapping rows, mismatched alignment, unexpected transparency,
  hover/focus changes, incorrect layering, text wrapping, unreadable contrast, and missing content.
  Compare against the user's reference and the application's existing visual language.
- For hover defects, keep a before/hover pair. A modal screenshot picker can dismiss menus or
  change focus/hover; prefer a nonmodal capture path or browser automation for that state.

## Iterate and deliver

Fix observed defects, reload the correct instance, and recapture the same state. Pair the result
with focused behavioral checks; do not add tests that only assert CSS wording. Preserve useful
before/after evidence in a task-specific location. Clean up only processes/profiles you started;
keep user-owned instances and decisive evidence intact.

Report what was actually inspected: application/route, meaningful state and viewport/backend,
image paths, and any remaining limitation. Separate build/test, artifact delivery, running process,
and visual evidence. If blocked, state which stage failed and the observed error, what relevant
fallback was attempted, and the smallest remaining action. Never stop at "browser unavailable"
without checking an allowed applicable fallback; never label uninspected output visually verified.

Use `web-app-debugging` for browser flows and `linux-gui-testing` when display/backend behavior
matters. These are companions, not prerequisites that prevent the basic verification loop.
