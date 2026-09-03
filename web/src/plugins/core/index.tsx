import { useLocation, useNavigate, useParams } from "@solidjs/router";
import { Show, createEffect, createMemo, createSignal } from "solid-js";
import { ChatPage } from "../../pages/ChatPage";
import { AgentsPage } from "../../pages/AgentsPage";
import { AgentDetailPage } from "../../pages/AgentDetailPage";
import { PlaceholderPage } from "../../pages/PlaceholderPage";
import type { WebPlugin } from "../../shell/plugins";
import { useWorkspace } from "../../shell/workspace";
import { coreConversationNodes } from "./conversationNodes";
import { coreComposerControls } from "./composerControls";

function ChatSurface() {
  const params = useParams<{ sessionId?: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [startingSession, setStartingSession] = createSignal(false);
  const [startError, setStartError] = createSignal("");
  let attempted = false;
  const selectedSession = createMemo(() =>
    params.sessionId ? workspace.session(params.sessionId) : undefined,
  );

  const startFresh = async () => {
    if (startingSession()) return;
    setStartingSession(true);
    setStartError("");
    try {
      const session = await workspace.createChat();
      if (location.pathname === "/chat") {
        navigate(`/chat/${encodeURIComponent(session.id)}`, { replace: true });
      }
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "Unable to start a new chat");
    } finally {
      setStartingSession(false);
    }
  };

  createEffect(() => {
    if (params.sessionId || workspace.connection() !== "connected" || attempted) return;
    attempted = true;
    void startFresh();
  });

  return (
    <ChatPage
      connection={workspace.connection()}
      error={workspace.error()}
      selectedSession={selectedSession()}
      startingSession={startingSession()}
      startError={startError()}
      onStartFresh={() => void startFresh()}
      onRetry={() => void workspace.refresh()}
      onSessionChanged={() => void workspace.refresh()}
      onSessionUpdated={workspace.updateSession}
    />
  );
}

function AgentSurface() {
  const params = useParams<{ agentId?: string }>();
  const workspace = useWorkspace();
  return (
    <Show when={params.agentId} keyed fallback={<AgentsPage />}>
      {(agentId) => (
        <AgentDetailPage
          agentId={agentId}
          workingDirectory={workspace.snapshot()?.bootstrap.working_directory ?? ""}
        />
      )}
    </Show>
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

export const coreWebPlugin = {
  id: "hames.core",
  conversationNodes: coreConversationNodes,
  composerControls: coreComposerControls,
  surfaces: [
    {
      id: "chat",
      path: "/chat",
      route: ["/chat", "/chat/:sessionId"],
      label: "Chat",
      icon: "nav.chat",
      component: ChatSurface,
      sidebar: { kind: "conversations" },
    },
    {
      id: "agents",
      path: "/agents",
      route: ["/agents", "/agents/:agentId"],
      label: "Agents",
      icon: "nav.agents",
      component: AgentSurface,
      sidebar: { kind: "section", description: "Roles and authority" },
    },
    {
      id: "memory",
      path: "/memory",
      route: "/memory",
      label: "Memory",
      icon: "nav.memory",
      component: placeholder(
        "Continuity",
        "Memory",
        "Inspect semantic, relationship, operational, and episodic memory.",
        "Memory records will remain provenance-linked and scoped by the existing runtime.",
      ),
      sidebar: { kind: "section", description: "Continuity and recall" },
    },
    {
      id: "skills",
      path: "/skills",
      route: "/skills",
      label: "Skills",
      icon: "nav.skills",
      component: placeholder(
        "Procedures",
        "Skills",
        "Review active procedures, evidence, validation, and version history.",
        "Promotion and rollback will call gateway controls rather than writing files in-browser.",
      ),
      sidebar: { kind: "section", description: "Procedures and evidence" },
    },
    {
      id: "scars",
      path: "/scars",
      route: "/scars",
      label: "Scars",
      icon: "nav.scars",
      component: placeholder(
        "Evolution",
        "Scars",
        "Understand corrections, repair candidates, guards, and regressions.",
        "Evidence and repair lineage will be reconstructed from durable gateway events.",
      ),
      sidebar: { kind: "section", description: "Corrections and repair" },
    },
    {
      id: "plugins",
      path: "/plugins",
      route: "/plugins",
      label: "Plugins",
      icon: "nav.plugins",
      component: placeholder(
        "Extensions",
        "Plugins",
        "Inspect capabilities, permissions, isolation, and runtime health.",
        "Unsafe execution and permission changes will remain explicit and visually prominent.",
      ),
      sidebar: { kind: "section", description: "Extensions and access" },
    },
    {
      id: "settings",
      path: "/settings",
      route: "/settings",
      label: "Settings",
      icon: "nav.settings",
      component: placeholder(
        "Local configuration",
        "Settings",
        "Control safe runtime defaults without exposing stored secrets.",
        "Provider credentials will never be returned to or retained by the browser.",
      ),
      sidebar: { kind: "section", description: "Local configuration" },
    },
  ],
} satisfies WebPlugin;
