import { Show, createMemo } from "solid-js";
import type { Session } from "../api/types";
import { ChatFrame } from "./components/ChatFrame";
import { ChatHeader } from "./components/ChatHeader";
import { ConversationViewport } from "./components/ConversationViewport";
import { MessageComposer } from "./components/MessageComposer";
import { projectConversation, withLiveOutput } from "./projection";
import { createSessionStream } from "./sessionStream";

interface SessionChatProps {
  session: Session;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
}

export function SessionChat(props: SessionChatProps) {
  const stream = createSessionStream(() => props.session.id);
  const durableProjection = createMemo(() => projectConversation(stream.events()));
  const projection = createMemo(() =>
    withLiveOutput(durableProjection(), stream.liveOutput(), props.session.agent_id),
  );
  const fresh = createMemo(() =>
    !props.session.title?.trim() && projection().nodes.length === 0,
  );

  return (
    <ChatFrame fresh={fresh()}>
      <Show when={!fresh()}>
        <ChatHeader
          session={props.session}
          streamState={stream.state()}
          working={Boolean(projection().activeRunId)}
        />
      </Show>
      <ConversationViewport
        nodes={projection().nodes}
        streamState={stream.state()}
        fresh={fresh()}
        agentId={props.session.agent_id}
      />
      <MessageComposer
        session={props.session}
        activeRunId={projection().activeRunId}
        onSessionChanged={props.onSessionChanged}
        onSessionUpdated={props.onSessionUpdated}
      />
    </ChatFrame>
  );
}
