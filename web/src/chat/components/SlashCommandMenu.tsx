import { For, Show, createEffect, createUniqueId } from "solid-js";
import type { SlashCommand } from "../slashCommands";
import { Button } from "../../components/Button";
import { DropdownSurface } from "../../components/DropdownSurface";

interface SlashCommandMenuProps {
  open: boolean;
  commands: readonly SlashCommand[];
  selectedIndex: number;
  onSelectedIndexChange: (index: number) => void;
  onSelect: (command: SlashCommand) => void;
}

export function SlashCommandMenu(props: SlashCommandMenuProps) {
  const listboxId = `slash-commands-${createUniqueId()}`;

  createEffect(() => {
    if (!props.open) return;
    const selected = document.getElementById(`${listboxId}-${props.selectedIndex}`);
    selected?.scrollIntoView?.({ block: "nearest" });
  });

  return (
    <DropdownSurface
      open={props.open}
      class="slash-command-menu"
      role="listbox"
      ariaLabel="Slash commands"
    >
      <div class="slash-command-heading">
        <span>Commands</span>
        <kbd>↑↓</kbd><kbd>Enter</kbd>
      </div>
      <Show
        when={props.commands.length > 0}
        fallback={<p class="slash-command-empty">No matching commands</p>}
      >
        <div class="slash-command-options">
          <For each={props.commands}>{(command, index) => (
            <Button
              id={`${listboxId}-${index()}`}
              variant="bare"
              role="option"
              aria-selected={index() === props.selectedIndex}
              onMouseEnter={() => props.onSelectedIndexChange(index())}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => props.onSelect(command)}
            >
              <span class="slash-command-name">
                <strong>{command.value}</strong>
                <Show when={command.argumentHint}>{(hint) => <span>{hint()}</span>}</Show>
              </span>
              <span class="slash-command-detail">{command.detail}</span>
            </Button>
          )}</For>
        </div>
      </Show>
    </DropdownSurface>
  );
}
