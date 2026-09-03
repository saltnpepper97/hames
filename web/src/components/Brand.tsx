import { Icon } from "../shell/icons";

export function Brand() {
  return (
    <div class="brand" aria-label="Hames">
      <Icon name="brand.mark" class="brand-mark" size={19} />
      <span class="brand-name">Hames</span>
      <span class="brand-surface">Web</span>
    </div>
  );
}
