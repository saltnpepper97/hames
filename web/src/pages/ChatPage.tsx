import { Show } from "solid-js";
import type { ConnectionState } from "../components/ConnectionStatus";
import { SessionList } from "../components/SessionList";
import type { GatewayHealth, Session } from "../api/types";

interface ChatPageProps {
  connection: ConnectionState;
  error: string;
  health?: GatewayHealth;
  sessions: Session[];
  onRetry: () => void;
}

export function ChatPage(props: ChatPageProps) {
  return (
    <section class="page chat-page" aria-labelledby="chat-title">
      <div class="page-heading">
        <div>
          <span class="eyebrow">Current workspace</span>
          <h1 id="chat-title">Conversations</h1>
          <p>Resume local work without losing its agent, model, or execution mode.</p>
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

      <Show when={props.connection === "offline" || props.error}>
        <div class="error-state" role="alert">
          <div>
            <span class="eyebrow">Gateway unavailable</span>
            <h2>Hames Web lost its local connection</h2>
            <p>{props.error || "The local gateway did not respond."}</p>
          </div>
          <button class="button" type="button" onClick={props.onRetry}>
            Retry connection
          </button>
        </div>
      </Show>

      <Show when={props.connection !== "connecting" && props.connection !== "offline"}>
        <SessionList sessions={props.sessions} />
      </Show>
    </section>
  );
}
