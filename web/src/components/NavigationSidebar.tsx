import { A, useLocation } from "@solidjs/router";
import {
  createEffect,
  createMemo,
  createSignal,
  For,
  Match,
  onCleanup,
  onMount,
  Show,
  Switch,
} from "solid-js";
import type { Accessor, Component } from "solid-js";
import type { ConnectionState } from "./ConnectionStatus";
import { Icon } from "../shell/icons";
import type { WebPluginRegistry, WebSurfaceContribution } from "../shell/plugins";
import { WorkspaceAddDialog } from "../shell/WorkspaceAddDialog";
import type { Session, Workspace } from "../api/types";
import { ChatDeleteDialog } from "../chat/components/ChatDeleteDialog";
import { WorkspaceManageDialog } from "../shell/WorkspaceManageDialog";
import { Brand } from "./Brand";
import { Button } from "./Button";
import { ConnectionStatus } from "./ConnectionStatus";
import { DropdownSurface } from "./DropdownSurface";
import { LoadingState } from "./LoadingState";
import { SearchInput } from "./SearchInput";
import { SidebarSearchProvider } from "./SidebarSearchContext";
import { Spinner } from "./Spinner";

interface NavigationSidebarProps {
  registry: WebPluginRegistry;
  activeSurface: WebSurfaceContribution;
  sessions: readonly Session[];
  workspaces: readonly Workspace[];
  selectedWorkspace?: Workspace;
  connection: ConnectionState;
  collapsed: boolean;
  creatingChat: boolean;
  createChatError: string;
  sidebarComponent?: Component;
  sidebarActionComponent?: Component;
  sectionDescription: string;
  onToggleCollapsed: () => void;
  onOpenChatSurface: () => void;
  onCreateChat: (workspaceId?: string) => void;
  onOpenChat: (workspaceId: string, sessionId: string) => Promise<void>;
  onToggleChatPinned: (session: Session) => Promise<void>;
  onDeleteChat: (session: Session) => Promise<void>;
  onWorkspaceAdded: (workspace: Workspace) => Promise<void>;
  onRenameWorkspace: (id: string, title: string) => Promise<void>;
  onRemoveWorkspace: (id: string) => Promise<void>;
}

function sessionTitle(title: string | null): string {
  return title?.trim() || "New chat";
}

function sessionDateKey(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function formatSessionDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Earlier";
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const dateStart = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const daysAgo = Math.round((todayStart.getTime() - dateStart.getTime()) / 86_400_000);
  if (daysAgo === 0) return "Today";
  if (daysAgo === 1) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === today.getFullYear() ? undefined : "numeric",
  }).format(date);
}

function ConversationTitle(props: { title: string }) {
  const [distance, setDistance] = createSignal(0);
  let viewport!: HTMLSpanElement;
  let text!: HTMLSpanElement;
  let observer: ResizeObserver | undefined;

  const measure = () => {
    setDistance(Math.max(0, Math.ceil(text.scrollWidth - viewport.clientWidth)));
  };

  createEffect(() => {
    props.title;
    queueMicrotask(measure);
  });

  onMount(() => {
    measure();
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(measure);
      observer.observe(viewport);
      observer.observe(text);
    }
    window.addEventListener("resize", measure);
  });

  onCleanup(() => {
    observer?.disconnect();
    window.removeEventListener("resize", measure);
  });

  const animationDuration = () => Math.max(3.2, Math.min(8, 2.4 + distance() / 28));

  return (
    <span
      class="conversation-title"
      title={props.title}
      data-overflow={distance() > 0 ? "" : undefined}
      style={`--conversation-title-distance:${distance()}px;--conversation-title-duration:${animationDuration()}s`}
      ref={viewport}
    >
      <span class="conversation-title-text" ref={text}>{props.title}</span>
    </span>
  );
}

