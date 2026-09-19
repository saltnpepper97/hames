import {
  createContext,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  useContext,
} from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import {
  HamesApiError,
  createSession,
  deleteWorkspace as deleteWorkspaceRegistration,
  getSession,
  loadDashboard,
  renameWorkspace as renameWorkspaceRegistration,
} from "../api/client";
import type { DashboardSnapshot, Session, Workspace } from "../api/types";
import type { ConnectionState } from "../components/ConnectionStatus";

const refreshIntervalMs = 10_000;

interface WorkspaceContextValue {
  connection: Accessor<ConnectionState>;
  error: Accessor<string>;
  snapshot: Accessor<DashboardSnapshot | undefined>;
  workspaces: Accessor<Workspace[]>;
  selectedWorkspace: Accessor<Workspace | undefined>;
  workingDirectory: Accessor<string>;
  sessions: Accessor<DashboardSnapshot["sessions"]>;
  allSessions: Accessor<DashboardSnapshot["sessions"]>;
  session: (id: string) => Session | undefined;
  resolveSession: (id: string) => Promise<Session | undefined>;
  createChat: () => Promise<Session>;
  updateSession: (session: Session) => void;
  removeSession: (id: string) => void;
  selectWorkspace: (id: string) => Promise<void>;
  renameWorkspace: (id: string, title: string) => Promise<void>;
  removeWorkspace: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue>();

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("Workspace surface rendered outside the app shell");
  return context;
}

