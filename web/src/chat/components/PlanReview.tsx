import { Show } from "solid-js";
import { Button } from "../../components/Button";
import type { ReviewPlan } from "../planReview";

export function PlanReview(props: {
  plan: ReviewPlan;
  canReview: boolean;
  busy: boolean;
  hasDraft: boolean;
  revising: boolean;
  onRevise: () => void;
  onExecute: () => void;
}) {
  const starting = () => ["requested", "approved", "executing"].includes(props.plan.status);
  const title = () => starting() ? "Starting plan execution…"
    : props.plan.status === "reviewing" ? "Plan changes requested"
    : props.plan.status === "failed" ? "Plan execution needs attention"
    : "Plan ready for review";
  return (
    <section class="plan-review" aria-label="Plan review">
      <div class="plan-review-copy">
        <strong role="status">{title()}</strong>
        <span>{props.plan.title}</span>
        <p>{starting() ? "Approved. Hames is continuing in Auto mode."
          : props.plan.error || (props.revising ? "Describe your changes below and send them to revise the plan."
          : props.plan.status === "reviewing" ? "Waiting for the revised plan before approval."
          : "Review the plan above. Execution starts only when you approve it.")}</p>
      </div>
      <Show when={props.canReview && !starting()}>
        <div class="plan-review-actions">
          <Button variant="quiet" size="sm" disabled={props.busy} onClick={props.onRevise}>Request changes</Button>
          <Button variant="primary" size="sm" loading={props.busy} disabled={props.busy || props.hasDraft || props.plan.status === "reviewing"} title={props.hasDraft ? "Send or clear your draft before executing the plan" : undefined} onClick={props.onExecute}>Execute plan</Button>
        </div>
      </Show>
    </section>
  );
}
