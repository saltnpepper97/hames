import type { Session } from "../../api/types";
import type { StreamState } from "../sessionStream";

interface ChatHeaderProps {
  session: Session;
  streamState: StreamState;
  working: boolean;
}

export function ChatHeader(props: ChatHeaderProps) {
  const status = () => {
    if (props.streamState === "connecting") return "Loading history";
    if (props.streamState === "reconnecting") return "Reconnecting";
    return props.working ? "Working" : "Live";
  };

  return (
    <header class="chat-header">
      <div>
        <h1 id="chat-title">{props.session.title?.trim() || "New chat"}</h1>
        <p>
          {props.session.agent_id} <span aria-hidden="true">·</span> {props.session.model}
        </p>
      </div>
      <span class="stream-state" data-state={props.streamState}>
        {status()}
      </span>
    </header>
  );
}
