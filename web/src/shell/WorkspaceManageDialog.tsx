import { Show, createMemo, createSignal } from "solid-js";
import type { Workspace } from "../api/types";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { TextField } from "../components/FormField";

interface WorkspaceManageDialogProps {
  workspace: Workspace;
  canRemove: boolean;
  initiallyConfirmingRemoval?: boolean;
  onClose: () => void;
  onRename: (title: string) => Promise<void>;
  onRemove: () => Promise<void>;
}

export function WorkspaceManageDialog(props: WorkspaceManageDialogProps) {
  const [title, setTitle] = createSignal(props.workspace.title);
  const [saving, setSaving] = createSignal(false);
  const [confirmingRemoval, setConfirmingRemoval] = createSignal(
    Boolean(props.initiallyConfirmingRemoval),
  );
  const [error, setError] = createSignal("");
  const changed = createMemo(() =>
    Boolean(title().trim() && title().trim() !== props.workspace.title)
  );

  const rename = async () => {
    if (!changed() || saving()) return;
    setSaving(true);
    setError("");
    try {
      await props.onRename(title().trim());
      props.onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to rename workspace");
      setSaving(false);
    }
  };

  const remove = async () => {
    if (saving()) return;
    setSaving(true);
    setError("");
    try {
      await props.onRemove();
      props.onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to remove workspace");
      setSaving(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="Workspace"
      title={confirmingRemoval() ? `Remove ${props.workspace.title}?` : "Workspace details"}
      class="workspace-manage-dialog"
      onClose={() => { if (!saving()) props.onClose(); }}
      footer={
        <>
          <Show when={error()}><span class="dialog-error" role="alert">{error()}</span></Show>
          <Show when={!confirmingRemoval() && props.canRemove}>
            <Button
              variant="destructive"
              class="workspace-remove-button"
              disabled={saving()}
              onClick={() => setConfirmingRemoval(true)}
            >
              Remove from Hames
            </Button>
          </Show>
          <Button
            variant="quiet"
            disabled={saving()}
            onClick={() => confirmingRemoval() ? setConfirmingRemoval(false) : props.onClose()}
          >
            {confirmingRemoval() ? "Go back" : "Cancel"}
          </Button>
          <Show
            when={confirmingRemoval()}
            fallback={
              <Button variant="primary" loading={saving()} disabled={!changed()} onClick={() => void rename()}>
                Save name
              </Button>
            }
          >
            <Button variant="destructive" loading={saving()} onClick={() => void remove()}>
              Remove workspace
            </Button>
          </Show>
        </>
      }
    >
      <Show when={!confirmingRemoval()} fallback={
        <div class="workspace-manage-body dialog-copy">
          <p>This only removes the workspace from Hames.</p>
          <p>The folder, its files, and every chat transcript remain on disk.</p>
        </div>
      }>
        <div class="workspace-manage-body">
          <TextField
            label="Display name"
            value={title()}
            maxlength={160}
            onInput={(event) => setTitle(event.currentTarget.value)}
          />
          <div class="workspace-manage-path">
            <span>Folder</span>
            <code title={props.workspace.path}>{props.workspace.path}</code>
          </div>
          <Show when={!props.canRemove}>
            <p class="workspace-manage-note">Add another workspace before removing this one.</p>
          </Show>
        </div>
      </Show>
    </DialogFrame>
  );
}
