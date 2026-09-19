import { onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";

interface ChatFrameProps extends ParentProps {
  fresh?: boolean;
}

export function ChatFrame(props: ChatFrameProps) {
  let frame!: HTMLDivElement;
  let observer: ResizeObserver | undefined;

  const syncHeight = (property: string, height: number) => {
    const value = `${height}px`;
    if (frame.style.getPropertyValue(property) !== value) frame.style.setProperty(property, value);
  };

  const syncComposerHeight = () => {
    const composer = frame.querySelector<HTMLElement>('[data-chat-region="composer"]');
    if (composer) syncHeight("--chat-composer-height", composer.offsetHeight);
    const input = frame.querySelector<HTMLElement>(".composer-stack");
    if (input) syncHeight("--chat-input-height", input.offsetHeight);
    const header = frame.querySelector<HTMLElement>(".chat-session-bar");
    if (header) syncHeight("--chat-session-header-height", header.offsetHeight);
  };

  onMount(() => {
    syncComposerHeight();
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(syncComposerHeight);
      const composer = frame.querySelector<HTMLElement>('[data-chat-region="composer"]');
      if (composer) observer.observe(composer);
      const input = frame.querySelector<HTMLElement>(".composer-stack");
      if (input) observer.observe(input);
      const header = frame.querySelector<HTMLElement>(".chat-session-bar");
      if (header) observer.observe(header);
    }
    window.addEventListener("resize", syncComposerHeight);
  });
  onCleanup(() => {
    observer?.disconnect();
    window.removeEventListener("resize", syncComposerHeight);
  });

  return (
    <div class="session-chat" classList={{ fresh: props.fresh }} ref={frame}>
      {props.children}
    </div>
  );
}
