import { createSignal } from "solid-js";
import type { Session } from "../../api/types";
import { Button } from "../../components/Button";
import { DialogFrame } from "../../components/DialogFrame";

interface ChatDeleteDialogProps {
  session: Session;
  onClose: () => void;
  onDelete: (session: Session) => Promise<void>;
}

function sessionTitle(session: Session): string {
  return session.title?.trim() || "New chat";
}

export function ChatDeleteDialog(props: ChatDeleteDialogProps) {
  const [deleting, setDeleting] = createSignal(false);
  const [error, setError] = createSignal("");

  const remove = async () => {
    if (deleting()) return;
    setDeleting(true);
    setError("");
    try {
      await props.onDelete(props.session);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to delete chat");
      setDeleting(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="Delete chat"
      title={`Delete ${sessionTitle(props.session)}?`}
      class="chat-delete-dialog"
      onClose={() => { if (!deleting()) props.onClose(); }}
      footer={
        <>
          {error() && <span class="dialog-error" role="alert">{error()}</span>}
          <Button variant="quiet" disabled={deleting()} onClick={props.onClose}>Cancel</Button>
          <Button variant="destructive" loading={deleting()} onClick={() => void remove()}>
            Delete chat
          </Button>
        </>
      }
    >
      <div class="dialog-copy">
        <p>This removes the chat from your active list.</p>
        <p>Its local audit history is retained. Chats with an active run cannot be deleted.</p>
      </div>
    </DialogFrame>
  );
}
