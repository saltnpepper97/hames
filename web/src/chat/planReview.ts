import type { HamesEvent } from "../api/types";

export interface ReviewPlan {
  id: string;
  title: string;
  status: "ready" | "reviewing" | "requested" | "approved" | "executing" | "needs_attention" | "completed" | "failed";
  error: string;
}

export function projectReviewPlan(events: readonly HamesEvent[]): ReviewPlan | undefined {
  let plan: ReviewPlan | undefined;
  for (const event of events) {
    if (event.type === "plan.proposed" && typeof event.payload.plan_id === "string") {
      plan = { id: event.payload.plan_id, title: String(event.payload.title || "Implementation plan"), status: "ready", error: "" };
      continue;
    }
    if (!plan) continue;
    if ((event.type === "plan.note.queued" || event.type === "plan.note.applied") && event.payload.plan_id === plan.id) {
      plan = { ...plan, status: "reviewing" };
      continue;
    }
    if (event.payload.plan_id !== plan.id) continue;
    const status = {
      "plan.execution.requested": "requested", "plan.approved": "approved",
      "plan.execution.started": "executing", "plan.execution.resumed": "executing",
      "plan.execution.attention": "needs_attention", "plan.execution.completed": "completed",
      "plan.execution.failed": "failed",
    }[event.type] as ReviewPlan["status"] | undefined;
    if (status) plan = { ...plan, status, error: typeof event.payload.message === "string" ? event.payload.message : "" };
  }
  return plan;
}
