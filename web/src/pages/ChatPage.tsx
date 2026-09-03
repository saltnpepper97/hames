import { Match, Show, Switch } from "solid-js";
import type { ConnectionState } from "../components/ConnectionStatus";
import type { Session } from "../api/types";
import { SessionChat } from "../chat/SessionChat";
import { Icon } from "../shell/icons";

interface ChatPageProps {
  connection: ConnectionState;
  error: string;
  selectedSession?: Session;
  onRetry: () => void;
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
            <button class="button" type="button" onClick={props.onRetry}>
              Retry connection
            </button>
          </Show>
        </div>
      </Show>

      <Show when={props.connection === "connected" || props.connection === "reconnecting"}>
        <Switch>
          <Match when={props.selectedSession}>
            {(session) => <SessionChat session={session()} />}
          </Match>
          <Match when={!props.selectedSession}>
            <div class="conversation-empty">
              <Icon name="state.empty" size={24} />
              <h1 id="chat-title">Select a chat</h1>
              <p>Workspace chats are listed in the sidebar.</p>
            </div>
          </Match>
        </Switch>
      </Show>
    </section>
  );
}
