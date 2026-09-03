import { Match, Show, Switch } from "solid-js";
import type { ConnectionState } from "../components/ConnectionStatus";
import type { GatewayHealth, Session } from "../api/types";
import { Icon } from "../shell/icons";

interface ChatPageProps {
  connection: ConnectionState;
  error: string;
  health?: GatewayHealth;
  selectedSession?: Session;
  onRetry: () => void;
}

export function ChatPage(props: ChatPageProps) {
  return (
    <section class="page chat-page" aria-labelledby="chat-title">
      <div class="page-heading">
        <div>
          <span class="eyebrow">Current workspace</span>
          <h1 id="chat-title">{props.selectedSession?.title?.trim() || "Chat"}</h1>
          <p>
            {props.selectedSession
              ? "Session metadata from the gateway."
              : "Select an existing chat from the sidebar."}
          </p>
        </div>
        <Show when={props.health}>
          {(health) => (
            <div class="runtime-summary" aria-label="Gateway activity">
              <span>
                <strong>{health().active_runs}</strong> active runs
              </span>
              <span>
                <strong>{health().active_terminals}</strong> terminals
              </span>
            </div>
          )}
        </Show>
      </div>

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
            {(session) => (
              <div class="conversation-detail">
                <dl>
                  <div>
                    <dt>Agent</dt>
                    <dd>{session().agent_id}</dd>
                  </div>
                  <div>
                    <dt>Model</dt>
                    <dd>{session().model}</dd>
                  </div>
                  <div>
                    <dt>Mode</dt>
                    <dd>{session().interaction_mode}</dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>{session().status}</dd>
                  </div>
                </dl>
                <div class="conversation-empty">
                  <Icon name="state.empty" size={24} />
                  <h2>Transcript not wired yet</h2>
                  <p>This view will read durable events from the gateway in the chat slice.</p>
                </div>
              </div>
            )}
          </Match>
          <Match when={!props.selectedSession}>
            <div class="conversation-empty">
              <Icon name="state.empty" size={24} />
              <h2>Select a chat</h2>
              <p>Workspace chats are listed in the sidebar.</p>
            </div>
          </Match>
        </Switch>
      </Show>
    </section>
  );
}
