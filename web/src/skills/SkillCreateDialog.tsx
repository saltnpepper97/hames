import { createMemo, createSignal } from "solid-js";
import type { SkillJob } from "../api/types";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { TextAreaField } from "../components/FormField";

interface SkillCreateDialogProps {
  onClose: () => void;
  onCreate: (goal: string, scope: "workspace" | "agent") => Promise<SkillJob>;
  onQueued: (job: SkillJob) => void;
}

function creationError(error: unknown): string {
  return error instanceof Error ? error.message : "Hames could not start Skill authoring.";
}

export function SkillCreateDialog(props: SkillCreateDialogProps) {
  const [goal, setGoal] = createSignal("");
  const [scope, setScope] = createSignal<"workspace" | "agent">("workspace");
  const [submitted, setSubmitted] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const goalError = createMemo(() => submitted() && !goal().trim()
    ? "Describe the repeatable work this Skill should perform."
    : "");

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setError("");
    const authoringGoal = goal().trim();
    if (!authoringGoal) return;
    setSaving(true);
    try {
      const job = await props.onCreate(authoringGoal, scope());
      props.onQueued(job);
    } catch (caught) {
      setError(creationError(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="New Skill"
      title="Create a Skill"
      class="skill-create-dialog"
      onClose={() => { if (!saving()) props.onClose(); }}
      footer={(
        <>
          <span class="skill-create-error" role="alert">{error()}</span>
          <Button disabled={saving()} onClick={props.onClose}>Cancel</Button>
          <Button variant="primary" loading={saving()} form="skill-create-form" type="submit">
            Start authoring
          </Button>
        </>
      )}
    >
      <form id="skill-create-form" class="skill-create-form" onSubmit={(event) => void submit(event)}>
        <TextAreaField
          label="What should this Skill do?"
          value={goal()}
          maxlength={4000}
          rows={7}
          resizable={false}
          autofocus
          error={goalError()}
          helper="Describe a repeatable outcome, important constraints, and how success should be checked."
          placeholder="Review a pull request, run the project checks, and summarize only actionable findings…"
          onInput={(event) => setGoal(event.currentTarget.value)}
        />

        <fieldset class="skill-scope-picker">
          <legend class="form-label">Availability</legend>
          <div>
            <Button
              variant="choice"
              aria-pressed={scope() === "workspace"}
              onClick={() => setScope("workspace")}
            >
              <strong>This workspace</strong>
              <span>Available to agents working in this project.</span>
            </Button>
            <Button
              variant="choice"
              aria-pressed={scope() === "agent"}
              onClick={() => setScope("agent")}
            >
              <strong>Current agent</strong>
              <span>Private to the agent attached to the authoring session.</span>
            </Button>
          </div>
        </fieldset>
        <p class="skill-authoring-note">
          Hames will draft, validate, and evaluate the Skill in the background before activating it.
        </p>
      </form>
    </DialogFrame>
  );
}
