import { Show, createMemo, createSignal, onCleanup } from "solid-js";
import type { Workspace } from "../api/types";
import { Button } from "../components/Button";
import { Select } from "../components/Select";
import { Icon } from "./icons";
import { WorkspaceAddDialog } from "./WorkspaceAddDialog";

const ADD_WORKSPACE = "__add_workspace__";

interface WorkspaceSwitcherProps {
  workspaces: readonly Workspace[];
  selected?: Workspace;
  disabled?: boolean;
  adding?: boolean;
  onSelect: (id: string) => Promise<void>;
  onAdd?: () => void;
}

export function WorkspaceSwitcher(props: WorkspaceSwitcherProps) {
  const [switching, setSwitching] = createSignal(false);
  const [error, setError] = createSignal("");
  const [picking, setPicking] = createSignal(false);
  let addTimer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => clearTimeout(addTimer));

  const openAdd = () => {
    setError("");
    if (props.onAdd) props.onAdd();
    else setPicking(true);
  };
  const options = createMemo(() => [...props.workspaces.map((workspace) => ({
    value: workspace.id,
    label: workspace.title,
    description: workspace.available ? workspace.path : `${workspace.path} · Folder unavailable`,
    disabled: !workspace.available,
  })), {
    value: ADD_WORKSPACE,
    label: "Add workspace",
    leading: <Icon name="action.add" size={15} />,
    disabled: Boolean(props.adding || picking()),
  }]);

  const select = async (id: string) => {
    if (switching()) return;
    if (id === ADD_WORKSPACE) {
      // Restore menu focus before opening the workspace dialog.
      clearTimeout(addTimer);
      addTimer = setTimeout(openAdd, 0);
      return;
    }
    setSwitching(true);
    setError("");
    try {
      await props.onSelect(id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to switch workspace");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div class="workspace-switcher-block">
      <Show
        when={props.workspaces.length > 0}
        fallback={(
          <Button
            variant="bare"
            class="workspace-empty-trigger"
            type="button"
            aria-label="Choose a workspace"
            loading={props.adding || picking()}
            disabled={props.disabled}
            onClick={openAdd}
          >
            <Icon name="action.folder" size={15} />
            <span>Choose workspace</span>
            <Icon name="action.next" size={13} />
          </Button>
        )}
      >
        <Select
          class="workspace-select"
          ariaLabel="Current workspace"
          value={props.selected?.id}
          options={options()}
          disabled={props.disabled || switching() || picking()}
          placeholder="Choose workspace"
          leading={<Icon name="action.folder" size={15} />}
          onValueChange={(id) => void select(id)}
        />
      </Show>
      <Show when={picking()}>
        <WorkspaceAddDialog initialPath={props.selected?.path} onClose={() => setPicking(false)}
          onAdded={(workspace) => props.onSelect(workspace.id)} />
      </Show>
      <Show when={error()}><span class="workspace-switcher-error" role="alert">{error()}</span></Show>
    </div>
  );
}
