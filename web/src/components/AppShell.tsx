import { A, useLocation } from "@solidjs/router";
import { For, Show, createEffect, createSignal, onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";
import type { ConnectionState } from "./ConnectionStatus";
import { Brand } from "./Brand";
import { ConnectionStatus } from "./ConnectionStatus";
import { Icon } from "../shell/icons";
import type { SemanticIconName } from "../shell/icons";

interface AppShellProps extends ParentProps {
  connection: ConnectionState;
  workspace: string;
}

const navigation = [
  { path: "/chat", label: "Chat", icon: "nav.chat" },
  { path: "/runs", label: "Runs", icon: "nav.runs" },
  { path: "/agents", label: "Agents", icon: "nav.agents" },
  { path: "/memory", label: "Memory", icon: "nav.memory" },
  { path: "/skills", label: "Skills", icon: "nav.skills" },
  { path: "/scars", label: "Scars", icon: "nav.scars" },
  { path: "/plugins", label: "Plugins", icon: "nav.plugins" },
  { path: "/settings", label: "Settings", icon: "nav.settings" },
] satisfies ReadonlyArray<{ path: string; label: string; icon: SemanticIconName }>;

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
                <Icon name={item.icon} class="nav-glyph" size={17} />
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
