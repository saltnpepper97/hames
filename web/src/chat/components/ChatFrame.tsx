import { onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";

export function ChatFrame(props: ParentProps) {
  let frame!: HTMLDivElement;
  let observer: ResizeObserver | undefined;

  const syncComposerHeight = () => {
    const composer = frame.querySelector<HTMLElement>('[data-chat-region="composer"]');
    if (composer) frame.style.setProperty("--chat-composer-height", `${composer.offsetHeight}px`);
  };

  onMount(() => {
    syncComposerHeight();
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(syncComposerHeight);
      const composer = frame.querySelector<HTMLElement>('[data-chat-region="composer"]');
      if (composer) observer.observe(composer);
    }
    window.addEventListener("resize", syncComposerHeight);
  });
  onCleanup(() => {
    observer?.disconnect();
    window.removeEventListener("resize", syncComposerHeight);
  });

  return (
    <div class="session-chat" ref={frame}>
      {props.children}
    </div>
  );
}
