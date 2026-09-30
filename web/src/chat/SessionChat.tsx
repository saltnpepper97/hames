import type { ConversationNode } from "./projection";
import { useAgentDirectory } from "../agents/AgentDirectory";
import { Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import type { Session } from "../api/types";
import { TaskPanel, createTaskCardState } from "./components/TaskPanel";
import { ChatFrame } from "./components/ChatFrame";
import { ChatSessionBar } from "./components/ChatSessionBar";
import type { ChatView } from "./components/ChatViewTabs";
import { ConversationViewport } from "./components/ConversationViewport";
import { SitePreview, validateSitePreviewUrl } from "./components/SitePreview";
import { MessageComposer } from "./components/MessageComposer";
import { EventsView } from "./events/EventsView";
import { projectConversation, withLiveOutput } from "./projection";
import { projectReviewPlan } from "./planReview";
import { createSessionStream } from "./sessionStream";
import { useWorkspace } from "../shell/workspace";
import { WorkspaceSwitcher } from "../shell/WorkspaceSwitcher";

interface SessionChatProps {
  session: Session;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
  onSessionOpened: (session: Session) => void;
}

const autoOpenedPreviewEvents = new Set<string>();

export function SessionChat(props: SessionChatProps) {
  const workspace = useWorkspace();
  const sessionId = createMemo(() => props.session.id);
  const directory = useAgentDirectory();
  onMount(() => void directory.ensureLoaded());
  const workerName = (id: string) => directory.agents().find(agent => agent.id === id)?.name ?? id;
  const [commandResult, setCommandResult] = createSignal<ConversationNode>();
  const [sendJump, setSendJump] = createSignal<{ sequence: number; submissionId?: string }>({ sequence: 0 });
  const [view, setView] = createSignal<ChatView>("chat");
  const [previewOpen, setPreviewOpen] = createSignal(false);
  const [previewExpanded, setPreviewExpanded] = createSignal(false);
  const [eventsVisited, setEventsVisited] = createSignal(false);
  createEffect(() => {
    sessionId();
    setCommandResult(undefined);
    setPreviewOpen(false);
    setPreviewExpanded(false);
  });
  const stream = createSessionStream(() => props.session.id);
  // Dashboard polling replaces the session object every ten seconds. Only a
  // changed directory should invalidate projection and its measured DOM nodes.
  const workingDirectory = createMemo(() => props.session.working_directory);
  const durableProjection = createMemo(() =>
    projectConversation(stream.events(), workingDirectory()),
  );
  const nextProjection = createMemo(() =>
    withLiveOutput(durableProjection(), stream.liveOutput(), props.session.agent_id),
  );
  const [projectionState, setProjection] = createStore(nextProjection());
  createEffect(() => setProjection(reconcile(nextProjection(), { key: "id" })));
  const projection = () => projectionState;
  const queueRevision = createMemo(() =>
    `${stream.state()}:${stream.events().filter(event => event.type.startsWith("queue.")).at(-1)?.id ?? ""}`,
  );
  const plan = createMemo(() => projectReviewPlan(stream.events()));
  const sitePreviewRequest = createMemo(() => [...stream.events()]
    .reverse()
    .flatMap(event => {
      if (event.type !== "site.preview.opened" || event.session_id !== props.session.id) return [];
      const value = event.payload.url;
      if (typeof value !== "string") return [];
      const result = validateSitePreviewUrl(value);
      return result.url ? [{ id: event.id, url: result.url }] : [];
    })[0],
  );
  let appliedSitePreviewEventId = "";
  createEffect(() => {
    const request = sitePreviewRequest();
    if (!request || request.id === appliedSitePreviewEventId) return;
    appliedSitePreviewEventId = request.id;
    if (autoOpenedPreviewEvents.has(request.id)) return;
    autoOpenedPreviewEvents.add(request.id);
    setPreviewOpen(true);
    setPreviewExpanded(false);
  });
  const taskCard = createTaskCardState(() => props.session.id);
  const fresh = createMemo(() =>
    !props.session.title?.trim() && projection().nodes.length === 0,
  );
  let appliedTitleEvent = "";
  createEffect(() => {
    const titleEvent = [...stream.events()]
      .reverse()
      .find((event) => event.type === "session.title.changed");
    if (!titleEvent || titleEvent.session_id !== props.session.id || titleEvent.id === appliedTitleEvent) return;
    appliedTitleEvent = titleEvent.id;
    const title = titleEvent.payload.title;
    if (typeof title !== "string" || !title.trim() || title === props.session.title) return;
    props.onSessionUpdated({ ...props.session, title: title.trim() });
  });
  const changeView = (next: ChatView) => {
    if (next === "events") setEventsVisited(true);
    setView(next);
  };
  const togglePreview = () => {
    if (previewOpen()) {
      setPreviewOpen(false);
      setPreviewExpanded(false);
    } else {
      setPreviewOpen(true);
    }
  };
  const closePreview = () => {
    setPreviewOpen(false);
    setPreviewExpanded(false);
  };
  const openWorkspaceChat = async (workspaceId: string) => {
    if (workspaceId === workspace.selectedWorkspace()?.id) return;
    await workspace.selectWorkspace(workspaceId);
    props.onSessionOpened(await workspace.createChat());
  };

  return (
    <ChatFrame
      fresh={fresh() && view() === "chat"}
      previewOpen={previewOpen()}
      previewExpanded={previewExpanded()}
    >
      <ChatSessionBar
        session={props.session}
        view={view()}
        streamState={stream.state()}
        working={Boolean(projection().activeRunId)}
        workerLabel={projection().activeWorker ? `${workerName(projection().activeWorker!.agentId)} · ${projection().activeWorker!.status === "stopping" ? "Stopping" : "Working"}` : undefined}
        previewAvailable={Boolean(sitePreviewRequest())}
        previewOpen={previewOpen()}
        onViewChanged={changeView}
        onPreviewToggle={togglePreview}
        onSessionUpdated={props.onSessionUpdated}
      />
      <div class="chat-workspace">
        <div class="chat-conversation" aria-hidden={previewExpanded() || undefined}>
          <div
            id="chat-view-panel"
            class="chat-view-panel"
            role="tabpanel"
            aria-labelledby="chat-view-tab"
            hidden={view() !== "chat"}
          >
            <Show when={props.session.id} keyed>{(sessionId) => <ConversationViewport
              sessionId={sessionId}
              nodes={commandResult() ? [...projection().nodes, commandResult()!] : projection().nodes}
              streamState={stream.state()}
              fresh={fresh()}
              modelRequired={!props.session.model}
              agentId={props.session.agent_id}
              sendJump={sendJump()}
            />}</Show>
          </div>
          <Show when={eventsVisited()}>
            <div
              id="events-view-panel"
              class="chat-view-panel"
              role="tabpanel"
              aria-labelledby="events-view-tab"
              hidden={view() !== "events"}
            >
              <EventsView events={stream.events()} streamState={stream.state()} />
            </div>
          </Show>
        </div>
        <Show when={sitePreviewRequest()}>
          {(request) => <SitePreview
            sessionId={props.session.id}
            request={request()}
            open={previewOpen()}
            expanded={previewExpanded()}
            onExpanded={(value) => setPreviewExpanded(value)}
            onClose={closePreview}
          />}
        </Show>
      </div>
      <MessageComposer
        onCommandResult={content => setCommandResult({ id: crypto.randomUUID(), kind: "notice", tone: "neutral", content })}
        plan={plan()}
        taskCard={<TaskPanel tasks={projection().tasks} open={taskCard.open()} onToggle={taskCard.toggle} />}
        session={props.session}
        events={stream.events()}
        activeRunId={projection().activeRunId}
        queueRevision={queueRevision()}
        hidden={view() === "events"}
        onSessionChanged={props.onSessionChanged}
        onMessageSent={submissionId => setSendJump(value => ({ sequence: value.sequence + 1, submissionId }))}
        onSessionUpdated={props.onSessionUpdated}
        onSessionOpened={props.onSessionOpened}
        workspaceControl={fresh() ? (
          <WorkspaceSwitcher
            workspaces={workspace.workspaces()}
            selected={workspace.selectedWorkspace()}
            disabled={workspace.connection() !== "connected"}
            onSelect={openWorkspaceChat}
          />
        ) : undefined}
        showStats={!fresh()}
      />
    </ChatFrame>
  );
}
