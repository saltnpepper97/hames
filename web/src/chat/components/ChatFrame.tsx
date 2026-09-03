import type { ParentProps } from "solid-js";

export function ChatFrame(props: ParentProps) {
  return <div class="session-chat">{props.children}</div>;
}
