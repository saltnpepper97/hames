import { Show } from "solid-js";
import type { Session } from "../../api/types";
import type { StreamState } from "../sessionStream";
import { AgentPicker } from "./AgentPicker";
import { ChatViewTabs } from "./ChatViewTabs";
import type { ChatView } from "./ChatViewTabs";

interface ChatSessionBarProps {
  session: Session;
  view: ChatView;
  streamState: StreamState;
  working: boolean;
  onViewChanged: (view: ChatView) => void;
  onSessionUpdated: (session: Session) => void;
}

export function ChatSessionBar(props: ChatSessionBarProps) {
  const status = () => {
    if (props.streamState === "connecting") return "Loading";
    if (props.streamState === "reconnecting") return "Reconnecting";
    return props.working ? "Working" : "";
  };

  return (
    <header class="chat-session-bar">
      <div class="chat-session-identity">
        <h1 id="chat-title" title={props.session.title?.trim() || "New chat"}>
          {props.session.title?.trim() || "New chat"}
        </h1>
        <Show when={status()}>
          <span class="chat-session-status" data-state={props.streamState} role="status">
            {status()}
          </span>
        </Show>
      </div>
      <ChatViewTabs value={props.view} onChange={props.onViewChanged} />
      <div class="chat-session-actions">
        <AgentPicker
          session={props.session}
          disabled={props.working}
          onSessionUpdated={props.onSessionUpdated}
        />
      </div>
    </header>
  );
}
