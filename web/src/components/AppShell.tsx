import { useLocation, useNavigate } from "@solidjs/router";
import {
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import type { ParentProps } from "solid-js";
import { closeSession, updateSessionPinned } from "../api/client";
import type { Session, Workspace } from "../api/types";
import type { WebPluginRegistry } from "../shell/plugins";
import { useWorkspace } from "../shell/workspace";
import { Icon } from "../shell/icons";
import { Brand } from "./Brand";
import { Button } from "./Button";
import { ConnectionStatus } from "./ConnectionStatus";
import { NavigationSidebar } from "./NavigationSidebar";

interface AppShellProps extends ParentProps {
  registry: WebPluginRegistry;
}

const mobileSidebarQuery = "(max-width: 820px)";

export function AppShell(props: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [navigationOpen, setNavigationOpen] = createSignal(false);
  const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false);
  const [sidebarPeeking, setSidebarPeeking] = createSignal(false);
  const [creatingChat, setCreatingChat] = createSignal(false);
  const [createChatError, setCreateChatError] = createSignal("");
  const [lastChatPath, setLastChatPath] = createSignal(
    location.pathname.startsWith("/chat/") ? location.pathname : "/chat",
  );
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
  const sidebarActionComponent = createMemo(() => {
    const sidebar = activeSurface().sidebar;
    return sidebar.kind === "component" ? sidebar.action : undefined;
  });

  createEffect(() => {
    const path = location.pathname;
    if (path.startsWith("/chat/")) setLastChatPath(path);
    setNavigationOpen(false);
  });

  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      setNavigationOpen(false);
      setSidebarPeeking(false);
    }
  };

  const toggleSidebar = () => {
    if (window.matchMedia?.(mobileSidebarQuery).matches) {
      setNavigationOpen(false);
      return;
    }
    setSidebarCollapsed((collapsed) => {
      const next = !collapsed;
      preferredSidebarCollapsed = next;
      return next;
    });
    setSidebarPeeking(false);
  };

  const previewSidebar = () => {
    if (sidebarCollapsed() && !mobileViewport?.matches) setSidebarPeeking(true);
  };

  const stopPreviewingSidebar = () => {
    if (sidebarCollapsed()) setSidebarPeeking(false);
  };

  const createChat = async (workspaceId?: string) => {
    if (creatingChat()) return;
    if (!workspaceId && !workspace.selectedWorkspace()) {
      navigate("/chat");
      return;
    }
    setCreatingChat(true);
    setCreateChatError("");
    try {
      if (workspaceId && workspaceId !== workspace.selectedWorkspace()?.id) {
        await workspace.selectWorkspace(workspaceId);
      }
      const session = await workspace.createChat();
      navigate(`/chat/${encodeURIComponent(session.id)}`);
    } catch (error) {
      setCreateChatError(error instanceof Error ? error.message : "Unable to create a chat");
    } finally {
      setCreatingChat(false);
    }
  };

  const toggleChatPinned = async (session: Session) => {
    const updated = await updateSessionPinned(session.id, !session.pinned);
    workspace.updateSession(updated);
  };

  const deleteChat = async (session: Session) => {
    await closeSession(session.id);
    workspace.removeSession(session.id);
    if (location.pathname === `/chat/${encodeURIComponent(session.id)}`) {
      navigate("/chat", { replace: true });
    }
  };

  const openChat = async (workspaceId: string, sessionId: string) => {
    if (workspaceId !== workspace.selectedWorkspace()?.id) {
      await workspace.selectWorkspace(workspaceId);
    }
    navigate(`/chat/${encodeURIComponent(sessionId)}`);
  };

  const addWorkspace = async (added: Workspace) => {
    await createChat(added.id);
  };

  const removeWorkspace = async (id: string) => {
    const removingSelected = workspace.selectedWorkspace()?.id === id;
    await workspace.removeWorkspace(id);
    if (removingSelected) navigate("/chat", { replace: true });
  };

  let preferredSidebarCollapsed = false;
  let mobileViewport: MediaQueryList | undefined;
  let sidebarPeekCloseTimer: ReturnType<typeof setTimeout> | undefined;
  const cancelPreviewClose = () => {
    if (!sidebarPeekCloseTimer) return;
    clearTimeout(sidebarPeekCloseTimer);
    sidebarPeekCloseTimer = undefined;
  };
  const syncResponsiveSidebar = () => {
    if (mobileViewport?.matches) {
      setSidebarCollapsed(false);
      return;
    }
    setSidebarCollapsed(preferredSidebarCollapsed);
  };

  onMount(() => {
    document.addEventListener("keydown", closeOnEscape);
    mobileViewport = window.matchMedia?.(mobileSidebarQuery);
    syncResponsiveSidebar();
    mobileViewport?.addEventListener("change", syncResponsiveSidebar);
  });
  onCleanup(() => {
    cancelPreviewClose();
    document.removeEventListener("keydown", closeOnEscape);
    mobileViewport?.removeEventListener("change", syncResponsiveSidebar);
  });

  const sidebarWidth = createMemo(() => {
    const tabs = props.registry.surfaces.filter(surface => surface.id !== "settings").length;
    // Match 2.25rem tabs, .2rem gaps, .75rem side padding, and the outer border.
    return `min(calc(${tabs * 2.25 + Math.max(0, tabs - 1) * 0.2 + 1.5}rem + 1px), 92vw)`;
  });

  return (
    <div
      class="app-frame"
      style={{ "--sidebar-width": sidebarWidth() }}
      classList={{
        "sidebar-collapsed": sidebarCollapsed(),
        "sidebar-peeking": sidebarPeeking(),
      }}
    >
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
          <Icon name="action.menu" size={23} />
        </Button>
        <Brand />
        <ConnectionStatus state={workspace.connection()} />
      </header>

      <Button
        variant="bare"
        class="nav-scrim"
        classList={{ visible: navigationOpen() }}
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavigationOpen(false)}
      />

      <Button
        variant="bare"
        class="sidebar-reveal-button"
        type="button"
        aria-label="Expand sidebar"
        title="Expand sidebar"
        aria-hidden={sidebarPeeking() || undefined}
        tabIndex={sidebarPeeking() ? -1 : undefined}
        onPointerEnter={() => {
          cancelPreviewClose();
          previewSidebar();
        }}
        onPointerLeave={() => {
          cancelPreviewClose();
          sidebarPeekCloseTimer = setTimeout(stopPreviewingSidebar, 260);
        }}
        onClick={toggleSidebar}
      >
        <Icon name="action.expandSidebar" size={18} />
      </Button>

      <div
        id="navigation-stack"
        class="navigation-stack"
        classList={{ open: navigationOpen() }}
        aria-hidden={sidebarCollapsed() && !sidebarPeeking() || undefined}
        onPointerEnter={() => {
          cancelPreviewClose();
          previewSidebar();
        }}
        onPointerLeave={stopPreviewingSidebar}
      >
        <NavigationSidebar
          registry={props.registry}
          activeSurface={activeSurface()}
          sessions={workspace.allSessions()}
          workspaces={workspace.workspaces()}
          selectedWorkspace={workspace.selectedWorkspace()}
          connection={workspace.connection()}
          collapsed={sidebarCollapsed()}
          creatingChat={creatingChat()}
          createChatError={createChatError()}
          sidebarComponent={sidebarComponent()}
          sidebarActionComponent={sidebarActionComponent()}
          sectionDescription={sectionDescription()}
          onToggleCollapsed={toggleSidebar}
          onOpenChatSurface={() => navigate(lastChatPath())}
          onCreateChat={(workspaceId) => void createChat(workspaceId)}
          onOpenChat={openChat}
          onToggleChatPinned={toggleChatPinned}
          onDeleteChat={deleteChat}
          onWorkspaceAdded={addWorkspace}
          onRenameWorkspace={workspace.renameWorkspace}
          onRemoveWorkspace={removeWorkspace}
        />
      </div>

      <main id="main-content" class="workspace">
        <div class="page-stage" classList={{ "chat-stage": activeSurface().id === "chat" }}>
          {props.children}
        </div>
      </main>
    </div>
  );
}
