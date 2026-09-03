import { A, useLocation, useNavigate } from "@solidjs/router";
import {
  For,
  Match,
  Show,
  Switch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import type { ParentProps } from "solid-js";
import { Icon } from "../shell/icons";
import type { WebPluginRegistry } from "../shell/plugins";
import { useWorkspace } from "../shell/workspace";
import { Brand } from "./Brand";
import { Button } from "./Button";
import { ConnectionStatus } from "./ConnectionStatus";

interface AppShellProps extends ParentProps {
  registry: WebPluginRegistry;
}

function workspaceName(path: string): string {
  if (!path) return "Waiting for gateway";
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) ?? path;
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

export function AppShell(props: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [navigationOpen, setNavigationOpen] = createSignal(false);
  const [creatingChat, setCreatingChat] = createSignal(false);
  const [createChatError, setCreateChatError] = createSignal("");
  const activeSurface = createMemo(() => {
    const fallback = props.registry.surfaces[0];
    if (!fallback) throw new Error("Hames Web has no registered surfaces");
    return (
      props.registry.surfaces.find(
        (surface) =>
          location.pathname === surface.path || location.pathname.startsWith(`${surface.path}/`),
      ) ?? fallback
    );
  });
  const sectionDescription = createMemo(() => {
    const sidebar = activeSurface().sidebar;
    return sidebar.kind === "section" ? sidebar.description : "";
  });
  const sidebarComponent = createMemo(() => {
    const sidebar = activeSurface().sidebar;
    return sidebar.kind === "component" ? sidebar.component : undefined;
  });

  createEffect(() => {
    location.pathname;
    setNavigationOpen(false);
  });

  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") setNavigationOpen(false);
  };

  const createChat = async () => {
    if (creatingChat()) return;
    setCreatingChat(true);
    setCreateChatError("");
    try {
      const session = await workspace.createChat();
      navigate(`/chat/${encodeURIComponent(session.id)}`);
    } catch (error) {
      setCreateChatError(error instanceof Error ? error.message : "Unable to create a chat");
    } finally {
      setCreatingChat(false);
    }
  };

  onMount(() => document.addEventListener("keydown", closeOnEscape));
  onCleanup(() => document.removeEventListener("keydown", closeOnEscape));

  return (
    <div class="app-frame">
      <a class="skip-link" href="#main-content">
        Skip to content
      </a>
      <header class="mobile-header">
        <Button
          variant="bare"
          class="nav-toggle"
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={navigationOpen()}
          aria-controls="navigation-stack"
          onClick={() => setNavigationOpen((open) => !open)}
        >
          <span />
          <span />
        </Button>
        <Brand />
        <ConnectionStatus state={workspace.connection()} compact />
      </header>

      <Button
        variant="bare"
        class="nav-scrim"
        classList={{ visible: navigationOpen() }}
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavigationOpen(false)}
      />

      <div id="navigation-stack" class="navigation-stack" classList={{ open: navigationOpen() }}>
        <aside class="activity-rail" aria-label="Primary navigation">
          <Brand compact />
          <nav>
            <For each={props.registry.surfaces}>
              {(surface) => (
                <A
                  href={surface.path}
                  class="rail-item"
                  activeClass="active"
                  end={!Array.isArray(surface.route)}
                  aria-label={surface.label}
                  title={surface.label}
                  onClick={(event) => {
                    if (
                      surface.id !== "chat" ||
                      workspace.connection() !== "connected" ||
                      event.button !== 0 ||
                      event.metaKey ||
                      event.ctrlKey ||
                      event.shiftKey ||
                      event.altKey
                    ) return;
                    event.preventDefault();
                    void createChat();
                  }}
                >
                  <Icon name={surface.icon} size={20} />
                </A>
              )}
            </For>
          </nav>
        </aside>

        <aside class="context-sidebar" aria-label={`${activeSurface().label} sidebar`}>
          <div class="context-header">
            <div>
              <h2>{activeSurface().label}</h2>
            </div>
            <Show when={activeSurface().sidebar.kind === "conversations"}>
              <Button
                variant="bare"
                class="context-action"
                type="button"
                aria-label="New chat"
                title="New chat"
                disabled={workspace.connection() !== "connected" || creatingChat()}
                onClick={() => void createChat()}
              >
                <Icon name="action.newChat" size={18} />
              </Button>
            </Show>
          </div>

          <Show when={createChatError()}>
            <p class="context-action-error" role="alert">{createChatError()}</p>
          </Show>

          <Switch>
            <Match when={activeSurface().sidebar.kind === "conversations"}>
              <nav class="conversation-list" aria-label="Workspace chats">
                <Show
                  when={workspace.sessions().length > 0}
                  fallback={<p class="context-empty">No chats in this workspace.</p>}
                >
                  <For each={workspace.sessions()}>
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
            </Match>
            <Match when={sidebarComponent()} keyed>
              {(Sidebar) => <Sidebar />}
            </Match>
            <Match when={activeSurface().sidebar.kind === "section"}>
              <p class="context-empty">{sectionDescription()}</p>
            </Match>
          </Switch>

          <div class="context-footer">
            <ConnectionStatus state={workspace.connection()} />
            <span>Local only</span>
          </div>
        </aside>
      </div>

      <main id="main-content" class="workspace">
        <Show when={activeSurface().id === "chat"}>
          <div class="workspace-bar">
            <div>
              <span class="eyebrow">Workspace</span>
              <strong>{workspaceName(workspace.snapshot()?.bootstrap.working_directory ?? "")}</strong>
            </div>
            <Show when={workspace.snapshot()?.bootstrap.working_directory}>
              {(path) => (
                <span class="workspace-path" title={path()}>
                  {path()}
                </span>
              )}
            </Show>
          </div>
        </Show>
        <div class="page-stage" classList={{ "chat-stage": activeSurface().id === "chat" }}>
          {props.children}
        </div>
      </main>
    </div>
  );
}
