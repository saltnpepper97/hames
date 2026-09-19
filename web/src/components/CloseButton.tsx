import { splitProps } from "solid-js";
import type { ButtonProps } from "./Button";
import { Button } from "./Button";
import { Icon } from "../shell/icons";

interface CloseButtonProps extends Omit<ButtonProps, "children" | "start" | "end" | "variant"> {
  iconSize?: number;
}

export function CloseButton(props: CloseButtonProps) {
  const [local, buttonProps] = splitProps(props, ["class", "iconSize"]);

  return (
    <Button
      {...buttonProps}
      variant="bare"
      class={`ui-close-button ${local.class ?? ""}`}
    >
      <Icon name="action.close" size={local.iconSize ?? 16} />
    </Button>
  );
}
