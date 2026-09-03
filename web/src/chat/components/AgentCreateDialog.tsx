import { createMemo, createSignal } from "solid-js";
import { createAgent } from "../../api/client";
import type { AgentAuthority, AgentDetail } from "../../api/types";
import { Button } from "../../components/Button";
import { DialogFrame } from "../../components/DialogFrame";
import { TextField } from "../../components/FormField";
import { MarkdownEditor } from "../../components/MarkdownEditor";

interface AgentCreateDialogProps {
  onClose: () => void;
  onCreated: (agent: AgentDetail) => Promise<void> | void;
}

const agentIdPattern = /^[a-z][a-z0-9-]{0,62}$/;

function slugFromName(name: string): string {
  const slug = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
  return /^[a-z]/.test(slug) ? slug : `agent${slug ? `-${slug}` : ""}`.slice(0, 63);
}

function agentSource(
  id: string,
  name: string,
  authority: AgentAuthority,
  instructions: string,
): string {
  const metadata = JSON.stringify({ id, name: name.trim(), authority }, null, 2);
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
  const [name, setName] = createSignal("");
  const [slug, setSlug] = createSignal("");
  const [slugEdited, setSlugEdited] = createSignal(false);
  const [authority, setAuthority] = createSignal<AgentAuthority>("standard");
  const [instructions, setInstructions] = createSignal("");
  const [submitted, setSubmitted] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const nameError = createMemo(() => submitted() && !name().trim() ? "Enter a display name." : "");
  const slugError = createMemo(() => {
    if (!submitted()) return "";
    if (!slug().trim()) return "Enter an agent slug.";
    return agentIdPattern.test(slug())
      ? ""
      : "Use a lowercase letter first, then letters, numbers, or hyphens.";
  });

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setError("");
    if (!name().trim() || !agentIdPattern.test(slug())) return;
    setSaving(true);
    try {
      const created = await createAgent({
        name: name().trim(),
        authority: authority(),
        source: agentSource(slug(), name(), authority(), instructions()),
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
      eyebrow="New agent"
      title="Create an agent"
      class="agent-create-dialog"
      onClose={() => { if (!saving()) props.onClose(); }}
      footer={(
        <>
          <span class="agent-create-error" role="alert">{error()}</span>
          <Button disabled={saving()} onClick={props.onClose}>Cancel</Button>
          <Button variant="primary" loading={saving()} form="agent-create-form" type="submit">
            Create and use agent
          </Button>
        </>
      )}
    >
      <form id="agent-create-form" class="agent-create-form" onSubmit={(event) => void submit(event)}>
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
              if (!slugEdited()) setSlug(slugFromName(next));
            }}
          />
          <TextField
            label="Agent slug"
            value={slug()}
            maxlength={63}
            error={slugError()}
            helper="Permanent ID used by chats and history"
            placeholder="careful-reviewer"
            onInput={(event) => {
              setSlugEdited(true);
              setSlug(event.currentTarget.value.toLowerCase());
            }}
          />
        </div>
        <AuthorityPicker value={authority()} onChange={setAuthority} />
        <MarkdownEditor
          label="AGENT.md instructions"
          value={instructions()}
          rows={8}
          helper="Optional. Hames supplies a starter role when this is blank."
          onInput={(event) => setInstructions(event.currentTarget.value)}
        />
      </form>
    </DialogFrame>
  );
}
