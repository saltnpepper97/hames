import { A } from "@solidjs/router";
import { For, Match, Show, Switch } from "solid-js";
import type { Accessor, Component } from "solid-js";
import type { ConnectionState } from "./ConnectionStatus";
import { Icon } from "../shell/icons";
import type { WebPluginRegistry, WebSurfaceContribution } from "../shell/plugins";
import type { Session } from "../api/types";
import { Brand } from "./Brand";
import { Button } from "./Button";
import { ConnectionStatus } from "./ConnectionStatus";

interface NavigationSidebarProps {
  registry: WebPluginRegistry;
  activeSurface: WebSurfaceContribution;
  sessions: readonly Session[];
  connection: ConnectionState;
  collapsed: boolean;
  creatingChat: boolean;
  createChatError: string;
  sidebarComponent?: Component;
  sectionDescription: string;
  onToggleCollapsed: () => void;
  onCreateChat: () => void;
}

function sessionTitle(title: string | null): string {
  return title?.trim() || "Untitled chat";
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date);
}

function SurfaceLink(props: {
  surface: WebSurfaceContribution;
  connection: Accessor<ConnectionState>;
  onCreateChat: () => void;
}) {
  return (
    <A
      href={props.surface.path}
      class="sidebar-surface-link"
      activeClass="active"
      end={!Array.isArray(props.surface.route)}
      aria-label={props.surface.label}
      title={props.surface.label}
      onClick={(event) => {
        if (
          props.surface.id !== "chat" ||
          props.connection() !== "connected" ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) return;
        event.preventDefault();
        props.onCreateChat();
      }}
    >
      <Icon name={props.surface.icon} size={20} />
      <span class="sidebar-surface-label">{props.surface.label}</span>
    </A>
  );
}

function ConversationDirectory(props: { sessions: readonly Session[] }) {
  return (
    <nav class="conversation-list" aria-label="Workspace chats">
      <Show
        when={props.sessions.length > 0}
        fallback={<p class="context-empty">No chats in this workspace.</p>}
      >
        <For each={props.sessions}>
          {(session) => (
            <A
              href={`/chat/${encodeURIComponent(session.id)}`}
              class="conversation-item"
              activeClass="active"
            >
              <span class="conversation-title">{sessionTitle(session.title)}</span>
              <span class="conversation-meta">
                <span>{session.agent_id}</span>
                <time dateTime={session.created_at}>{formatTime(session.created_at)}</time>
              </span>
            </A>
          )}
        </For>
      </Show>
    </nav>
  );
}

export function NavigationSidebar(props: NavigationSidebarProps) {
  const primarySurfaces = () =>
    props.registry.surfaces.filter((surface) => surface.id !== "settings");
  const settingsSurface = () =>
    props.registry.surfaces.find((surface) => surface.id === "settings");
  const connection = () => props.connection;

  return (
    <aside
      class="app-sidebar"
      classList={{ collapsed: props.collapsed }}
      aria-label={`${props.activeSurface.label} sidebar`}
    >
      <div class="sidebar-brand-row">
        <div class="sidebar-brand-full"><Brand /></div>
        <Button
          variant="bare"
          class="sidebar-collapse-button"
          type="button"
          aria-label={props.collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={props.collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={props.onToggleCollapsed}
        >
          <span class="sidebar-collapse-icon"><Icon name="action.collapseSidebar" size={18} /></span>
          <span class="sidebar-collapsed-brand"><Brand compact /></span>
          <span class="sidebar-expand-icon"><Icon name="action.expandSidebar" size={18} /></span>
        </Button>
      </div>

      <div class="sidebar-new-chat-wrap">
        <Button
          variant="secondary"
          class="sidebar-new-chat"
          type="button"
          aria-label="New chat"
          title="New chat"
          disabled={props.connection !== "connected" || props.creatingChat}
          onClick={props.onCreateChat}
        >
          <Icon name={props.collapsed ? "action.newChat" : "action.add"} size={18} />
          <span>New chat</span>
        </Button>
      </div>

      <nav class="sidebar-surface-nav" aria-label="Primary navigation">
        <For each={primarySurfaces()}>
          {(surface) => (
            <SurfaceLink
              surface={surface}
              connection={connection}
              onCreateChat={props.onCreateChat}
            />
          )}
        </For>
      </nav>

      <section class="sidebar-context" aria-labelledby="sidebar-context-title">
        <div class="sidebar-context-header">
          <h2 id="sidebar-context-title">{props.activeSurface.label}</h2>
        </div>

        <Show when={props.createChatError}>
          <p class="context-action-error" role="alert">{props.createChatError}</p>
        </Show>

        <Switch>
          <Match when={props.activeSurface.sidebar.kind === "conversations"}>
            <ConversationDirectory sessions={props.sessions} />
          </Match>
          <Match when={props.sidebarComponent} keyed>
            {(Sidebar) => <Sidebar />}
          </Match>
          <Match when={props.activeSurface.sidebar.kind === "section"}>
            <p class="context-empty">{props.sectionDescription}</p>
          </Match>
        </Switch>
      </section>

      <div class="sidebar-footer">
        <Show when={props.connection !== "connected"}>
          <ConnectionStatus state={props.connection} />
        </Show>
        <Show when={settingsSurface()} keyed>
          {(surface) => (
            <SurfaceLink surface={surface} connection={connection} onCreateChat={props.onCreateChat} />
          )}
        </Show>
      </div>
    </aside>
  );
}
