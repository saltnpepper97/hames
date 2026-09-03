import { For, Show } from "solid-js";
import type { Session } from "../api/types";

interface SessionListProps {
  sessions: Session[];
}

function sessionTitle(session: Session): string {
  return session.title?.trim() || "Untitled session";
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function SessionList(props: SessionListProps) {
  return (
    <Show
      when={props.sessions.length > 0}
      fallback={
        <div class="quiet-state">
          <span class="quiet-mark" aria-hidden="true">
            ◇
          </span>
          <h2>No resumable sessions here</h2>
          <p>
            Sessions created in this workspace will appear here. Chat controls arrive in
            the next vertical slice.
          </p>
        </div>
      }
    >
      <div class="session-list" aria-label="Workspace sessions">
        <For each={props.sessions}>
          {(session) => (
            <article class="session-row">
              <div class="session-primary">
                <span class="session-state" data-status={session.status} aria-hidden="true" />
                <div>
                  <h2>{sessionTitle(session)}</h2>
                  <p>
                    {session.agent_id} <span aria-hidden="true">·</span> {session.model}
                  </p>
                </div>
              </div>
              <div class="session-meta">
                <span>{session.interaction_mode}</span>
                <time dateTime={session.created_at}>{formatTime(session.created_at)}</time>
              </div>
            </article>
          )}
        </For>
      </div>
    </Show>
  );
}
