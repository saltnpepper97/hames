import { useLocation, useNavigate, useParams } from "@solidjs/router";
import { Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import { AgentSidebar } from "../../agents/AgentSidebar";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { MemorySidebar } from "../../memory/MemorySidebar";
import { useMemoryDirectory } from "../../memory/MemoryDirectory";
import { ChatPage } from "../../pages/ChatPage";
import { AgentDetailPage } from "../../pages/AgentDetailPage";
import { MemoryPage } from "../../pages/MemoryPage";
import { SkillPage } from "../../pages/SkillPage";
import { SettingsPage } from "../../pages/SettingsPage";
import { PlaceholderPage } from "../../pages/PlaceholderPage";
import { Button } from "../../components/Button";
import type { WebPlugin } from "../../shell/plugins";
import { useWorkspace } from "../../shell/workspace";
import { SkillSidebar } from "../../skills/SkillSidebar";
import { useSkillDirectory } from "../../skills/SkillDirectory";
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
  const navigate = useNavigate();
  const directory = useAgentDirectory();
  const workspace = useWorkspace();

  onMount(() => void directory.ensureLoaded());
  createEffect(() => {
    if (params.agentId) return;
    const first = directory.agents()[0];
    if (first) navigate(`/agents/${encodeURIComponent(first.id)}`, { replace: true });
  });

  return (
    <Show
      when={params.agentId}
      keyed
      fallback={
        <section class="page agent-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <div class="agent-detail-loading"><span /><span /><span /></div>
          </Show>
          <Show when={directory.error()}>
            <div class="error-state">
              <div><span class="eyebrow">Agent error</span><h2>Agents could not be loaded.</h2><p>{directory.error()}</p></div>
              <Button onClick={() => void directory.refresh()}>Try again</Button>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.agents().length === 0}>
            <div class="agent-route-empty">
              <span class="eyebrow">Agents</span>
              <h1>No agents are installed.</h1>
            </div>
          </Show>
        </section>
      }
    >
      {(agentId) => (
        <AgentDetailPage
          agentId={agentId}
          workingDirectory={workspace.snapshot()?.bootstrap.working_directory ?? ""}
        />
      )}
    </Show>
  );
}

function MemorySurface() {
  const params = useParams<{ memoryId?: string }>();
  const navigate = useNavigate();
  const directory = useMemoryDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.snapshot()?.bootstrap.working_directory;
    void directory.ensureLoaded();
  });
  createEffect(() => {
    if (params.memoryId || !directory.loaded()) return;
    const first = directory.records()[0];
    if (first) navigate(`/memory/${encodeURIComponent(first.id)}`, { replace: true });
  });

  return (
    <Show
      when={params.memoryId}
      keyed
      fallback={
        <section class="page memory-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <div class="memory-detail-loading"><span /><span /><span /></div>
          </Show>
          <Show when={directory.error()}>
            <div class="error-state">
              <div><span class="eyebrow">Memory error</span><h2>Memory could not be loaded.</h2><p>{directory.error()}</p></div>
              <Button onClick={() => void directory.refresh()}>Try again</Button>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.records().length === 0}>
            <div class="memory-route-empty">
              <span class="eyebrow">Memory</span>
              <h1>No active memories yet.</h1>
              <p>Ask Hames to remember something durable from a chat.</p>
            </div>
          </Show>
        </section>
      }
    >
      {(memoryId) => <MemoryPage memoryId={memoryId} />}
    </Show>
  );
}

function SkillSurface() {
  const params = useParams<{ skillSlug?: string }>();
  const navigate = useNavigate();
  const directory = useSkillDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.snapshot()?.bootstrap.working_directory;
    void directory.ensureLoaded();
  });
  createEffect(() => {
    if (params.skillSlug || !directory.loaded()) return;
    const first = directory.skills()[0];
    if (first) navigate(`/skills/${encodeURIComponent(first.slug)}`, { replace: true });
  });

  return (
    <Show
      when={params.skillSlug}
      keyed
      fallback={
        <section class="page skill-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <div class="skill-detail-loading"><span /><span /><span /></div>
          </Show>
          <Show when={directory.error()}>
            <div class="error-state">
              <div><span class="eyebrow">Skill error</span><h2>Skills could not be loaded.</h2><p>{directory.error()}</p></div>
              <Button onClick={() => void directory.refresh()}>Try again</Button>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.skills().length === 0}>
            <div class="skill-route-empty">
              <span class="eyebrow">Skills</span>
              <h1>No Skills are available.</h1>
              <p>Add a portable package under .agents/skills or let Hames learn one.</p>
            </div>
          </Show>
        </section>
      }
    >
      {(skillSlug) => <SkillPage skillSlug={skillSlug} />}
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
      sidebar: { kind: "component", component: AgentSidebar },
    },
    {
      id: "memory",
      path: "/memory",
      route: ["/memory", "/memory/:memoryId"],
      label: "Memory",
      icon: "nav.memory",
      component: MemorySurface,
      sidebar: { kind: "component", component: MemorySidebar },
    },
    {
      id: "skills",
      path: "/skills",
      route: ["/skills", "/skills/:skillSlug"],
      label: "Skills",
      icon: "nav.skills",
      component: SkillSurface,
      sidebar: { kind: "component", component: SkillSidebar },
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
      component: SettingsPage,
      sidebar: { kind: "section", description: "Local configuration" },
    },
  ],
} satisfies WebPlugin;