export function WorkspaceProvider(props: ParentProps) {
  const [snapshot, setSnapshot] = createSignal<DashboardSnapshot>();
  const [connection, setConnection] = createSignal<ConnectionState>("connecting");
  const [error, setError] = createSignal("");
  const [draftSessions, setDraftSessions] = createSignal<Session[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = createSignal(
    sessionStorage.getItem("hames.selectedWorkspace") ?? "",
  );
  let interval: ReturnType<typeof setInterval> | undefined;
  let refreshGeneration = 0;
  const pendingSessionCreations = new Map<string, Promise<Session>>();

  const refresh = async () => {
    const generation = ++refreshGeneration;
    try {
      const next = await loadDashboard(selectedWorkspaceId());
      if (generation !== refreshGeneration) return;
      const known = new Map(allSessions().map((session) => [session.id, session]));
      next.sessions = next.sessions.map((session) => ({
        ...session,
        title: session.title?.trim() ? session.title : known.get(session.id)?.title ?? session.title,
      }));
      setSnapshot(next);
      const durableSessionIds = new Set(next.sessions.map((session) => session.id));
      setDraftSessions((current) =>
        current.filter((session) => !durableSessionIds.has(session.id))
      );
      const nextWorkspaceId = next.selected_workspace?.id ?? "";
      setSelectedWorkspaceId(nextWorkspaceId);
      if (nextWorkspaceId) sessionStorage.setItem("hames.selectedWorkspace", nextWorkspaceId);
      else sessionStorage.removeItem("hames.selectedWorkspace");
      setError("");
      setConnection("connected");
    } catch (caught) {
      if (caught instanceof HamesApiError && caught.status === 401) {
        setError("This browser session expired. Run hames web to reconnect securely.");
        setConnection("expired");
        return;
      }
      setError(caught instanceof Error ? caught.message : "Unable to reach Hames");
      setConnection(snapshot() ? "reconnecting" : "offline");
    }
  };

  const refreshWhenVisible = () => {
    if (document.visibilityState === "visible" && connection() !== "expired") void refresh();
  };

  onMount(() => {
    void refresh();
    interval = setInterval(refreshWhenVisible, refreshIntervalMs);
    window.addEventListener("online", refreshWhenVisible);
  });
  onCleanup(() => {
    if (interval) clearInterval(interval);
    window.removeEventListener("online", refreshWhenVisible);
  });

  const workspaceSessions = createMemo(() => {
    const current = snapshot();
    if (!current?.selected_workspace) return [];
    const workingDirectory = current.selected_workspace.path;
    return current.sessions
      .filter(
        (session) =>
          session.status === "open" &&
          session.working_directory === workingDirectory,
      )
      .sort((left, right) =>
        Number(right.pinned) - Number(left.pinned) ||
        right.created_at.localeCompare(left.created_at)
      );
  });
  const allSessions = createMemo(() => {
    const durable = snapshot()?.sessions ?? [];
    const byId = new Map<string, Session>();
    for (const candidate of [...durable, ...draftSessions()]) {
      if (candidate.status === "open") byId.set(candidate.id, candidate);
    }
    return [...byId.values()].sort((left, right) =>
      Number(right.pinned) - Number(left.pinned) ||
      right.created_at.localeCompare(left.created_at)
    );
  });

  const session = (id: string) =>
    workspaceSessions().find((candidate) => candidate.id === id) ??
    draftSessions().find((candidate) => candidate.id === id);

  const createChat = async () => {
    const workingDirectory = snapshot()?.selected_workspace?.path;
    if (!workingDirectory) throw new HamesApiError("Add a workspace before starting a chat.");
    const existingDraft = draftSessions().find(
      (session) =>
        session.status === "open" &&
        session.working_directory === workingDirectory &&
        !session.title?.trim(),
    );
    if (existingDraft) return existingDraft;
    const pending = pendingSessionCreations.get(workingDirectory);
    if (pending) return pending;

    const creation = createSession(workingDirectory)
      .then((created) => {
        setDraftSessions((current) =>
          current.some((session) => session.id === created.id) ? current : [created, ...current]
        );
        return created;
      })
      .finally(() => {
        pendingSessionCreations.delete(workingDirectory);
      });
    pendingSessionCreations.set(workingDirectory, creation);
    return creation;
  };

  const resolveSession = async (id: string): Promise<Session | undefined> => {
    const current = session(id);
    if (current) return current;
    try {
      const resolved = await getSession(id);
      const workingDirectory = snapshot()?.selected_workspace?.path;
      if (
        resolved.status !== "open" ||
        !workingDirectory ||
        resolved.working_directory !== workingDirectory
      ) return undefined;
      setDraftSessions((sessions) =>
        sessions.some((candidate) => candidate.id === resolved.id)
          ? sessions
          : [resolved, ...sessions]
      );
      return resolved;
    } catch (caught) {
      if (caught instanceof HamesApiError && caught.status === 404) return undefined;
      throw caught;
    }
  };

  const updateSession = (updated: Session) => {
    ++refreshGeneration;
    setSnapshot((current) => current
      ? {
        ...current,
        sessions: current.sessions.map((candidate) =>
          candidate.id === updated.id ? updated : candidate
        ),
      }
      : current);
    setDraftSessions((current) => current.map((candidate) =>
      candidate.id === updated.id ? updated : candidate
    ));
  };

  const removeSession = (id: string) => {
    setSnapshot((current) => current
      ? { ...current, sessions: current.sessions.filter((candidate) => candidate.id !== id) }
      : current);
    setDraftSessions((current) => current.filter((candidate) => candidate.id !== id));
  };

  const selectWorkspace = async (id: string) => {
    if (id === selectedWorkspaceId()) return;
    setSelectedWorkspaceId(id);
    sessionStorage.setItem("hames.selectedWorkspace", id);
    setDraftSessions([]);
    await refresh();
  };

  const renameWorkspace = async (id: string, title: string) => {
    const updated = await renameWorkspaceRegistration(id, title);
    setSnapshot((current) => current
      ? {
        ...current,
        workspaces: current.workspaces.map((workspace) =>
          workspace.id === updated.id ? updated : workspace
        ),
        selected_workspace: current.selected_workspace?.id === updated.id
          ? updated
          : current.selected_workspace,
      }
      : current);
  };

  const removeWorkspace = async (id: string) => {
    await deleteWorkspaceRegistration(id);
    if (id === selectedWorkspaceId()) {
      setSelectedWorkspaceId("");
      sessionStorage.removeItem("hames.selectedWorkspace");
      setDraftSessions([]);
    }
    await refresh();
  };

  createEffect(() => {
    document.documentElement.dataset.connection = connection();
  });

  return (
    <WorkspaceContext.Provider
      value={{
        connection,
        error,
        snapshot,
        workspaces: () => snapshot()?.workspaces ?? [],
        selectedWorkspace: () => snapshot()?.selected_workspace,
        workingDirectory: () => snapshot()?.selected_workspace?.path ?? "",
        sessions: workspaceSessions,
        allSessions,
        session,
        resolveSession,
        createChat,
        updateSession,
        removeSession,
        selectWorkspace,
        renameWorkspace,
        removeWorkspace,
        refresh,
      }}
    >
      {props.children}
    </WorkspaceContext.Provider>
  );
}
