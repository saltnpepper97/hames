import { createMemo, createSignal } from "solid-js";
import { createMemory } from "../api/client";
import type { MemoryCreate, MemoryLayer, MemoryRecord, MemoryVisibility } from "../api/types";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { SelectField, TextAreaField, TextField } from "../components/FormField";

interface MemoryCreateDialogProps {
  sessionId: string;
  onClose: () => void;
  onCreated: (record: MemoryRecord) => void;
}

const layerOptions: readonly { value: MemoryLayer; label: string; description: string }[] = [
  { value: "relationship", label: "Relationship", description: "Preferences and durable user context." },
  { value: "semantic", label: "Semantic fact", description: "Reusable knowledge about the work." },
  { value: "episodic", label: "Episode", description: "A specific event or outcome." },
];

const visibilityOptions: readonly { value: MemoryVisibility; label: string; description: string }[] = [
  { value: "workspace", label: "This workspace", description: "Available while working in this project." },
  { value: "global", label: "All workspaces", description: "Available everywhere in Hames." },
  { value: "agent_private", label: "Current agent only", description: "Private to the selected agent." },
  { value: "session_team", label: "This session team", description: "Shared only with this session's agents." },
];

export function MemoryCreateDialog(props: MemoryCreateDialogProps) {
  const [layer, setLayer] = createSignal<MemoryLayer>("relationship");
  const [visibility, setVisibility] = createSignal<MemoryVisibility>("workspace");
  const [subject, setSubject] = createSignal("");
  const [predicate, setPredicate] = createSignal("");
  const [value, setValue] = createSignal("");
  const [summary, setSummary] = createSignal("");
  const [creating, setCreating] = createSignal(false);
  const [error, setError] = createSignal("");
  const complete = createMemo(() =>
    Boolean(subject().trim() && predicate().trim() && value().trim() && summary().trim())
  );

  const create = async () => {
    if (creating() || !complete()) return;
    setCreating(true);
    setError("");
    const memory: MemoryCreate = {
      layer: layer(),
      visibility: visibility(),
      subject: subject().trim(),
      predicate: predicate().trim(),
      value: value().trim(),
      summary: summary().trim(),
    };
    try {
      props.onCreated(await createMemory(props.sessionId, memory));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create memory");
      setCreating(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="New memory"
      title="Add something Hames should remember"
      class="memory-create-dialog"
      onClose={() => { if (!creating()) props.onClose(); }}
      footer={
        <>
          {error() && <span class="dialog-error" role="alert">{error()}</span>}
          <Button variant="quiet" disabled={creating()} onClick={props.onClose}>Cancel</Button>
          <Button variant="primary" loading={creating()} disabled={!complete()} onClick={() => void create()}>
            Add memory
          </Button>
        </>
      }
    >
      <div class="memory-create-form">
        <div class="memory-create-grid">
          <SelectField
            label="Memory type"
            helper="Relationship, reusable knowledge, or a specific episode."
            value={layer()}
            options={layerOptions}
            onValueChange={(value) => setLayer(value as MemoryLayer)}
          />
          <SelectField
            label="Visible to"
            helper="Choose the narrowest useful scope."
            value={visibility()}
            options={visibilityOptions}
            onValueChange={(value) => setVisibility(value as MemoryVisibility)}
          />
        </div>
        <div class="memory-create-grid">
          <TextField label="Subject" value={subject()} maxlength={300} placeholder="user:local, project:hames, run outcome…" onInput={(event) => setSubject(event.currentTarget.value)} />
          <TextField label="Relationship or fact" value={predicate()} maxlength={120} placeholder="prefers_review_style" helper="A short durable label." onInput={(event) => setPredicate(event.currentTarget.value)} />
        </div>
        <TextAreaField label="What should Hames remember?" value={value()} maxlength={32000} rows={5} resizable={false} placeholder="Write the durable detail in plain language." onInput={(event) => setValue(event.currentTarget.value)} />
        <TextField label="Short summary" value={summary()} maxlength={2000} placeholder="One sentence shown in the Memory sidebar." onInput={(event) => setSummary(event.currentTarget.value)} />
      </div>
    </DialogFrame>
  );
}
