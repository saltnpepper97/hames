import { Button } from "../../components/Button";
import { Icon } from "../../shell/icons";
import { For, Show } from "solid-js";
import { Dynamic } from "solid-js/web";
import { DropdownSurface } from "../../components/DropdownSurface";
import { useWebPlugins } from "../../shell/pluginContext";

interface ComposerActionMenuProps {
  open: boolean;
  disabled: boolean;
  onUpload: () => void;
  onCommands: () => void;
  onResumePlan?: () => void;
  resumeDisabled?: boolean;
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
      <Show when={props.onResumePlan}>
        <Button variant="bare" type="button" role="menuitem" disabled={props.disabled || props.resumeDisabled}
          onClick={props.onResumePlan}><span class="composer-action-icon"><Icon name="conversation.tasks" size={17} /></span><span><strong>Resume plan execution</strong></span></Button>
      </Show>
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
