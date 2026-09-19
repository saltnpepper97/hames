import { createMemo, createSignal } from "solid-js";
import { createScar } from "../api/client";
import type { Scar, ScarCreate, ScarScope, ScarSeverity } from "../api/types";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { SelectField, TextAreaField, TextField } from "../components/FormField";

interface ScarCreateDialogProps {
  sessionId: string;
  onClose: () => void;
  onCreated: (scar: Scar) => void;
}

const severityOptions: readonly { value: ScarSeverity; label: string; description: string }[] = [
  { value: "low", label: "Low", description: "Annoying, but easy to recover from." },
  { value: "medium", label: "Medium", description: "Meaningful rework or an unreliable result." },
  { value: "high", label: "High", description: "Destructive, unsafe, or costly to repeat." },
];

const scopeOptions: readonly { value: ScarScope; label: string; description: string }[] = [
  { value: "workspace", label: "This workspace", description: "Protect work in this project." },
  { value: "agent", label: "Current agent", description: "Apply only to the selected agent." },
  { value: "global", label: "All workspaces", description: "Protect every Hames workspace." },
];

export function ScarCreateDialog(props: ScarCreateDialogProps) {
  const [title, setTitle] = createSignal("");
  const [severity, setSeverity] = createSignal<ScarSeverity>("medium");
  const [scope, setScope] = createSignal<ScarScope>("workspace");
  const [signature, setSignature] = createSignal("");
  const [description, setDescription] = createSignal("");
  const [expectedBehavior, setExpectedBehavior] = createSignal("");
  const [creating, setCreating] = createSignal(false);
  const [error, setError] = createSignal("");
  const complete = createMemo(() => Boolean(
    title().trim()
    && signature().trim()
    && description().trim()
    && expectedBehavior().trim()
  ));

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    if (creating() || !complete()) return;
    setCreating(true);
    setError("");
    const request: ScarCreate = {
      title: title().trim(),
      severity: severity(),
      scope: scope(),
      failure_signature: signature().trim(),
      description: description().trim(),
      expected_behavior: expectedBehavior().trim(),
    };
    try {
      props.onCreated(await createScar(props.sessionId, request));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create Scar");
      setCreating(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="New Scar"
      title="Create a Scar"
      class="scar-create-dialog"
      onClose={() => { if (!creating()) props.onClose(); }}
      footer={(
        <>
          {error() && <span class="dialog-error" role="alert">{error()}</span>}
          <Button variant="quiet" disabled={creating()} onClick={props.onClose}>Cancel</Button>
          <Button
            variant="primary"
            type="submit"
            form="scar-create-form"
            loading={creating()}
            disabled={!complete()}
          >
            Create Scar
          </Button>
        </>
      )}
    >
      <form id="scar-create-form" class="scar-create-form" onSubmit={(event) => void submit(event)}>
        <TextField
          label="What went wrong?"
          value={title()}
          maxlength={300}
          autofocus
          placeholder="The final answer claimed success before verification"
          onInput={(event) => setTitle(event.currentTarget.value)}
        />
        <div class="scar-create-grid">
          <SelectField
            label="Severity"
            helper="How costly is this behavior when it recurs?"
            value={severity()}
            options={severityOptions}
            onValueChange={(value) => setSeverity(value as ScarSeverity)}
          />
          <SelectField
            label="Applies to"
            helper="Choose the narrowest place that needs protection."
            value={scope()}
            options={scopeOptions}
            onValueChange={(value) => setScope(value as ScarScope)}
          />
        </div>
        <TextField
          label="Recognition cue"
          value={signature()}
          maxlength={1000}
          helper="A stable description Hames can use to recognize the same failure again."
          placeholder="Reports completion without checking the rendered result"
          onInput={(event) => setSignature(event.currentTarget.value)}
        />
        <TextAreaField
          label="What happened?"
          value={description()}
          maxlength={4000}
          rows={4}
          resizable={false}
          placeholder="Describe the incorrect behavior and why it matters."
          onInput={(event) => setDescription(event.currentTarget.value)}
        />
        <TextAreaField
          label="What should happen instead?"
          value={expectedBehavior()}
          maxlength={4000}
          rows={4}
          resizable={false}
          placeholder="State the behavior Hames should follow next time."
          onInput={(event) => setExpectedBehavior(event.currentTarget.value)}
        />
      </form>
    </DialogFrame>
  );
}