function SurfaceLink(props: {
  surface: WebSurfaceContribution;
  connection: Accessor<ConnectionState>;
  onOpenChat: () => void;
}) {
  const location = useLocation();
  let link: HTMLAnchorElement | undefined;
  createEffect(() => {
    const active = location.pathname === props.surface.path || location.pathname.startsWith(`${props.surface.path}/`);
    if (active) queueMicrotask(() => {
      const nav = link?.closest<HTMLElement>(".sidebar-surface-nav");
      if (!nav || !link) return;
      const row = nav.getBoundingClientRect(), item = link.getBoundingClientRect();
      if (item.left < row.left) nav.scrollLeft -= row.left - item.left;
      else if (item.right > row.right) nav.scrollLeft += item.right - row.right;
    });
  });
  return (
    <A
      ref={link}
      href={props.surface.path}
      class="sidebar-surface-link"
      activeClass="active"
      end={!Array.isArray(props.surface.route)}
      aria-label={props.surface.label}
      title={props.surface.label}
      onClick={(event) => {
        if (
          props.surface.id === "chat" &&
          (location.pathname === "/chat" || location.pathname.startsWith("/chat/"))
        ) {
          event.preventDefault();
          return;
        }
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
        props.onOpenChat();
      }}
    >
      <Icon name={props.surface.icon} size={20} />
      <span class="sidebar-surface-label">{props.surface.label}</span>
    </A>
  );
}

