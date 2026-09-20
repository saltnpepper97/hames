import { createEffect, createMemo, createSignal, onCleanup, on, type Accessor } from "solid-js";
import type { Session } from "../api/types";
import { getDelegatedSessions } from "../api/client";
import type { DelegationNode } from "../chat/projection";
export function createCurrentWorkerSessions(parentId: Accessor<string>, workers: Accessor<DelegationNode[]>) {
  const [sessions, setSessions] = createSignal<Session[]>([]);
  const identity = createMemo(() => `${parentId()}:${workers().map(worker => worker.id).join(",")}`);
  createEffect(on(identity, () => {
    const parent = parentId();
    const ids = new Set(workers().map(worker => worker.id));
    setSessions(previous => previous.filter(session => session.parent_session_id === parent && ids.has(session.fork_event_id ?? "")));
    if (!ids.size) return;
    let disposed = false;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await getDelegatedSessions();
        if (!disposed) setSessions(result.filter(session => session.parent_session_id === parent &&
          session.lineage_kind === "delegation" && ids.has(session.fork_event_id ?? "")));
      } catch { /* Retry while the run is visible; never substitute historical workers. */ }
      finally { loading = false; }
    };
    void load();
    const timer = setInterval(() => { if (sessions().length < ids.size) void load(); }, 1500);
    onCleanup(() => { disposed = true; clearInterval(timer); });
  }));
  return sessions;
}
