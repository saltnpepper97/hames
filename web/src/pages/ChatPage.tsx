import { Match, Show, Switch } from "solid-js";
import type { ConnectionState } from "../components/ConnectionStatus";
import type { Session } from "../api/types";
import { SessionChat } from "../chat/SessionChat";
import { PendingChatFrame } from "../chat/components/PendingChatFrame";
import { Button } from "../components/Button";

interface ChatPageProps {
  connection: ConnectionState;
  error: string;
  selectedSession?: Session;
  startingSession?: boolean;
  startError?: string;
  onStartFresh?: () => void;
  onRetry: () => void;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
}

export function ChatPage(props: ChatPageProps) {
  return (
    <section class="page chat-page" aria-labelledby="chat-title">
      <Show when={props.connection === "connecting"}>
        <div class="loading-rows" aria-label="Loading sessions" aria-busy="true">
          <span />
          <span />
          <span />
        </div>
      </Show>

      <Show when={props.connection === "offline" || props.connection === "expired"}>
        <div class="error-state" role="alert">
          <div>
            <span class="eyebrow">
              {props.connection === "expired" ? "Session expired" : "Gateway unavailable"}
            </span>
            <h2>
              {props.connection === "expired"
                ? "Reopen Hames Web to continue"
                : "Hames Web lost its local connection"}
            </h2>
            <p>{props.error || "The local gateway did not respond."}</p>
          </div>
          <Show when={props.connection !== "expired"}>
            <Button onClick={props.onRetry}>
              Retry connection
            </Button>
          </Show>
        </div>
      </Show>

      <Show when={props.connection === "connected" || props.connection === "reconnecting"}>
        <Switch>
          <Match when={props.selectedSession}>
            {(session) => (
              <SessionChat
                session={session()}
                onSessionChanged={props.onSessionChanged}
                onSessionUpdated={props.onSessionUpdated}
              />
            )}
          </Match>
          <Match when={!props.selectedSession}>
            <Show when={props.startError} fallback={<PendingChatFrame />}>
              <div class="conversation-empty">
                <h1 id="chat-title">New chat could not start</h1>
                <p>{props.startError}</p>
                <Button loading={props.startingSession} onClick={props.onStartFresh}>Try again</Button>
              </div>
            </Show>
          </Match>
        </Switch>
      </Show>
    </section>
  );
}
