import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import type { SemanticIconName } from "../../shell/icons";
import { Icon } from "../../shell/icons";
import { Button } from "../../components/Button";
import { DropdownSurface } from "../../components/DropdownSurface";

export interface ComposerMenuOption {
  value: string;
  label: string;
  icon?: SemanticIconName;
}

interface ComposerMenuProps {
  ariaLabel: string;
  value: string;
  displayValue?: string;
  icon: SemanticIconName;
  options: readonly ComposerMenuOption[];
  disabled?: boolean;
  align?: "left" | "right";
  loading?: boolean;
  emptyMessage?: string;
  onOpen?: () => Promise<void> | void;
  onSelect: (value: string) => Promise<void> | void;
}

export function ComposerMenu(props: ComposerMenuProps) {
  const [open, setOpen] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  let root!: HTMLDivElement;
  const selectedLabel = () =>
    props.displayValue ??
    props.options.find((option) => option.value === props.value)?.label ??
    props.value;
  const close = () => setOpen(false);

  const toggle = () => {
    const next = !open();
    setOpen(next);
    if (next && props.onOpen) {
      void Promise.resolve(props.onOpen()).catch(() => undefined);
    }
  };

  const select = async (value: string) => {
    if (busy() || value === props.value) {
      close();
      return;
    }
    setBusy(true);
    try {
      await props.onSelect(value);
      close();
    } catch {
      // The contributing control reports the gateway error through the composer.
    } finally {
      setBusy(false);
    }
  };

  createEffect(() => {
    if (!open()) return;
    const closeOutside = (event: MouseEvent) => {
      if (!root.contains(event.target as Node)) close();
    };
    document.addEventListener("mousedown", closeOutside);
    onCleanup(() => document.removeEventListener("mousedown", closeOutside));
  });

  return (
    <div
      class="composer-menu"
      data-align={props.align ?? "left"}
      ref={root}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open()) return;
        event.preventDefault();
        close();
      }}
      onFocusOut={(event) => {
        if (!root.contains(event.relatedTarget as Node | null)) close();
      }}
    >
      <Button
        variant="bare"
        class="composer-chip"
        type="button"
        aria-label={props.ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open()}
        disabled={props.disabled || busy()}
        onClick={toggle}
      >
        <Icon name={props.icon} size={16} />
        <span>{selectedLabel()}</span>
        <Icon name="action.expand" size={13} />
      </Button>
      <DropdownSurface
        open={open()}
        class="composer-menu-popover"
        role="menu"
        ariaLabel={props.ariaLabel}
      >
        <Show when={!props.loading} fallback={<div class="composer-menu-state">Loading…</div>}>
          <For
            each={props.options}
            fallback={<div class="composer-menu-state">{props.emptyMessage ?? "No options"}</div>}
          >
            {(option) => (
              <Button
                variant="bare"
                role="menuitemradio"
                aria-checked={option.value === props.value}
                disabled={busy()}
                onClick={() => void select(option.value)}
              >
                <Show when={option.icon}>
                  {(icon) => <Icon name={icon()} size={16} />}
                </Show>
                <span>{option.label}</span>
              </Button>
            )}
          </For>
        </Show>
      </DropdownSurface>
    </div>
  );
}