function ConversationDirectory(props: {
  sessions: readonly Session[];
  workspaces: readonly Workspace[];
  selectedWorkspace?: Workspace;
  loading: boolean;
  creatingChat: boolean;
  onCreateChat: (workspaceId?: string) => void;
  onOpenChat: (workspaceId: string, sessionId: string) => Promise<void>;
  onTogglePinned: (session: Session) => Promise<void>;
  onDelete: (session: Session) => Promise<void>;
  onWorkspaceAdded: (workspace: Workspace) => Promise<void>;
  onRenameWorkspace: (id: string, title: string) => Promise<void>;
  onRemoveWorkspace: (id: string) => Promise<void>;
}) {
  const location = useLocation();
  const [pinningId, setPinningId] = createSignal("");
  const [deleting, setDeleting] = createSignal<Session>();
  const [managing, setManaging] = createSignal<Workspace>();
  const [workspaceMenu, setWorkspaceMenu] = createSignal<Workspace>();
  const [confirmWorkspaceRemoval, setConfirmWorkspaceRemoval] = createSignal(false);
  const [pickingWorkspace, setPickingWorkspace] = createSignal(false);
  const [searchExpanded, setSearchExpanded] = createSignal(false);
  const [query, setQuery] = createSignal("");
  const [expandedWorkspaceIds, setExpandedWorkspaceIds] = createSignal<ReadonlySet<string>>(
    new Set(),
  );
  const [actionError, setActionError] = createSignal("");
  const [suppressedNewChatDirectories, setSuppressedNewChatDirectories] = createSignal<ReadonlySet<string>>(
    new Set(),
  );
  const observedDraftSessionIds = new Set<string>();
  let lastSelectedWorkspaceId = "";
  let searchInput: HTMLInputElement | undefined;

  const suppressionKey = (workingDirectory: string) =>
    `hames.new-chat-consumed:${workingDirectory}`;

  const setNewChatSuppressed = (workingDirectory: string, suppressed: boolean) => {
    try {
      if (suppressed) sessionStorage.setItem(suppressionKey(workingDirectory), "true");
      else sessionStorage.removeItem(suppressionKey(workingDirectory));
    } catch {
      // The in-memory state still preserves the interaction for this render lifetime.
    }
    setSuppressedNewChatDirectories((current) => {
      const next = new Set(current);
      if (suppressed) next.add(workingDirectory);
      else next.delete(workingDirectory);
      return next;
    });
  };

  createEffect(() => {
    const sessions = props.sessions;
    const restored = new Set(suppressedNewChatDirectories());
    for (const workspace of props.workspaces) {
      try {
        if (sessionStorage.getItem(suppressionKey(workspace.path))) restored.add(workspace.path);
      } catch {
        // Session storage is optional.
      }
    }
    if (restored.size !== suppressedNewChatDirectories().size) {
      setSuppressedNewChatDirectories(restored);
    }
    for (const session of sessions) {
      if (!session.title?.trim()) observedDraftSessionIds.add(session.id);
    }
    for (const session of sessions) {
      if (!session.title?.trim() || !observedDraftSessionIds.has(session.id)) continue;
      observedDraftSessionIds.delete(session.id);
      setNewChatSuppressed(session.working_directory, true);
    }
  });

  const createWorkspaceChat = (workspace: Workspace) => {
    setNewChatSuppressed(workspace.path, false);
    props.onCreateChat(workspace.id);
  };

  createEffect(() => {
    if (!workspaceMenu()) return;
    const closeMenu = (event: MouseEvent) => {
      if (!(event.target as Element).closest?.(".workspace-chat-group-actions")) {
        setWorkspaceMenu(undefined);
      }
    };
    document.addEventListener("mousedown", closeMenu);
    onCleanup(() => document.removeEventListener("mousedown", closeMenu));
  });

  const openWorkspaceDialog = (workspace: Workspace, remove = false) => {
    setWorkspaceMenu(undefined);
    setConfirmWorkspaceRemoval(remove);
    setManaging(workspace);
  };

  createEffect(() => {
    const selectedId = props.selectedWorkspace?.id;
    if (!selectedId || selectedId === lastSelectedWorkspaceId) return;
    lastSelectedWorkspaceId = selectedId;
    setExpandedWorkspaceIds((current) =>
      current.has(selectedId) ? current : new Set([...current, selectedId])
    );
  });

  const groupedWorkspaces = createMemo(() => {
    const normalized = query().trim().toLocaleLowerCase();
    return props.workspaces.flatMap((workspace) => {
      const sessions = props.sessions.filter(
        (session) => session.working_directory === workspace.path,
      );
      if (!normalized) return [{ workspace, sessions, showNewChat: true }];
      const workspaceMatches = `${workspace.title}\n${workspace.path}`
        .toLocaleLowerCase()
        .includes(normalized);
      const matchingSessions = workspaceMatches
        ? sessions
        : sessions.filter((session) => sessionTitle(session.title).toLocaleLowerCase().includes(normalized));
      const newChatMatches = "new chat".includes(normalized);
      return workspaceMatches || matchingSessions.length > 0 || newChatMatches
        ? [{
          workspace,
          sessions: matchingSessions,
          showNewChat: workspaceMatches || newChatMatches,
        }]
        : [];
    });
  });

  const dateGroups = (sessions: readonly Session[]) => {
    const grouped = new Map<string, { key: string; label: string; sessions: Session[] }>();
    for (const session of sessions.filter((candidate) => !candidate.pinned)) {
      const key = sessionDateKey(session.created_at);
      const current = grouped.get(key);
      if (current) current.sessions.push(session);
      else grouped.set(key, {
        key,
        label: formatSessionDate(session.created_at),
        sessions: [session],
      });
    }
    return [...grouped.values()];
  };

  const toggleWorkspace = (workspaceId: string) => {
    setExpandedWorkspaceIds((current) => {
      const next = new Set(current);
      if (next.has(workspaceId)) next.delete(workspaceId);
      else next.add(workspaceId);
      return next;
    });
  };

  const openSearch = () => {
    setSearchExpanded(true);
    queueMicrotask(() => searchInput?.focus());
  };

  const addWorkspace = () => setPickingWorkspace(true);

  const togglePinned = async (session: Session) => {
    if (pinningId()) return;
    setPinningId(session.id);
    setActionError("");
    try {
      await props.onTogglePinned(session);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to update chat");
    } finally {
      setPinningId("");
    }
  };

  const deleteChat = async (session: Session) => {
    await props.onDelete(session);
    setDeleting(undefined);
  };

  const sessionRow = (workspace: Workspace, session: Session, draft = false) => {
    const title = () => sessionTitle(session.title);
    return (
      <div
        class="conversation-item-row"
        classList={{ "conversation-new-chat-row": draft }}
      >
        <A
          href={`/chat/${encodeURIComponent(session.id)}`}
          class="conversation-item"
          activeClass="active"
          onClick={(event) => {
            event.preventDefault();
            void props.onOpenChat(workspace.id, session.id);
          }}
        >
          <ConversationTitle title={title()} />
        </A>
        <Show when={!draft}>
          <div class="conversation-actions">
            <Button
              variant="bare"
              class="conversation-action conversation-pin-action"
              classList={{ pending: pinningId() === session.id }}
              type="button"
              aria-label={`${session.pinned ? "Unpin" : "Pin"} ${title()}`}
              title={session.pinned ? "Unpin chat" : "Pin chat"}
              aria-pressed={session.pinned}
              disabled={Boolean(pinningId())}
              onClick={() => void togglePinned(session)}
            >
              <Show
                when={pinningId() !== session.id}
                fallback={<Spinner size="sm" />}
              >
                <Icon name={session.pinned ? "action.unpin" : "action.pin"} size={14} />
              </Show>
            </Button>
            <Button
              variant="bare"
              class="conversation-action conversation-delete-action"
              type="button"
              aria-label={`Delete ${title()}`}
              title="Delete chat"
              disabled={Boolean(pinningId())}
              onClick={() => setDeleting(session)}
            >
              <Icon name="action.delete" size={14} />
            </Button>
          </div>
        </Show>
      </div>
    );
  };

  const newChatRow = (workspace: Workspace) => (
    <div class="conversation-item-row conversation-new-chat-row">
      <A
        href="/chat"
        class="conversation-item"
        onClick={(event) => {
          event.preventDefault();
          createWorkspaceChat(workspace);
        }}
      >
        <ConversationTitle title="New chat" />
      </A>
    </div>
  );

  return (
    <>
      <div
        class="sidebar-context-header sidebar-search-header workspace-directory-header"
        classList={{ searching: searchExpanded() }}
      >
        <div class="workspace-header-default" aria-hidden={searchExpanded() || undefined}>
          <h2 id="sidebar-context-title">Workspaces</h2>
          <Button
            variant="bare"
            class="workspace-header-action"
            type="button"
            aria-label="Search chats"
            title="Search chats"
            tabIndex={searchExpanded() ? -1 : undefined}
            onClick={openSearch}
          >
            <Icon name="action.search" size={16} />
          </Button>
          <Button
            variant="bare"
            class="workspace-header-action"
            type="button"
            aria-label="Add workspace"
            title="Add workspace"
            tabIndex={searchExpanded() ? -1 : undefined}
            disabled={props.loading || pickingWorkspace()}
            loading={pickingWorkspace()}
            onClick={() => void addWorkspace()}
          >
            <Icon name="action.folderAdd" size={17} />
          </Button>
        </div>
        <div
          class="workspace-header-search"
          aria-hidden={!searchExpanded() || undefined}
        >
          <SearchInput
            elementRef={(element) => { searchInput = element; }}
            value={query()}
            aria-label="Search chats"
            placeholder="Search workspaces and chats…"
            dismissLabel="Close search"
            inactive={!searchExpanded()}
            onValueChange={setQuery}
            onDismiss={() => {
              setQuery("");
              setSearchExpanded(false);
            }}
          />
        </div>
      </div>
      <Show when={!props.loading} fallback={<LoadingState variant="sidebar" label="Loading chats" />}>
        <nav class="conversation-list workspace-conversation-list" aria-label="Workspace chats">
          <Show when={actionError()}>
            <p class="conversation-action-error" role="alert">{actionError()}</p>
          </Show>
          <Show
            when={groupedWorkspaces().length > 0}
            fallback={<p class="context-empty">{query().trim() ? "No matching chats." : "No workspaces yet."}</p>}
          >
            <For each={groupedWorkspaces()}>{({ workspace, sessions, showNewChat }) => {
              const expanded = () => Boolean(query().trim()) || expandedWorkspaceIds().has(workspace.id);
              const draft = () => sessions.find((session) =>
                !session.title?.trim() && location.pathname === `/chat/${encodeURIComponent(session.id)}`
              ) ?? sessions.find((session) => !session.title?.trim());
              const established = () => sessions.filter((session) => Boolean(session.title?.trim()));
              const pinned = () => established().filter((session) => session.pinned);
              const displayNewChat = () => showNewChat && (Boolean(draft()) || established().length === 0) && (
                !suppressedNewChatDirectories().has(workspace.path) ||
                Boolean(draft() && location.pathname === `/chat/${encodeURIComponent(draft()!.id)}`)
              );
              const todayKey = sessionDateKey(new Date().toISOString());
              const timelineGroups = () => {
                const groups = dateGroups(established());
                if (displayNewChat() && !groups.some((group) => group.key === todayKey)) {
                  groups.unshift({ key: todayKey, label: "Today", sessions: [] });
                }
                return groups;
              };
              return (
                <section class="workspace-chat-group" data-selected={props.selectedWorkspace?.id === workspace.id ? "" : undefined}>
                  <div class="workspace-chat-group-header">
                    <Button
                      variant="bare"
                      class="workspace-chat-group-toggle"
                      type="button"
                      aria-expanded={expanded()}
                      aria-label={`${expanded() ? "Collapse" : "Expand"} ${workspace.title}`}
                      onClick={() => toggleWorkspace(workspace.id)}
                    >
                      <Icon name="action.next" size={13} class={expanded() ? "expanded" : ""} />
                      <Icon name="action.folder" size={16} />
                      <span title={workspace.path}>{workspace.title}</span>
                    </Button>
                    <div class="workspace-chat-group-actions">
                      <Button
                        variant="bare"
                        type="button"
                        aria-label={`New chat in ${workspace.title}`}
                        title="New chat"
                        disabled={props.creatingChat}
                        onClick={() => createWorkspaceChat(workspace)}
                      >
                        <Icon name="action.newChat" size={14} />
                      </Button>
                      <Button
                        variant="bare"
                        type="button"
                        aria-label={`Workspace actions for ${workspace.title}`}
                        title="Workspace actions"
                        aria-haspopup="menu"
                        aria-expanded={workspaceMenu()?.id === workspace.id}
                        onClick={() => setWorkspaceMenu((current) =>
                          current?.id === workspace.id ? undefined : workspace
                        )}
                      >
                        <Icon name="action.more" size={15} />
                      </Button>
                      <DropdownSurface
                        open={workspaceMenu()?.id === workspace.id}
                        class="workspace-actions-menu"
                        role="menu"
                        ariaLabel={`${workspace.title} workspace actions`}
                      >
                        <Button variant="bare" role="menuitem" onClick={() => openWorkspaceDialog(workspace)}>
                          <Icon name="action.edit" size={15} />
                          <span>Edit</span>
                        </Button>
                        <Button
                          variant="bare"
                          role="menuitem"
                          class="workspace-actions-delete"
                          onClick={() => openWorkspaceDialog(workspace, true)}
                        >
                          <Icon name="action.delete" size={15} />
                          <span>Delete</span>
                        </Button>
                      </DropdownSurface>
                    </div>
                  </div>
                  <Show when={expanded()}>
                    <div class="workspace-chat-group-sessions">
                      <Show when={pinned().length > 0}>
                        <section class="conversation-section" aria-label={`Pinned chats in ${workspace.title}`}>
                          <h3 class="conversation-section-title">Pinned</h3>
                          <For each={pinned()}>{(session) => sessionRow(workspace, session)}</For>
                        </section>
                      </Show>
                      <For each={timelineGroups()}>{(group) => (
                        <section class="conversation-section conversation-date-group" aria-label={`${group.label} chats in ${workspace.title}`}>
                          <h3 class="conversation-section-title">{group.label}</h3>
                          <Show when={displayNewChat() && group.key === todayKey}>
                            <Show when={draft()} fallback={newChatRow(workspace)}>
                              {(session) => sessionRow(workspace, session(), true)}
                            </Show>
                          </Show>
                          <For each={group.sessions}>{(session) => sessionRow(workspace, session)}</For>
                        </section>
                      )}</For>
                      <Show when={established().length === 0 && !query().trim()}>
                        <p class="workspace-chat-empty">No sessions yet</p>
                      </Show>
                    </div>
                  </Show>
                </section>
              );
            }}</For>
          </Show>
        </nav>
      </Show>
      <Show when={pickingWorkspace()}>
        <WorkspaceAddDialog initialPath={props.selectedWorkspace?.path}
          onClose={() => setPickingWorkspace(false)} onAdded={props.onWorkspaceAdded} />
      </Show>
      <Show when={managing()} keyed>
        {(workspace) => (
          <WorkspaceManageDialog
            workspace={workspace}
            canRemove
            initiallyConfirmingRemoval={confirmWorkspaceRemoval()}
            onClose={() => {
              setManaging(undefined);
              setConfirmWorkspaceRemoval(false);
            }}
            onRename={(title) => props.onRenameWorkspace(workspace.id, title)}
            onRemove={async () => {
              await props.onRemoveWorkspace(workspace.id);
              setManaging(undefined);
              setConfirmWorkspaceRemoval(false);
            }}
          />
        )}
      </Show>
      <Show when={deleting()} keyed>
        {(session) => (
          <ChatDeleteDialog
            session={session}
            onClose={() => setDeleting(undefined)}
            onDelete={deleteChat}
          />
        )}
      </Show>
    </>
  );
}

