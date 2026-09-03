import { Icon } from "../shell/icons";
import { Show } from "solid-js";

interface BrandProps {
  compact?: boolean;
}

export function Brand(props: BrandProps) {
  return (
    <div class="brand" classList={{ compact: props.compact }} aria-label="Hames">
      <Icon name="brand.mark" class="brand-mark" size={19} />
      <Show when={!props.compact}>
        <span class="brand-name">Hames</span>
        <span class="brand-surface">Web</span>
      </Show>
    </div>
  );
}
