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
import { HamesApiError, loadDashboard } from "../api/client";
import type { DashboardSnapshot } from "../api/types";
import type { ConnectionState } from "../components/ConnectionStatus";

const refreshIntervalMs = 10_000;

interface WorkspaceContextValue {
  connection: Accessor<ConnectionState>;
  error: Accessor<string>;
  snapshot: Accessor<DashboardSnapshot | undefined>;
  sessions: Accessor<DashboardSnapshot["sessions"]>;
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
  let interval: ReturnType<typeof setInterval> | undefined;

  const refresh = async () => {
    if (snapshot()) setConnection("reconnecting");
    try {
      const next = await loadDashboard();
      setSnapshot(next);
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
    if (!current) return [];
    return current.sessions
      .filter(
        (session) =>
          session.status === "open" &&
          session.working_directory === current.bootstrap.working_directory,
      )
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
  });

  createEffect(() => {
    document.documentElement.dataset.connection = connection();
  });

  return (
    <WorkspaceContext.Provider
      value={{ connection, error, snapshot, sessions: workspaceSessions, refresh }}
    >
      {props.children}
    </WorkspaceContext.Provider>
  );
}
