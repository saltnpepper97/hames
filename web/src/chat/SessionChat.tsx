import { Show, createEffect, createMemo, createSignal } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import type { Session } from "../api/types";
import { TaskPanel, createTaskCardState } from "./components/TaskPanel";
import { ChatFrame } from "./components/ChatFrame";
import { ChatSessionBar } from "./components/ChatSessionBar";
import type { ChatView } from "./components/ChatViewTabs";
import { ConversationViewport } from "./components/ConversationViewport";
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

export function SessionChat(props: SessionChatProps) {
  const workspace = useWorkspace();
  const [view, setView] = createSignal<ChatView>("chat");
  const [eventsVisited, setEventsVisited] = createSignal(false);
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
  const openWorkspaceChat = async (workspaceId: string) => {
    if (workspaceId === workspace.selectedWorkspace()?.id) return;
    await workspace.selectWorkspace(workspaceId);
    props.onSessionOpened(await workspace.createChat());
  };

  return (
    <ChatFrame fresh={fresh() && view() === "chat"}>
      <ChatSessionBar
        session={props.session}
        view={view()}
        streamState={stream.state()}
        working={Boolean(projection().activeRunId)}
        workerLabel={projection().activeWorker ? `${projection().activeWorker!.agentId} · ${projection().activeWorker!.status === "stopping" ? "Stopping" : "Working"}` : undefined}
        onViewChanged={changeView}
        onSessionUpdated={props.onSessionUpdated}
      />
      <div
        id="chat-view-panel"
        class="chat-view-panel"
        role="tabpanel"
        aria-labelledby="chat-view-tab"
        hidden={view() !== "chat"}
      >
        <Show when={props.session.id} keyed>{(sessionId) => <ConversationViewport
          sessionId={sessionId}
          nodes={projection().nodes}
          streamState={stream.state()}
          fresh={fresh()}
          agentId={props.session.agent_id}
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
      <MessageComposer
        plan={plan()}
        taskCard={<TaskPanel tasks={projection().tasks} open={taskCard.open()} onToggle={taskCard.toggle} />}
        session={props.session}
        activeRunId={projection().activeRunId}
        queueRevision={queueRevision()}
        hidden={view() === "events"}
        onSessionChanged={props.onSessionChanged}
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
