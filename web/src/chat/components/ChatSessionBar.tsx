import { Button } from "../../components/Button";
import { Icon } from "../../shell/icons";
import { Show } from "solid-js";
import type { Session } from "../../api/types";
import { Spinner } from "../../components/Spinner";
import type { StreamState } from "../sessionStream";
import { AgentPicker } from "./AgentPicker";
import { ChatViewTabs } from "./ChatViewTabs";
import type { ChatView } from "./ChatViewTabs";

interface ChatSessionBarProps {
  session: Session;
  view: ChatView;
  streamState: StreamState;
  working: boolean;
  workerLabel?: string;
  agentStatus?: string;
  agentsOpen?: boolean;
  onAgentsToggle?: () => void;
  onViewChanged: (view: ChatView) => void;
  onSessionUpdated: (session: Session) => void;
}

export function ChatSessionBar(props: ChatSessionBarProps) {
  const status = () => {
    if (props.streamState === "connecting") return "Loading";
    if (props.streamState === "reconnecting") return "Reconnecting";
    return props.working ? props.workerLabel || "Working" : "";
  };

  return (
    <header class="chat-session-bar">
      <div class="chat-session-identity">
        <div class="chat-session-copy">
          <h1 id="chat-title" title={props.session.title?.trim() || "New chat"}>
            {props.session.title?.trim() || "New chat"}
          </h1>
        </div>
        <Show when={status()}>
          <span class="chat-session-status" data-state={props.streamState} role="status">
            <Show when={props.streamState !== "live"}><Spinner size="sm" /></Show>
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
        <Show when={props.onAgentsToggle && props.agentStatus}><Button variant="bare" class="chat-agents-trigger" aria-label="Delegated agents" title={`Delegated agents · ${props.agentStatus?.replaceAll("_", " ")}`} aria-expanded={props.agentsOpen} aria-controls="chat-agents-panel" onClick={props.onAgentsToggle}><Icon name="nav.agents" size={19} /></Button></Show>
      </div>
    </header>
  );
}
