import { Show } from "solid-js";
import { Checkbox } from "./Checkbox";

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
      <Checkbox
        class="selection-main"
        label={props.label}
        description={props.description}
        checked={props.selected}
        onCheckedChange={props.onSelected}
      />
      <Show when={props.pinAvailable && props.selected && props.onPinned}>
        <Checkbox
          class="selection-pin"
          label="Pin"
          checked={props.pinned}
          onCheckedChange={(pinned) => props.onPinned?.(pinned)}
        />
      </Show>
    </div>
  );
}
