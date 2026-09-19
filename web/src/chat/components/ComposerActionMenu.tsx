import { For } from "solid-js";
import { Dynamic } from "solid-js/web";
import { DropdownSurface } from "../../components/DropdownSurface";
import { useWebPlugins } from "../../shell/pluginContext";

interface ComposerActionMenuProps {
  open: boolean;
  disabled: boolean;
  onUpload: () => void;
  onCommands: () => void;
}

export function ComposerActionMenu(props: ComposerActionMenuProps) {
  const plugins = useWebPlugins();
  return (
    <DropdownSurface
      open={props.open}
      class="composer-action-menu"
      role="menu"
      ariaLabel="Add to message"
    >
      <div class="composer-action-menu-heading">Add to message</div>
      <For each={plugins.composerActions}>{(action) => (
        <Dynamic
          component={action.component}
          disabled={props.disabled}
          onUpload={props.onUpload}
          onCommands={props.onCommands}
        />
      )}</For>
    </DropdownSurface>
  );
}
