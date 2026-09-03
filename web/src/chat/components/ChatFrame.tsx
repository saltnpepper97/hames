import { onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";

interface ChatFrameProps extends ParentProps {
  fresh?: boolean;
}

export function ChatFrame(props: ChatFrameProps) {
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
    <div class="session-chat" classList={{ fresh: props.fresh }} ref={frame}>
      {props.children}
    </div>
  );
}
