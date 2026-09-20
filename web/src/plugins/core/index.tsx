import { useNavigate, useParams } from "@solidjs/router";
import { Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import { AgentSidebar, AgentSidebarAction } from "../../agents/AgentSidebar";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { MemorySidebar, MemorySidebarAction } from "../../memory/MemorySidebar";
import { useMemoryDirectory } from "../../memory/MemoryDirectory";
import { ChatPage } from "../../pages/ChatPage";
import { AgentDetailPage } from "../../pages/AgentDetailPage";
import { MemoryPage } from "../../pages/MemoryPage";
import { SkillPage } from "../../pages/SkillPage";
import { SettingsPage } from "../../pages/SettingsPage";
import { ScarPage } from "../../pages/ScarPage";
import { PluginPage } from "../../pages/PluginPage";
import { Button } from "../../components/Button";
import { LoadingState } from "../../components/LoadingState";
import { SettingsSidebar } from "../../settings/SettingsSidebar";
import { Icon } from "../../shell/icons";
import type { WebPlugin } from "../../shell/plugins";
import { useWorkspace } from "../../shell/workspace";
import { SkillSidebar, SkillSidebarAction } from "../../skills/SkillSidebar";
import { useSkillDirectory } from "../../skills/SkillDirectory";
import { ScarSidebar, ScarSidebarAction } from "../../scars/ScarSidebar";
import { useScarDirectory } from "../../scars/ScarDirectory";
import { usePluginDirectory } from "../PluginDirectory";
import { PluginSidebar, PluginSidebarAction } from "../PluginSidebar";
import { coreConversationNodes } from "./conversationNodes";
import { coreComposerActions, coreComposerControls } from "./composerControls";

function ChatSurface() {
  const params = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [startingSession, setStartingSession] = createSignal(false);
  const [startError, setStartError] = createSignal("");
  let attemptedRoute = "";
  const selectedSession = createMemo(() =>
    params.sessionId ? workspace.session(params.sessionId) : undefined,
  );

  const recoverChat = async () => {
    if (startingSession()) return;
    setStartingSession(true);
    setStartError("");
    const workingDirectory = workspace.workingDirectory();
    const sourceRoute = params.sessionId;
    try {
      const candidates = workspace.allSessions().filter(session =>
        session.working_directory === workingDirectory && session.status === "open"
      );
      const existing = candidates.find(session => session.title?.trim()) ?? candidates[0];
      const session = existing ?? await workspace.createChat();
      if (workspace.workingDirectory() === workingDirectory && params.sessionId === sourceRoute) {
        navigate(`/chat/${encodeURIComponent(session.id)}`, { replace: true });
      }
    } catch (error) {
      if (workspace.workingDirectory() === workingDirectory && params.sessionId === sourceRoute) {
        setStartError(error instanceof Error ? error.message : "Unable to start a new chat");
      }
    } finally {
      setStartingSession(false);
    }
  };

  createEffect(() => {
    const sessionId = params.sessionId;
    const connection = workspace.connection();
    const selected = selectedSession();
    if (connection !== "connected") return;

    if (!sessionId) {
      if (!workspace.selectedWorkspace()) return;
      if (startingSession()) return;
      const routeKey = `/chat:${workspace.workingDirectory()}`;
      if (attemptedRoute === routeKey) return;
      attemptedRoute = routeKey;
      void recoverChat();
      return;
    }

    if (selected) {
      attemptedRoute = `session:${sessionId}`;
      setStartError("");
      return;
    }

    const routeKey = `resolve:${sessionId}`;
    if (attemptedRoute === routeKey) return;
    attemptedRoute = routeKey;
    setStartError("");
    void workspace.resolveSession(sessionId)
      .then((resolved) => {
        if (params.sessionId !== sessionId || resolved) return;
        attemptedRoute = "";
        navigate("/chat", { replace: true });
      })
      .catch((error: unknown) => {
        if (params.sessionId !== sessionId) return;
        setStartError(error instanceof Error ? error.message : "Unable to reopen this chat");
      });
  });

  return (
    <ChatPage
      connection={workspace.connection()}
      error={workspace.error()}
      selectedSession={selectedSession()}
      startingSession={startingSession()}
      startError={startError()}
      workspaceRequired={!workspace.selectedWorkspace()}
      onStartFresh={() => void recoverChat()}
      onRetry={() => void workspace.refresh()}
      onSessionChanged={() => void workspace.refresh()}
      onSessionUpdated={workspace.updateSession}
      onSessionOpened={(session) => {
        navigate(`/chat/${encodeURIComponent(session.id)}`);
        void workspace.refresh();
      }}
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
    if (first) navigate(`/agents/${encodeURIComponent(first.slug || first.id)}`, { replace: true });
  });

  return (
    <Show
      when={params.agentId}
      keyed
      fallback={
        <section class="page agent-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <LoadingState variant="detail" label="Loading agents" />
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
          ready={workspace.connection() === "connected"}
          onRenamed={slug => navigate(`/agents/${encodeURIComponent(slug)}`, { replace: true })}
          workingDirectory={workspace.workingDirectory()}
          onDeleted={() => navigate("/agents", { replace: true })}
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
    workspace.workingDirectory();
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
            <LoadingState variant="detail" label="Loading memory" />
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
    workspace.workingDirectory();
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
            <LoadingState variant="detail" label="Loading Skills" />
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

function ScarSurface() {
  const params = useParams<{ scarId?: string }>();
  const navigate = useNavigate();
  const directory = useScarDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.workingDirectory();
    void directory.ensureLoaded();
  });
  createEffect(() => {
    if (params.scarId || !directory.loaded()) return;
    const first = directory.scars()[0];
    if (first) navigate(`/scars/${encodeURIComponent(first.id)}`, { replace: true });
  });

  return (
    <Show
      when={params.scarId}
      keyed
      fallback={
        <section class="page scar-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <LoadingState variant="detail" label="Loading Scars" />
          </Show>
          <Show when={directory.error()}>
            <div class="error-state">
              <div><span class="eyebrow">Scar error</span><h2>Scars could not be loaded.</h2><p>{directory.error()}</p></div>
              <Button onClick={() => void directory.refresh()}>Try again</Button>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.scars().length === 0}>
            <div class="scar-route-empty">
              <span class="eyebrow">Scars</span>
              <h1>No Scars in this workspace.</h1>
              <p>Meaningful corrections and recurring failures will appear here with their evidence and repair history.</p>
            </div>
          </Show>
        </section>
      }
    >
      {(scarId) => <ScarPage scarId={scarId} />}
    </Show>
  );
}

