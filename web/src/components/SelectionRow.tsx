import { Show } from "solid-js";

interface SelectionRowProps {
  label: string;
  description?: string;
  selected: boolean;
  pinned?: boolean;
  pinAvailable?: boolean;
  onSelected: (selected: boolean) => void;
  onPinned?: (pinned: boolean) => void;
}

export function SelectionRow(props: SelectionRowProps) {
  return (
    <div class="selection-row">
      <label class="selection-main">
        <input
          type="checkbox"
          checked={props.selected}
          onChange={(event) => props.onSelected(event.currentTarget.checked)}
        />
        <span class="selection-check" aria-hidden="true" />
        <span>
          <strong>{props.label}</strong>
          <Show when={props.description}><small>{props.description}</small></Show>
        </span>
      </label>
      <Show when={props.pinAvailable && props.selected && props.onPinned}>
        <label class="selection-pin">
          <input
            type="checkbox"
            checked={props.pinned}
            onChange={(event) => props.onPinned?.(event.currentTarget.checked)}
          />
          <span>Pin</span>
        </label>
      </Show>
    </div>
  );
}
