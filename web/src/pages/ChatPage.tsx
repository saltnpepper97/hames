import { Show } from "solid-js";
import type { ConnectionState } from "../components/ConnectionStatus";
import type { Session } from "../api/types";
import { SessionChat } from "../chat/SessionChat";
import { WorkspaceRequiredFrame } from "../chat/components/WorkspaceRequiredFrame";

interface ChatPageProps {
  connection: ConnectionState;
  error: string;
  selectedSession?: Session;
  startingSession?: boolean;
  startError?: string;
  workspaceRequired?: boolean;
  onStartFresh?: () => void;
  onRetry: () => void;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
  onSessionOpened: (session: Session) => void;
}

export function ChatPage(props: ChatPageProps) {
  const unavailable = () => props.connection === "offline" || props.connection === "expired";
  const connected = () => props.connection === "connected" || props.connection === "reconnecting";
  const notice = () => {
    if (props.connection === "expired") return "Reopen Hames Web to reconnect.";
    if (props.connection === "offline") return "Reconnect to Hames to send messages.";
    if (props.connection === "connecting") return "Connecting to Hames…";
    if (props.workspaceRequired) return "Select a workspace to start chatting.";
    if (props.startError) return "Retry starting your chat to send messages.";
    return "Preparing your chat…";
  };
  return (
    <section class="page chat-page" aria-label="Chat">
      <Show when={props.selectedSession} fallback={
        <WorkspaceRequiredFrame
          onSessionOpened={props.onSessionOpened}
          workspaceRequired={props.workspaceRequired}
          notice={notice()}
          error={unavailable() ? props.error || "The local gateway did not respond." : props.startError}
          canSelectWorkspace={connected()}
          busy={props.startingSession}
          onRetry={props.connection === "expired" ? undefined : unavailable() ? props.onRetry : props.startError ? props.onStartFresh : undefined}
          retryLabel={unavailable() ? "Retry connection" : "Try again"}
        />
      }>{(session) => (
        <SessionChat
          session={session()}
          onSessionChanged={props.onSessionChanged}
          onSessionUpdated={props.onSessionUpdated}
          onSessionOpened={props.onSessionOpened}
        />
      )}</Show>
    </section>
  );
}