export function NavigationSidebar(props: NavigationSidebarProps) {
  const primarySurfaces = () =>
    props.registry.surfaces.filter((surface) => surface.id !== "settings");
  const settingsSurface = () =>
    props.registry.surfaces.find((surface) => surface.id === "settings");
  const connection = () => props.connection;
  const [sectionSearchExpanded, setSectionSearchExpanded] = createSignal(false);
  const [sectionSearchQuery, setSectionSearchQuery] = createSignal("");
  let sectionSearchInput: HTMLInputElement | undefined;
  let previousSurfaceId = props.activeSurface.id;

  createEffect(() => {
    const surfaceId = props.activeSurface.id;
    if (surfaceId === previousSurfaceId) return;
    previousSurfaceId = surfaceId;
    setSectionSearchExpanded(false);
    setSectionSearchQuery("");
  });

  const searchable = () => {
    const sidebar = props.activeSurface.sidebar;
    return sidebar.kind === "component" && Boolean(sidebar.searchable);
  };

  const openSectionSearch = () => {
    setSectionSearchExpanded(true);
    queueMicrotask(() => sectionSearchInput?.focus());
  };

  const closeSectionSearch = () => {
    setSectionSearchQuery("");
    setSectionSearchExpanded(false);
  };

  return (
    <SidebarSearchProvider query={sectionSearchQuery}>
    <aside
      class="app-sidebar"
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
          <Icon
            name={props.collapsed ? "action.expandSidebar" : "action.collapseSidebar"}
            size={18}
          />
        </Button>
      </div>

      <nav class="sidebar-surface-nav" aria-label="Primary navigation">
        <For each={primarySurfaces()}>
          {(surface) => (
            <SurfaceLink
              surface={surface}
              connection={connection}
              onOpenChat={props.onOpenChatSurface}
            />
          )}
        </For>
      </nav>

      <section class="sidebar-context" aria-labelledby="sidebar-context-title">
        <Show when={props.activeSurface.id !== "chat"}>
          <div
            class="sidebar-context-header sidebar-search-header"
            classList={{ searching: sectionSearchExpanded() }}
          >
            <div class="workspace-header-default" aria-hidden={sectionSearchExpanded() || undefined}>
              <h2 id="sidebar-context-title">{props.activeSurface.label}</h2>
              <Show when={searchable()}>
                <Button
                  variant="bare"
                  class="workspace-header-action"
                  type="button"
                  aria-label={`Search ${props.activeSurface.label}`}
                  title={`Search ${props.activeSurface.label}`}
                  tabIndex={sectionSearchExpanded() ? -1 : undefined}
                  onClick={openSectionSearch}
                >
                  <Icon name="action.search" size={16} />
                </Button>
              </Show>
              <Show when={props.sidebarActionComponent} keyed>
                {(SidebarAction) => <SidebarAction />}
              </Show>
            </div>
            <Show when={searchable()}>
              <div class="workspace-header-search" aria-hidden={!sectionSearchExpanded() || undefined}>
                <SearchInput
                  elementRef={(element) => { sectionSearchInput = element; }}
                  value={sectionSearchQuery()}
                  aria-label={`Search ${props.activeSurface.label}`}
                  placeholder={`Search ${props.activeSurface.label.toLocaleLowerCase()}…`}
                  dismissLabel="Close search"
                  inactive={!sectionSearchExpanded()}
                  onValueChange={setSectionSearchQuery}
                  onDismiss={closeSectionSearch}
                />
              </div>
            </Show>
          </div>
        </Show>

        <Show when={props.createChatError}>
          <p class="context-action-error" role="alert">{props.createChatError}</p>
        </Show>

        <Switch>
          <Match when={props.activeSurface.sidebar.kind === "conversations"}>
            <ConversationDirectory
              sessions={props.sessions}
              workspaces={props.workspaces}
              selectedWorkspace={props.selectedWorkspace}
              loading={props.connection === "connecting"}
              creatingChat={props.creatingChat}
              onCreateChat={props.onCreateChat}
              onOpenChat={props.onOpenChat}
              onTogglePinned={props.onToggleChatPinned}
              onDelete={props.onDeleteChat}
              onWorkspaceAdded={props.onWorkspaceAdded}
              onRenameWorkspace={props.onRenameWorkspace}
              onRemoveWorkspace={props.onRemoveWorkspace}
            />
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
            <SurfaceLink surface={surface} connection={connection} onOpenChat={props.onOpenChatSurface} />
          )}
        </Show>
      </div>
    </aside>
    </SidebarSearchProvider>
  );
}
