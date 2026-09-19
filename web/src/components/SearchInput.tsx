import type { JSX } from "solid-js";
import { Icon } from "../shell/icons";
import { CloseButton } from "./CloseButton";

interface SearchInputProps
  extends Omit<
    JSX.InputHTMLAttributes<HTMLInputElement>,
    "onInput" | "onKeyDown" | "ref" | "type" | "value"
  > {
  value: string;
  onValueChange: (value: string) => void;
  onDismiss: () => void;
  elementRef?: (element: HTMLInputElement) => void;
  dismissLabel?: string;
  inactive?: boolean;
}

export function SearchInput(props: SearchInputProps) {
  return (
    <div class="ui-search-input" data-component="search-input">
      <Icon name="action.search" size={16} class="ui-search-input-icon" />
      <input
        ref={props.elementRef}
        type="search"
        aria-label={props["aria-label"] ?? "Search"}
        placeholder={props.placeholder ?? "Search…"}
        value={props.value}
        autocomplete={props.autocomplete ?? "off"}
        spellcheck={props.spellcheck ?? false}
        tabIndex={props.inactive ? -1 : props.tabIndex}
        onInput={(event) => props.onValueChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          props.onDismiss();
        }}
      />
      <CloseButton
        class="ui-search-input-dismiss"
        type="button"
        aria-label={props.dismissLabel ?? "Close search"}
        title={props.dismissLabel ?? "Close search"}
        tabIndex={props.inactive ? -1 : undefined}
        onClick={props.onDismiss}
        iconSize={14}
      />
    </div>
  );
}
