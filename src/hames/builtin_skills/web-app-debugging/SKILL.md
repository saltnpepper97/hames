---
id: web-app-debugging
name: Web App Debugging
description: Run and empirically debug a JS or TS web application through real browser flows, runtime evidence, and repeatable verification.
version: 1
scope: global
tools:
- read_file
- list_dir
- write_file
- edit_file
- shell
triggers:
- web app
- browser bug
- frontend debugging
- React
- Next.js
- Vite
- SvelteKit
- Vue
- Astro
- Tauri webview
- Electron renderer
- responsive layout
- browser console
- network request
requires: []
scripts: []
---
# Web app debugging

Close the runtime feedback loop. Source inspection can suggest a fix; it cannot prove that a
page starts, renders, or behaves correctly.

Use this loop:

**understand -> run -> exercise -> inspect -> fix -> reload -> verify -> test -> finish**

## Required behavior

1. Understand the project before running anything.
   - Inspect `package.json`, its scripts and package-manager field, the README, lockfiles,
     workspace files, framework configuration, and existing browser tests.
   - Identify Vite, React, Next.js, Svelte/SvelteKit, Vue/Nuxt, Astro, Angular, Remix, Parcel, or
     another stack from evidence, not from a single filename. Respect monorepo package boundaries.
   - Choose the repository's documented package manager and development script. Do not invent a
     generic command when a project-specific one exists.
   - Do not install or upgrade dependencies merely to get started. Use the existing lockfile and
     installed environment. Ask or explain before a necessary installation, migration, reset, or
     other material setup change.

2. Establish a controlled server.
   - Reuse a healthy project server when ownership and configuration are clear. Otherwise start
     one as a managed process whose PID, command, working directory, port, and logs are known.
   - Prefer the framework's supported port option. Use an unused localhost port where the port is
     selectable; avoid killing an unrelated listener or relying on a race-prone guess.
   - Capture stdout and stderr in an organized scratch artifact. Wait for an explicit ready signal
     and confirm the URL responds before opening a browser. A spawned process is not proof of a
     reachable application.
   - Restart only when configuration or stale state requires it. Hot reload is preferable when it
     is trustworthy.
   - Clean up every server or watcher you started. Terminate the recorded process or process group,
     never a broad `killall`/`pkill` that may affect the user.

3. Exercise the real behavior.
   - Prefer existing browser tests and project tooling. When available, Playwright is the preferred
     general browser automation choice; use an already configured or installed version rather than
     silently adding it. An available browser-control capability is also suitable.
   - Navigate to the affected route and reproduce the actual user path: click, type, scroll,
     navigate, complete and submit forms, and open dialogs or menus as the task requires.
   - Use realistic state and data without modifying production services. Cover success and the
     relevant empty, loading, validation, or error state.
   - For visible or responsive work, exercise the important viewport sizes rather than checking a
     single convenient desktop width.

4. Inspect runtime evidence.
   - Check page errors, browser console errors and warnings, failed or unexpected network requests,
     response status, DOM state, and the accessibility tree when it clarifies names, roles, focus,
     or hidden content.
   - Capture focused screenshots of the changed state. For intermittent or sequence-dependent
     failures, retain a Playwright trace or video when available and proportionate.
   - Distinguish application failures from unavailable test infrastructure. Never translate a
     missing browser, inaccessible service, or unsupported model capability into a passing result.

5. Iterate and finish with evidence.
   - Make the smallest grounded correction, reload or restart as needed, and repeat the same user
     flow. Inspect the result again; do not stop at the first plausible render.
   - Run relevant automated checks after interactive verification. Report the exact routes, flows,
     viewports, console/network result, screenshots or traces, and tests actually checked.
   - If browser verification was impossible, say what blocked it and what remains unverified. Never
     claim UI behavior is correct because the source looks correct or compilation passed.

## Tool selection and composition

- Required: repository inspection, a controlled server lifecycle, a reachable-page check, and
  empirical exercise of the affected flow.
- Preferred: the project's own scripts and Playwright/browser tooling already present.
- Fallback: another existing browser runner or a documented manual verification handoff. Static
  source inspection alone is not a visual or behavioral test.
- Load `visual-verification` as a companion for visible changes. For Tauri or Electron on Linux,
  also load `linux-gui-testing` when native-window or desktop-session behavior matters.

Keep logs, screenshots, traces, and temporary profiles together under a task-specific scratch
location. Preserve only artifacts useful as evidence and remove disposable runtime state.