function PluginSurfaceContent() {
  const params = useParams<{ pluginId?: string }>();
  const navigate = useNavigate();
  const directory = usePluginDirectory();
  const selected = createMemo(() =>
    params.pluginId ? directory.plugins().find((plugin) => plugin.id === params.pluginId) : undefined,
  );

  createEffect(() => void directory.ensureLoaded());
  createEffect(() => {
    if (params.pluginId || !directory.loaded()) return;
    const first = directory.plugins()[0];
    if (first) navigate(`/plugins/${encodeURIComponent(first.id)}`, { replace: true });
  });

  const removed = (pluginId: string) => {
    directory.remove(pluginId);
    navigate("/plugins", { replace: true });
  };

  return (
    <>
      <Show when={selected()} keyed fallback={
        <section class="page plugin-route-state" aria-live="polite">
          <Show when={directory.loading() || !directory.loaded()}>
            <LoadingState variant="detail" label="Loading plugins" />
          </Show>
          <Show when={directory.error()}>
            <div class="error-state">
              <div><span class="eyebrow">Plugin error</span><h2>Plugins could not be loaded.</h2><p>{directory.error()}</p></div>
              <Button onClick={() => void directory.refresh()}>Try again</Button>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.plugins().length === 0}>
            <div class="plugin-route-empty">
              <span class="plugin-empty-icon"><Icon name="nav.plugins" size={24} /></span>
              <span class="eyebrow">Plugins</span>
              <h1>Add capabilities to Hames.</h1>
              <p>Install a local plugin package, review its manifest and permissions, then enable it when you are ready.</p>
              <div class="plugin-empty-hint">
                <span>No plugins yet—use <span class="plugin-add-symbol" role="img" aria-label="Add Plugin"><Icon name="action.add" size={14} /></span> in the sidebar.</span>
              </div>
            </div>
          </Show>
          <Show when={directory.loaded() && !directory.error() && directory.plugins().length > 0 && params.pluginId}>
            <div class="error-state">
              <div><span class="eyebrow">Plugins</span><h2>This plugin is not installed.</h2><p>Choose an installed plugin from the sidebar.</p></div>
            </div>
          </Show>
        </section>
      }>{(plugin) => (
        <PluginPage plugin={plugin} onRemoved={removed} />
      )}</Show>
    </>
  );
}

function PluginSurface() {
  return <PluginSurfaceContent />;
}

function SettingsSurface() {
  const params = useParams<{ category?: string }>();
  return <SettingsPage category={params.category} />;
}

export const coreWebPlugin = {
  id: "hames.core",
  conversationNodes: coreConversationNodes,
  composerControls: coreComposerControls,
  composerActions: coreComposerActions,
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
      sidebar: {
        kind: "component",
        component: AgentSidebar,
        action: AgentSidebarAction,
        searchable: true,
      },
    },
    {
      id: "memory",
      path: "/memory",
      route: ["/memory", "/memory/:memoryId"],
      label: "Memory",
      icon: "nav.memory",
      component: MemorySurface,
      sidebar: {
        kind: "component",
        component: MemorySidebar,
        action: MemorySidebarAction,
        searchable: true,
      },
    },
    {
      id: "skills",
      path: "/skills",
      route: ["/skills", "/skills/:skillSlug"],
      label: "Skills",
      icon: "nav.skills",
      component: SkillSurface,
      sidebar: {
        kind: "component",
        component: SkillSidebar,
        action: SkillSidebarAction,
        searchable: true,
      },
    },
    {
      id: "scars",
      path: "/scars",
      route: ["/scars", "/scars/:scarId"],
      label: "Scars",
      icon: "nav.scars",
      component: ScarSurface,
      sidebar: {
        kind: "component",
        component: ScarSidebar,
        action: ScarSidebarAction,
        searchable: true,
      },
    },
    {
      id: "plugins",
      path: "/plugins",
      route: ["/plugins", "/plugins/:pluginId"],
      label: "Plugins",
      icon: "nav.plugins",
      component: PluginSurface,
      sidebar: {
        kind: "component",
        component: PluginSidebar,
        action: PluginSidebarAction,
        searchable: true,
      },
    },
    {
      id: "settings",
      path: "/settings",
      route: ["/settings", "/settings/:category"],
      label: "Settings",
      icon: "nav.settings",
      component: SettingsSurface,
      sidebar: { kind: "component", component: SettingsSidebar },
    },
  ],
} satisfies WebPlugin;
