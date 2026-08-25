---
id: linux-gui-testing
name: Linux GUI Testing
description: Select and verify the correct modern Linux graphical environment across headless, Wayland, Xwayland, X11, and isolated displays.
version: 1
scope: global
tools:
- read_file
- list_dir
- shell
triggers:
- Linux GUI
- Wayland
- Xwayland
- X11
- Xvfb
- native window
- compositor
- desktop integration
- fractional scaling
- clipboard
- drag and drop
- graphical test
- Tauri
- Electron
requires: []
scripts: []
---
# Linux GUI testing

Choose the graphical environment from the behavior under test. Xvfb is one useful X11 tool, not a
universal Linux GUI solution.

## Establish the environment

Inspect relevant session evidence before launching graphical tests, including:

```text
XDG_SESSION_TYPE
WAYLAND_DISPLAY
DISPLAY
```

Also inspect project launch scripts, toolkit/backend flags, available test tooling, and useful
application logs. Environment variables indicate what may be available; they do not by themselves
prove which backend a process actually used.

Select the smallest environment that can test the claim:

### Headless

Prefer headless operation when a real desktop session is unnecessary: ordinary web rendering, DOM
testing, most browser automation, and many screenshot tests. Use the application's supported
headless or offscreen mode. Do not claim native window-manager, clipboard, compositor, scaling, or
GPU behavior from a headless result.

### Existing Wayland session

Use the live Wayland session only when the behavior depends on it, such as native windows,
compositor interaction, clipboard, drag-and-drop, fractional scaling, native dialogs, GPU/rendering
behavior, Wayland protocols, or desktop integration. Avoid focus theft, input injection, clipboard
replacement, or window churn on the user's desktop unless the test requires it and the impact is
understood.

### Nested Wayland environment

Use a nested compositor when Wayland behavior is required but isolation from the user's live
compositor is valuable. Prefer tooling already selected by the project or available on the host;
do not hardcode or silently install one compositor. Record the nested display/socket and direct
only the test processes to it. A nested compositor may not reproduce every GPU, protocol, scaling,
or outer-compositor interaction, so state that boundary.

### Existing X11 or Xwayland session

Use this when the bug is genuinely X11-specific or when comparing an application's X11/Xwayland
path with native Wayland. `DISPLAY` alone does not prove Xwayland use. Where possible, combine
process environment, toolkit diagnostics, compositor/window inspection tools, and application logs
to identify the backend.

### Xvfb

Use Xvfb for an isolated virtual X11 display when X11 semantics are appropriate and no real display
features are required. Do not use it as evidence for native Wayland behavior, compositor protocols,
real GPU presentation, fractional scaling, or desktop integration.

Some bugs require an explicit matrix: native Wayland and X11/Xwayland. Keep the launch conditions
and observations separate so one backend's result is not generalized to the other.

## Required execution discipline

1. Record the chosen backend, why it matches the claim, relevant environment, command, logs, and
   window or process identity.
2. Launch only task-specific graphical processes. Use isolated profiles, scratch state, or a nested
   display when practical. Never terminate unrelated desktop, compositor, browser, or user
   processes.
3. Verify startup and backend selection where possible, then exercise the actual behavior. Capture
   focused screenshots, logs, protocol/toolkit diagnostics, or other visual evidence.
4. Clean up every application, browser, nested compositor, Xvfb server, watcher, and temporary
   profile that you started. Target recorded PIDs or process groups rather than broad process names.
5. If the necessary session, permissions, utilities, display protocol, or hardware path is
   unavailable, degrade gracefully: run only the valid subset, report the exact limitation, and
   leave the untested claim unverified.

## Tool selection and composition

- Required: environment inspection, a justified backend choice, safe process ownership, and honest
  evidence boundaries.
- Preferred: project-native launch/test commands and existing diagnostic or capture tools.
- Optional: a nested compositor, backend-specific inspection utilities, or Xvfb when their semantics
  match the failure.
- Load `visual-verification` for visible changes. Add `web-app-debugging` for browser surfaces or
  Tauri/Electron flows that also require server and browser-style inspection.
