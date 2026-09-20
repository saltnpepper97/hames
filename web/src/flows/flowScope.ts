import type { HamesEvent } from "../api/types";
export function latestFlowScope(events: readonly HamesEvent[]) {
  let start = -1;
  let pendingLegacyStart = false;
  let marked = false;
  for (let i = 0; i < events.length; i++) {
    const event = events[i]!;
    if (event.type === "flow.started") { start = i; marked = true; }
    // Older recipe invocations predate the durable start marker.
    if (!marked && event.type === "user.message") {
      pendingLegacyStart = String(event.payload.content ?? "").startsWith("Flow recipe:");
    }
    if (!marked && event.type === "run.started" && pendingLegacyStart) {
      start = i; pendingLegacyStart = false;
    }
  }
  if (start < 0) {
    const last = [...events].reverse().find(event => event.type === "delegation.requested");
    if (last) start = events.findIndex(event => event.run_id === last.run_id);
  }
  if (start < 0) return undefined;
  return { id: events[start]!.run_id ?? events[start]!.id,
    runIds: new Set(events.slice(start).map(event => event.run_id).filter((id): id is string => !!id)) };
}
