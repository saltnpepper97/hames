---
id: visual-verification
name: Visual Verification
description: Validate visible application changes by rendering real states, inspecting focused visual evidence, and iterating on defects.
version: 1
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
- Tauri
- Electron
- GTK
- Qt
requires: []
scripts: []
---
# Visual verification

When modifying visible application behavior, do not assume correctness from source inspection
alone. Run the application, render the changed state, inspect the result, and correct visible
problems before declaring the work complete.

## Required behavior

1. Define the visual claim.
   - Identify the changed surface, the intended appearance or behavior, and the states and sizes
     that could reveal regressions.
   - Capture a before image when comparison will clarify the change or protect an established
     design. Do not create ceremonial screenshots that add no evidence.

2. Render the real application.
   - Use the application's normal rendering path. Capture the relevant page, window, component, or
     region at a legible scale, with enough surrounding context to diagnose layout relationships.
   - Exercise the states that matter: initial, hover, focused, active, open and closed, loading,
     error, empty, and populated. Select the subset relevant to the change rather than mechanically
     recording every state.
   - Test the meaningful viewport or window sizes, including narrow and wide layouts when wrapping,
     overflow, breakpoints, scaling, or resizing can affect the result.

3. Inspect, do not merely collect.
   - Use available model vision or image-inspection capability to examine the captures. Look for
     alignment errors, clipping, overflow, uneven spacing, incorrect layering, broken borders,
     unexpected transparency or backgrounds, bad text wrapping, missing controls, and responsive
     regressions.
   - Compare the rendered output with the requested result, existing visual language, and any
     reference. Treat visible defects as correctness issues, not optional polish.
   - If no visual inspection capability is available, retain the useful images and state plainly
     that they were captured but not visually inspected. Do not fabricate visual verification.

4. Iterate.
   - Correct observed problems, render the same state again, and compare. Continue until the
     relevant defects are resolved or a concrete environment limitation blocks progress.
   - Pair visual inspection with behavioral and automated checks; a good screenshot does not prove
     interaction, accessibility, or data correctness.

5. Report evidence precisely.
   - State which application state, window or route, viewport/window size, and environment were
     inspected. Link or name the useful before/after captures and mention remaining limitations.
   - Keep artifacts in a task-specific directory with descriptive names. Preserve decisive
     evidence; discard duplicates, stale iterations, temporary profiles, and noisy captures.

## Tool selection and composition

- Required: actual rendering of every visible claim being marked verified, plus inspection of the
  resulting evidence.
- Preferred: the application's existing screenshot harness or an available capture and image-view
  capability.
- Fallback: platform screenshot utilities or a manual evidence handoff when automation is absent.
- Combine with `web-app-debugging` for browser applications. Combine with `linux-gui-testing` for
  Tauri, Electron, GTK, Qt, or other Linux-native windows. Use all three when both browser content
  and Linux desktop integration affect the result.

Never report “looks correct” when the changed state was not rendered and inspected.
