import { expect, it } from "vitest";
import type { HamesEvent } from "../api/types";
import { latestFlowScope } from "./flowScope";
const event = (type: string, run_id: string, payload = {}): HamesEvent => ({ id: `${type}-${run_id}`, type, run_id, payload } as HamesEvent);
it("retains the latest flow through completion and followups, replacing it only at a new flow marker", () => {
  const events = [event("flow.started", "first"), event("delegation.requested", "first"), event("run.completed", "first"), event("run.started", "followup")];
  expect(latestFlowScope(events)?.id).toBe("first");
  expect(latestFlowScope(events)?.runIds.has("followup")).toBe(true);
  events.push(event("flow.started", "second"));
  expect(latestFlowScope(events)?.id).toBe("second");
  expect(latestFlowScope(events)?.runIds.has("first")).toBe(false);
});
it("restores pre-marker recipe history and ignores ordinary later turns", () => {
  const events = [event("user.message", "", { content: "Flow recipe: Build & review\nTask" }), event("run.started", "first"), event("delegation.requested", "first"), event("user.message", "", { content: "What changed?" }), event("run.started", "followup")];
  expect(latestFlowScope(events)?.id).toBe("first");
  expect(latestFlowScope([])).toBeUndefined();
});
