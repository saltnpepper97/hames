import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import { getDelegatedSessions } from "../../api/client";
import type { Session } from "../../api/types";
import type { DelegationNode } from "../projection";

export function DelegationChatLink(props: { node: DelegationNode }) {
  const [session, setSession] = createSignal<Session>();
  const [error, setError] = createSignal("");
  createEffect(() => {
    const parent = props.node.parentSessionId;
    const request = props.node.id;
    setSession(undefined);
    if (!parent) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const sessions = await getDelegatedSessions();
        if (disposed) return;
        const child = sessions.find(item => item.lineage_kind === "delegation"
          && item.parent_session_id === parent && item.fork_event_id === request);
        setSession(child);
        setError("");
        if (child) return;
      } catch {
        if (disposed) return;
        setError("The worker has a separate chat. Looking up its link…");
      }
      if (!disposed) timer = setTimeout(() => void load(), 3000);
    };
    void load();
    onCleanup(() => { disposed = true; clearTimeout(timer); });
  });
  return <div class="delegation-chat-link">
    <span>{props.node.status === "working" ? "Working in a separate chat." : props.node.status === "stopping" ? "Stopping work in the separate chat…" : props.node.status === "cancelled" ? "Work in the separate chat was cancelled." : "This work has a separate chat."}</span>
    <Show when={session()} fallback={<Show when={error()}><span role="status">{error()}</span></Show>}>
      {(child) => <a href={`/chat/${encodeURIComponent(child().id)}`}>Open chat</a>}
    </Show>
  </div>;
}
