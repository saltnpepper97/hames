import { Show, createMemo, createSignal } from "solid-js";
import { AgentDefaultModel } from "../../agents/AgentDefaultModel";
import { createAgent } from "../../api/client";
import type { AgentAuthority, AgentDetail } from "../../api/types";
import { Button } from "../../components/Button";
import { DialogFrame } from "../../components/DialogFrame";
import { TextField } from "../../components/FormField";
import { MarkdownEditor } from "../../components/MarkdownEditor";

interface AgentCreateDialogProps {
  onClose: () => void;
  onCreated: (agent: AgentDetail) => Promise<void> | void;
  submitLabel?: string;
}

function agentSource(
  name: string,
  authority: AgentAuthority,
  instructions: string,
  defaultModel: AgentDetail["default_model"],
): string {
  const metadata = JSON.stringify({ name: name.trim(), authority, ...(defaultModel ? { default_model: defaultModel } : {}) }, null, 2);
  return `---\n${metadata}\n---\n${instructions.trim()}\n`;
}

function creationError(error: unknown): string {
  return error instanceof Error ? error.message : "Hames could not create this agent.";
}

interface AuthorityPickerProps {
  value: AgentAuthority;
  onChange: (value: AgentAuthority) => void;
}

function AuthorityPicker(props: AuthorityPickerProps) {
  return (
    <fieldset class="agent-authority-picker">
      <legend class="form-label">Authority</legend>
      <div>
        <Button
          variant="choice"
          aria-pressed={props.value === "standard"}
          onClick={() => props.onChange("standard")}
        >
          <strong>Standard</strong>
          <span>Normal workspace tools, subject to policy.</span>
        </Button>
        <Button
          variant="choice"
          aria-pressed={props.value === "read_only"}
          onClick={() => props.onChange("read_only")}
        >
          <strong>Read only</strong>
          <span>Inspection and research without writes.</span>
        </Button>
      </div>
    </fieldset>
  );
}

export function AgentCreateDialog(props: AgentCreateDialogProps) {
  const [step, setStep] = createSignal<"details" | "model">("details");
  const [defaultModel, setDefaultModel] = createSignal<AgentDetail["default_model"]>(null);
  const [name, setName] = createSignal("");
  const [authority, setAuthority] = createSignal<AgentAuthority>("standard");
  const [instructions, setInstructions] = createSignal("");
  const [submitted, setSubmitted] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const nameError = createMemo(() => submitted() && !name().trim() ? "Enter a display name." : "");
  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setError("");
    if (!name().trim()) return;
    if (saving()) return;
    if (step() === "details") { setStep("model"); return; }
    setSaving(true);
    try {
      const created = await createAgent({
        name: name().trim(),
        authority: authority(),
        source: agentSource(name(), authority(), instructions(), defaultModel()),
      });
      await props.onCreated(created);
    } catch (caught) {
      setError(creationError(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <DialogFrame
      eyebrow={step() === "details" ? "New agent · 1 of 2 · Details" : "New agent · 2 of 2 · Model"}
      title="Create an agent"
      class="agent-create-dialog"
      onClose={() => { if (!saving()) props.onClose(); }}
      footer={(
        <>
          <span class="agent-create-error" role="alert">{error()}</span>
          <Button disabled={saving()} onClick={props.onClose}>Cancel</Button>
          <Show when={step() === "model"}>
            <Button disabled={saving()} onClick={() => setStep("details")}>Back</Button>
          </Show>
          <Button variant="primary" loading={saving()} form="agent-create-form" type="submit">
            {step() === "details" ? "Next" : props.submitLabel ?? "Create and use agent"}
          </Button>
        </>
      )}
    >
      <form id="agent-create-form" class="agent-create-form" onSubmit={(event) => void submit(event)}>
        <Show when={step() === "details"}>
        <div class="agent-create-identity">
          <TextField
            label="Display name"
            value={name()}
            maxlength={80}
            autofocus
            error={nameError()}
            placeholder="Careful reviewer"
            onInput={(event) => {
              const next = event.currentTarget.value;
              setName(next);
            }}
          />
        </div>
        <AuthorityPicker value={authority()} onChange={setAuthority} />
        <MarkdownEditor
          label="AGENT.md instructions"
          value={instructions()}
          rows={8}
          resizable={false}
          helper="Optional. Hames supplies a starter role when this is blank."
          onInput={(event) => setInstructions(event.currentTarget.value)}
        />
        </Show>
        <Show when={step() === "model"}>
          <div>
            <h3>Choose a default model</h3>
            <p>Pick the model and effort for this agent, or let it use the chat’s model. You can change this later.</p>
          </div>
          <AgentDefaultModel value={defaultModel()} onChange={setDefaultModel} />
        </Show>
      </form>
    </DialogFrame>
  );
}
