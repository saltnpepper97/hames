---
id: macos-gui-testing
name: macOS GUI Testing
description: Reproduce and inspect native macOS windows, browser views, folder dialogs, and desktop screenshots with the correct privacy permissions.
version: 1
scope: global
tools:
- read_file
- list_dir
- shell
triggers:
- macOS GUI
- Mac desktop
- native Mac window
- macOS screenshot
- Mac folder picker
- AppleScript
- Accessibility permission
- Screen Recording permission
- Retina scaling
requires: []
scripts: []
---
# macOS GUI testing

Identify the real app, window, display, and user session before testing. Run the installed artifact
that the user will use, with disposable state where possible. Record its PID, launch command, and
window title. A process running on a CI host without an interactive login session cannot establish
that a native dialog or desktop capture works for a person.

Use an available UI control surface first. If host shell control is allowed, macOS provides
`open`, `osascript`, and `screencapture`. Query the target application's own automation support
before using System Events. Accessibility permission is needed for scripted UI input, and Screen
Recording permission may be needed for capture. These permissions belong to the controlling app;
do not change privacy settings or try to bypass a denial. Ask for the smallest user action only
when a real interaction requires it.

For a still frame, use a task-owned PNG path with `screencapture -x -t png <path>`. Prefer a
selected window or an existing browser capture facility when the claim concerns one window. For
folder dialogs, test both selection and cancellation in the real app; AppleScript compilation
alone proves neither. For the TUI, use a native terminal/PTTY and test input and resize at the
claimed dimensions. A headless browser run is evidence for page rendering, not macOS window
behavior.

Check that each capture is fresh, decodable, and depicts the requested state. Inspect its pixels
with an available image-viewing capability before reporting visual correctness. Preserve the
original frame if you crop it, and state whether the image came from a display, window, browser,
or headless renderer. If image inspection is unavailable, report capture and inspection separately.

Clean up only task-owned processes and temporary files. Report the exact app version, macOS
version, architecture, display/terminal size, permissions used, and any untested interaction.
Load `visual-verification` for appearance work and `web-app-debugging` for browser flows.
