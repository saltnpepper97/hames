import { Show, createSignal, createUniqueId } from "solid-js";
import { createWorkspace, selectDirectory } from "../api/client";
import type { Workspace } from "../api/types";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { TextField } from "../components/FormField";
import { Icon } from "./icons";

interface WorkspaceAddDialogProps {
  initialPath?: string;
  onClose: () => void;
  onAdded: (workspace: Workspace) => Promise<void>;
}

export function WorkspaceAddDialog(props: WorkspaceAddDialogProps) {
  const formId = createUniqueId();
  const [name, setName] = createSignal("");
  const [path, setPath] = createSignal("");
  const [picking, setPicking] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const busy = () => picking() || saving();
  const choose = async () => {
    if (busy()) return;
    setPicking(true);
    setError("");
    try {
      const selected = await selectDirectory(path() || props.initialPath);
      if (selected) setPath(selected.path);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to choose a folder");
    } finally {
      setPicking(false);
    }
  };
  const save = async () => {
    if (busy() || !name().trim() || !path()) return;
    setSaving(true);
    setError("");
    try {
      await props.onAdded(await createWorkspace(path(), name().trim()));
      props.onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to add workspace");
    } finally {
      setSaving(false);
    }
  };
  return (
    <DialogFrame title="Add workspace" eyebrow="Workspace" class="workspace-manage-dialog"
      onClose={() => { if (!busy()) props.onClose(); }}
      footer={<>
        <Show when={error()}><span class="dialog-error" role="alert">{error()}</span></Show>
        <Button variant="quiet" disabled={busy()} onClick={props.onClose}>Cancel</Button>
        <Button variant="primary" loading={saving()} disabled={busy() || !name().trim() || !path()}
          type="submit" form={formId}>Add workspace</Button>
      </>}
    >
      <form id={formId} class="workspace-manage-body" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <TextField label="Workspace name" required value={name()} maxlength={160} autofocus
          disabled={saving()} placeholder="My project" onInput={(event) => setName(event.currentTarget.value)} />
        <Button type="button" variant="quiet" loading={picking()} disabled={busy()} onClick={() => void choose()}>
          <Icon name="action.folder" size={16} />
          <span>{path() ? "Change folder" : "Choose folder"}</span>
        </Button>
        <div class="workspace-manage-path" role="status">
          <span>Folder</span>
          <code title={path()}>{path() || "No folder selected"}</code>
        </div>
      </form>
    </DialogFrame>
  );
}
