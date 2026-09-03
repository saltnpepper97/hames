import { A, useLocation } from "@solidjs/router";
import { For, Show, createEffect, createSignal, onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";
import type { ConnectionState } from "./ConnectionStatus";
import { Brand } from "./Brand";
import { ConnectionStatus } from "./ConnectionStatus";

interface AppShellProps extends ParentProps {
  connection: ConnectionState;
  workspace: string;
}

const navigation = [
  { path: "/chat", label: "Chat", glyph: "◇" },
  { path: "/runs", label: "Runs", glyph: "↳" },
  { path: "/agents", label: "Agents", glyph: "A" },
  { path: "/memory", label: "Memory", glyph: "M" },
  { path: "/skills", label: "Skills", glyph: "S" },
  { path: "/scars", label: "Scars", glyph: "△" },
  { path: "/plugins", label: "Plugins", glyph: "P" },
  { path: "/settings", label: "Settings", glyph: "·" },
] as const;

function workspaceName(path: string): string {
  if (!path) return "Waiting for gateway";
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

export function AppShell(props: AppShellProps) {
  const location = useLocation();
  const [navigationOpen, setNavigationOpen] = createSignal(false);

  createEffect(() => {
    location.pathname;
    setNavigationOpen(false);
  });

  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") setNavigationOpen(false);
  };

  onMount(() => document.addEventListener("keydown", closeOnEscape));
  onCleanup(() => document.removeEventListener("keydown", closeOnEscape));

  return (
    <div class="app-frame">
      <a class="skip-link" href="#main-content">
        Skip to content
      </a>
      <header class="mobile-header">
        <button
          class="nav-toggle"
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={navigationOpen()}
          aria-controls="primary-navigation"
          onClick={() => setNavigationOpen((open) => !open)}
        >
          <span />
          <span />
        </button>
        <Brand />
        <ConnectionStatus state={props.connection} />
      </header>

      <button
        class="nav-scrim"
        classList={{ visible: navigationOpen() }}
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavigationOpen(false)}
      />

      <aside
        id="primary-navigation"
        class="sidebar"
        classList={{ open: navigationOpen() }}
      >
        <Brand />
        <nav aria-label="Primary">
          <For each={navigation}>
            {(item) => (
              <A
                href={item.path}
                class="nav-item"
                activeClass="active"
                end={item.path === "/chat"}
              >
                <span class="nav-glyph" aria-hidden="true">
                  {item.glyph}
                </span>
                <span>{item.label}</span>
              </A>
            )}
          </For>
        </nav>
        <div class="sidebar-footer">
          <ConnectionStatus state={props.connection} />
          <p>Local control surface</p>
        </div>
      </aside>

      <main id="main-content" class="workspace">
        <div class="workspace-bar">
          <div>
            <span class="eyebrow">Workspace</span>
            <strong>{workspaceName(props.workspace)}</strong>
          </div>
          <Show when={props.workspace}>
            <span class="workspace-path" title={props.workspace}>
              {props.workspace}
            </span>
          </Show>
        </div>
        <div class="page-stage">{props.children}</div>
      </main>
    </div>
  );
}
