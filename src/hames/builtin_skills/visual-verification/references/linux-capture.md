# Linux compositor capture

## Identify the real session

Inspect `XDG_SESSION_TYPE`, `XDG_CURRENT_DESKTOP`, `WAYLAND_DISPLAY`, `DISPLAY`, and
`XDG_RUNTIME_DIR`, plus the target process/window and available compositor tools. Do not dump the
entire environment: it may contain credentials. A sandbox with no display socket is not evidence
that the host has no graphical session. Run host-session probes through an allowed host shell;
Hames `skill_run` uses an isolated environment and does not represent the desktop session.

Prefer the exact window or known output. Keep output name, dimensions and scaling in the evidence.
Check capture utility help before using flags; filenames and output/monitor selectors differ.

## Halley

Halley is the user's own compositor. Detect a responsive instance rather than assuming support
from an installed executable:

```sh
halleyctl --version
halleyctl outputs
halleyctl capture --help
halleyctl node --help
```

The native screenshot commands are:

```sh
halleyctl capture menu
halleyctl capture screen
halleyctl capture window
halleyctl capture region
# Select a connector reported by `halleyctl outputs`:
halleyctl capture screen -o DP-1
```

**`-o OUTPUT` means the monitor/connector, not the destination filename.** All four commands enter
Halley's interactive capture flow; even `screen` is a selection/confirmation mode, not an immediate
headless dump. Use the visible picker with the available permitted input control, or arrange for
the user to complete it. Do not open a modal capture if neither can finish/cancel it. Keep the
command in a managed tool process so waiting does not freeze communication indefinitely.

The CLI waits and prints `saved: /actual/path.png` or `cancelled`. Both saved and cancelled outcomes
can exit with code zero. Treat only a reported saved path with a fresh valid image as a capture.
Use that actual path, not a guessed capitalization of `~/Pictures/Screenshots`. If given an older
user screenshot, identify it by timestamp and label it as reference evidence, not a new capture.

Inspect the saved image through vision. If the picker or focus change disturbed a hover/menu,
that image cannot establish the original hover/menu behavior. Use a nonmodal capture route if
available, or browser-level capture for the page state while keeping the evidence boundary clear.

### Halley fallback and failure handling

Where the running compositor exposes and permits screencopy, installed `grim` can capture without
a picker. Check its help and use the actual output name, for example:

```sh
grim -o DP-1 /absolute/task-artifacts/halley-dp1.png
```

Do not assume this works on Halley merely because `grim` is installed. Halley's direct screencopy
permission is governed by its startup capability configuration, including
`HALLEY_ALLOW_SCREEN_CAPTURE` for approved executable paths. A denial or "compositor doesn't support
the screen capture protocol" is a real limitation of that route. Do not change allowlists, bypass
portal consent, reload/restart the compositor, or weaken security to make a capture succeed.
Use the native `halleyctl capture` flow, an already available desktop portal, or a valid browser
capture instead. Never substitute an X11 root screenshot as proof of native Wayland content.

When CLI behavior changes, inspect the current `halleyctl capture --help` and the Halley checkout's
`halley-cli/src/cmd/capture.rs`, `halley-cli/src/main.rs`, and `src/capture/mod.rs`. Verify against
the installed/running version; source from a different build is not runtime proof.

## Other Linux environments

- **Sway/wlroots-compatible compositors:** use installed `grim` when the running protocol permits
  it; query outputs using that compositor's available inspection command. A region selector such
  as `slurp` is interactive and needs a completion/cancellation path. Do not launch it blindly.
- **Hyprland:** inspect `hyprctl` and installed capture tooling; use `grim` only when the protocol
  works. Keep native Wayland and Xwayland evidence separate.
- **KDE Plasma / GNOME:** prefer the installed desktop screenshot application or screenshot portal.
  Discover current CLI options through its help rather than assuming cross-desktop flags. A portal
  may require explicit user selection and return a URI asynchronously; await completion and inspect
  the image. Do not rely on private compositor APIs or silently install a different desktop tool.
- **X11:** an installed X11 window/root capture utility is appropriate for an actual X11 target.
  Discover its options, select the right window, and verify the image. Under Wayland, an X11 root
  capture may be blank or omit native windows even when it exits successfully.
- **Headless/Xvfb/nested Wayland:** useful for isolated application rendering only when the backend
  matches the claim. Record the environment; do not generalize to the user's desktop compositor,
  real output scale, GPU scanout, native popups, or input behavior.

Stop a failed route after diagnosing the concrete failure; retry only with a relevant changed
condition. Keep captures local to task artifacts and avoid unnecessary full-desktop collections.
