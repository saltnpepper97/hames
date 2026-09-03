import { Show, createMemo, createSignal } from "solid-js";
import type { Session } from "../api/types";
import { ChatFrame } from "./components/ChatFrame";
import { ChatSessionBar } from "./components/ChatSessionBar";
import type { ChatView } from "./components/ChatViewTabs";
import { ConversationViewport } from "./components/ConversationViewport";
import { MessageComposer } from "./components/MessageComposer";
import { EventsView } from "./events/EventsView";
import { projectConversation, withLiveOutput } from "./projection";
import { createSessionStream } from "./sessionStream";

interface SessionChatProps {
  session: Session;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
}

export function SessionChat(props: SessionChatProps) {
  const [view, setView] = createSignal<ChatView>("chat");
  const [eventsVisited, setEventsVisited] = createSignal(false);
  const stream = createSessionStream(() => props.session.id);
  const durableProjection = createMemo(() => projectConversation(stream.events()));
  const projection = createMemo(() =>
    withLiveOutput(durableProjection(), stream.liveOutput(), props.session.agent_id),
  );
  const fresh = createMemo(() =>
    !props.session.title?.trim() && projection().nodes.length === 0,
  );
  const changeView = (next: ChatView) => {
    if (next === "events") setEventsVisited(true);
    setView(next);
  };

  return (
    <ChatFrame fresh={fresh() && view() === "chat"}>
      <ChatSessionBar
        session={props.session}
        view={view()}
        streamState={stream.state()}
        working={Boolean(projection().activeRunId)}
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
        <ConversationViewport
          nodes={projection().nodes}
          streamState={stream.state()}
          fresh={fresh()}
          agentId={props.session.agent_id}
        />
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
        session={props.session}
        activeRunId={projection().activeRunId}
        hidden={view() === "events"}
        onSessionChanged={props.onSessionChanged}
        onSessionUpdated={props.onSessionUpdated}
      />
    </ChatFrame>
  );
}
