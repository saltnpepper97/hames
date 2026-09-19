import { createSignal } from "solid-js";
import type { JSX } from "solid-js";
import { Button } from "./Button";
import { DialogFrame } from "./DialogFrame";

interface DeleteConfirmationDialogProps {
  eyebrow: string;
  title: string;
  confirmLabel: string;
  children: JSX.Element;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}

export function DeleteConfirmationDialog(props: DeleteConfirmationDialogProps) {
  const [deleting, setDeleting] = createSignal(false);
  const [error, setError] = createSignal("");

  const confirm = async () => {
    if (deleting()) return;
    setDeleting(true);
    setError("");
    try {
      await props.onConfirm();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Unable to ${props.confirmLabel.toLowerCase()}`);
      setDeleting(false);
    }
  };

  return (
    <DialogFrame
      eyebrow={props.eyebrow}
      title={props.title}
      class="delete-confirmation-dialog"
      onClose={() => { if (!deleting()) props.onClose(); }}
      footer={
        <>
          {error() && <span class="dialog-error" role="alert">{error()}</span>}
          <Button variant="quiet" disabled={deleting()} onClick={props.onClose}>Cancel</Button>
          <Button variant="destructive" loading={deleting()} onClick={() => void confirm()}>
            {props.confirmLabel}
          </Button>
        </>
      }
    >
      <div class="dialog-copy">{props.children}</div>
    </DialogFrame>
  );
}
