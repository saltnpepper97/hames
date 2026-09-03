import { For, Show, createSignal } from "solid-js";
import type { SemanticIconName } from "../../shell/icons";
import { Icon } from "../../shell/icons";

export interface ComposerMenuOption {
  value: string;
  label: string;
  icon?: SemanticIconName;
}

interface ComposerMenuProps {
  ariaLabel: string;
  value: string;
  icon: SemanticIconName;
  options: readonly ComposerMenuOption[];
  disabled?: boolean;
  align?: "left" | "right";
  onSelect: (value: string) => Promise<void> | void;
}

export function ComposerMenu(props: ComposerMenuProps) {
  const [open, setOpen] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  let root!: HTMLDivElement;
  const selectedLabel = () =>
    props.options.find((option) => option.value === props.value)?.label ?? props.value;

  const select = async (value: string) => {
    if (busy() || value === props.value) {
      setOpen(false);
      return;
    }
    setBusy(true);
    try {
      await props.onSelect(value);
      setOpen(false);
    } catch {
      // The contributing control reports the gateway error through the composer.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      class="composer-menu"
      data-align={props.align ?? "left"}
      ref={root}
      onFocusOut={(event) => {
        if (!root.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        class="composer-chip"
        type="button"
        aria-label={props.ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open()}
        disabled={props.disabled || busy()}
        onClick={() => setOpen((current) => !current)}
      >
        <Icon name={props.icon} size={16} />
        <span>{selectedLabel()}</span>
        <Icon name="action.expand" size={13} />
      </button>
      <Show when={open()}>
        <div class="composer-menu-popover" role="menu" aria-label={props.ariaLabel}>
          <For each={props.options}>
            {(option) => (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={option.value === props.value}
                disabled={busy()}
                onClick={() => void select(option.value)}
              >
                <Show when={option.icon}>
                  {(icon) => <Icon name={icon()} size={16} />}
                </Show>
                <span>{option.label}</span>
              </button>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
