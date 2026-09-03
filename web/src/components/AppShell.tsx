import { useLocation, useNavigate } from "@solidjs/router";
import {
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import type { ParentProps } from "solid-js";
import type { WebPluginRegistry } from "../shell/plugins";
import { useWorkspace } from "../shell/workspace";
import { Brand } from "./Brand";
import { Button } from "./Button";
import { ConnectionStatus } from "./ConnectionStatus";
import { NavigationSidebar } from "./NavigationSidebar";

interface AppShellProps extends ParentProps {
  registry: WebPluginRegistry;
}

function workspaceName(path: string): string {
  if (!path) return "Waiting for gateway";
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

export function AppShell(props: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [navigationOpen, setNavigationOpen] = createSignal(false);
  const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false);
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

  const toggleSidebar = () => {
    if (window.matchMedia?.("(max-width: 820px)").matches) {
      setNavigationOpen(false);
      return;
    }
    setSidebarCollapsed((collapsed) => !collapsed);
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

  let compactViewport: MediaQueryList | undefined;
  let mobileViewport: MediaQueryList | undefined;
  const collapseForViewport = (event: MediaQueryListEvent | MediaQueryList) => {
    if (event.matches) setSidebarCollapsed(true);
  };
  const expandForMobile = (event: MediaQueryListEvent | MediaQueryList) => {
    if (event.matches) setSidebarCollapsed(false);
  };

  onMount(() => {
    document.addEventListener("keydown", closeOnEscape);
    compactViewport = window.matchMedia?.("(min-width: 821px) and (max-width: 1024px)");
    mobileViewport = window.matchMedia?.("(max-width: 820px)");
    if (compactViewport) {
      collapseForViewport(compactViewport);
      compactViewport.addEventListener("change", collapseForViewport);
    }
    if (mobileViewport) {
      expandForMobile(mobileViewport);
      mobileViewport.addEventListener("change", expandForMobile);
    }
  });
  onCleanup(() => {
    document.removeEventListener("keydown", closeOnEscape);
    compactViewport?.removeEventListener("change", collapseForViewport);
    mobileViewport?.removeEventListener("change", expandForMobile);
  });

  return (
    <div class="app-frame" classList={{ "sidebar-collapsed": sidebarCollapsed() }}>
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
        <NavigationSidebar
          registry={props.registry}
          activeSurface={activeSurface()}
          sessions={workspace.sessions()}
          connection={workspace.connection()}
          collapsed={sidebarCollapsed()}
          creatingChat={creatingChat()}
          createChatError={createChatError()}
          sidebarComponent={sidebarComponent()}
          sectionDescription={sectionDescription()}
          onToggleCollapsed={toggleSidebar}
          onCreateChat={() => void createChat()}
        />
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
