import { createSignal } from "solid-js";
import type { PluginView } from "../api/types";
import { removePlugin } from "../api/client";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";

interface PluginRemoveDialogProps {
  plugin: PluginView;
  onClose: () => void;
  onRemoved: () => void;
}

export function PluginRemoveDialog(props: PluginRemoveDialogProps) {
  const [removing, setRemoving] = createSignal(false);
  const [error, setError] = createSignal("");

  const remove = async () => {
    if (removing()) return;
    setRemoving(true);
    setError("");
    try {
      await removePlugin(props.plugin.id);
      props.onRemoved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to remove plugin");
      setRemoving(false);
    }
  };

  return (
    <DialogFrame
      eyebrow="Remove plugin"
      title={`Remove ${props.plugin.name}?`}
      class="plugin-remove-dialog"
      onClose={() => { if (!removing()) props.onClose(); }}
      footer={
        <>
          {error() && <span class="plugin-dialog-error" role="alert">{error()}</span>}
          <Button variant="quiet" disabled={removing()} onClick={props.onClose}>Cancel</Button>
          <Button class="plugin-remove-button" loading={removing()} onClick={() => void remove()}>Remove plugin</Button>
        </>
      }
    >
      <div class="plugin-remove-body">
        <p>This removes the installed package and its registry record from Hames.</p>
        <p>The managed copy at <code>{props.plugin.package_path}</code> will be removed. Files outside Hames's plugin directory are not modified.</p>
      </div>
    </DialogFrame>
  );
}
