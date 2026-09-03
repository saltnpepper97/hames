import { Navigate, Route, Router } from "@solidjs/router";
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
import { loadDashboard } from "./api/client";
import type { DashboardSnapshot } from "./api/types";
import { AppShell } from "./components/AppShell";
import type { ConnectionState } from "./components/ConnectionStatus";
import { ChatPage } from "./pages/ChatPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

const refreshIntervalMs = 10_000;

interface WorkspaceContextValue {
  connection: Accessor<ConnectionState>;
  error: Accessor<string>;
  snapshot: Accessor<DashboardSnapshot | undefined>;
  sessions: Accessor<DashboardSnapshot["sessions"]>;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue>();

function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("Workspace route rendered outside the app shell");
  return context;
}

function HamesWorkspace(props: ParentProps) {
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
      setError(caught instanceof Error ? caught.message : "Unable to reach Hames");
      setConnection(snapshot() ? "reconnecting" : "offline");
    }
  };

  const refreshWhenVisible = () => {
    if (document.visibilityState === "visible") void refresh();
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
      .filter((session) => session.working_directory === current.bootstrap.working_directory)
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
  });

  createEffect(() => {
    document.documentElement.dataset.connection = connection();
  });

  return (
    <WorkspaceContext.Provider
      value={{ connection, error, snapshot, sessions: workspaceSessions, refresh }}
    >
      <AppShell
        connection={connection()}
        workspace={snapshot()?.bootstrap.working_directory ?? ""}
      >
        {props.children}
      </AppShell>
    </WorkspaceContext.Provider>
  );
}

function ChatRoute() {
  const workspace = useWorkspace();
  return (
    <ChatPage
      connection={workspace.connection()}
      error={workspace.error()}
      health={workspace.snapshot()?.health}
      sessions={workspace.sessions()}
      onRetry={() => void workspace.refresh()}
    />
  );
}

const placeholder = (
  eyebrow: string,
  title: string,
  description: string,
  detail: string,
) => () => (
  <PlaceholderPage
    eyebrow={eyebrow}
    title={title}
    description={description}
    detail={detail}
  />
);

export function App() {
  return (
    <Router root={HamesWorkspace}>
      <Route
        path="/chat"
        component={ChatRoute}
      />
      <Route
        path="/runs"
        component={placeholder(
          "Provenance",
          "Runs",
          "Trace model calls, context, tools, policy, and child-agent work.",
          "Run timelines and exact inspection links will arrive with the chat vertical slice.",
        )}
      />
      <Route
        path="/agents"
        component={placeholder(
          "Identity",
          "Agents",
          "Manage portable agent roles and their effective authority.",
          "Agent editing will continue to write validated AGENT.md capsules through the gateway.",
        )}
      />
      <Route
        path="/memory"
        component={placeholder(
          "Continuity",
          "Memory",
          "Inspect semantic, relationship, operational, and episodic memory.",
          "Memory records will remain provenance-linked and scoped by the existing runtime.",
        )}
      />
      <Route
        path="/skills"
        component={placeholder(
          "Procedures",
          "Skills",
          "Review active procedures, evidence, validation, and version history.",
          "Promotion and rollback will call gateway controls rather than writing files in-browser.",
        )}
      />
      <Route
        path="/scars"
        component={placeholder(
          "Evolution",
          "Scars",
          "Understand corrections, repair candidates, guards, and regressions.",
          "Evidence and repair lineage will be reconstructed from durable gateway events.",
        )}
      />
      <Route
        path="/plugins"
        component={placeholder(
          "Extensions",
          "Plugins",
          "Inspect capabilities, permissions, isolation, and runtime health.",
          "Unsafe execution and permission changes will remain explicit and visually prominent.",
        )}
      />
      <Route
        path="/settings"
        component={placeholder(
          "Local configuration",
          "Settings",
          "Control safe runtime defaults without exposing stored secrets.",
          "Provider credentials will never be returned to or retained by the browser.",
        )}
      />
      <Route path="/" component={() => <Navigate href="/chat" />} />
      <Route path="*404" component={() => <Navigate href="/chat" />} />
    </Router>
  );
}
