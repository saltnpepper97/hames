import { Show, createSignal, type JSX } from "solid-js";
import { Icon } from "../shell/icons";
import { Button } from "./Button";
import { DeleteConfirmationDialog } from "./DeleteConfirmationDialog";

interface DeletableSidebarRowProps {
  children: JSX.Element;
  name: string;
  kind: string;
  eligible?: boolean;
  description: JSX.Element;
  onDelete: () => Promise<void>;
  verb?: "Delete" | "Remove";
}

/** Keeps the removal button beside the link, never nested inside navigation. */
export function DeletableSidebarRow(props: DeletableSidebarRowProps) {
  const [confirming, setConfirming] = createSignal(false);
  const verb = () => props.verb ?? "Delete";
  return <div class="deletable-sidebar-row" classList={{ "can-delete": props.eligible !== false }}>
    {props.children}
    <Show when={props.eligible !== false}>
      <Button variant="bare" class="sidebar-row-delete conversation-action conversation-delete-action"
        aria-label={`${verb()} ${props.name}`} title={`${verb()} ${props.kind}`}
        onClick={() => setConfirming(true)}>
        <Icon name="action.delete" size={14} />
      </Button>
    </Show>
    <Show when={confirming()}>
      <DeleteConfirmationDialog eyebrow={`${verb()} ${props.kind}`} title={`${verb()} ${props.name}?`}
        confirmLabel={`${verb()} ${props.kind}`} onClose={() => setConfirming(false)}
        onConfirm={async () => { await props.onDelete(); setConfirming(false); }}>
        {props.description}
      </DeleteConfirmationDialog>
    </Show>
  </div>;
}
