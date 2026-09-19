import { ReasoningDisclosureProvider } from "../reasoningDisclosure";
import { Dynamic } from "solid-js/web";
import { For, Show, createEffect, createMemo, onCleanup, onMount } from "solid-js";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import type { ConversationNode } from "../projection";
import type { StreamState } from "../sessionStream";
import { useWebPlugins } from "../../shell/pluginContext";
import { FreshChatHero } from "./FreshChatHero";

interface ConversationViewportProps {
  sessionId?: string;
  nodes: readonly ConversationNode[];
  streamState: StreamState;
  fresh?: boolean;
  agentId: string;
}

export function ConversationViewport(props: ConversationViewportProps) {
  const plugins = useWebPlugins();
  const agents = useAgentDirectory();
  let transcript!: HTMLDivElement;
  const storageKey = props.sessionId ? `hames.transcript-position:${props.sessionId}` : undefined;
  let saved: { top: number; follow: boolean } | undefined;
  try {
    const value = storageKey ? JSON.parse(sessionStorage.getItem(storageKey) || "null") : null;
    if (value && Number.isFinite(value.top) && value.top >= 0 && typeof value.follow === "boolean") saved = value;
  } catch { /* Browser storage may be unavailable. */ }
  let restoringTop = saved && !saved.follow ? saved.top : undefined;
  let stickToBottom = saved?.follow ?? true;
  const savePosition = () => {
    if (!storageKey || !transcript || transcript.clientHeight === 0 || !props.nodes.length) return;
    const position = { top: restoringTop ?? transcript.scrollTop, follow: stickToBottom };
    try { sessionStorage.setItem(storageKey, JSON.stringify(position)); } catch { /* Optional UI state. */ }
  };
  let disposed = false;
  let pointerHeld = false;
  let expectedTop: number | undefined;
  const pauseFollow = () => {
    restoringTop = undefined;
    stickToBottom = false;
    expectedTop = undefined;
  };
  let resizeObserver: ResizeObserver | undefined;

  const selectingText = () => {
    const selection = window.getSelection();
    return !!selection && !selection.isCollapsed &&
      (transcript.contains(selection.anchorNode) || transcript.contains(selection.focusNode));
  };
  const followBottom = () => {
    if (disposed || pointerHeld || !transcript || !transcript.clientHeight || selectingText()) return;
    if (restoringTop !== undefined) {
      // Replay arrives in batches. Keep the target until enough content is laid
      // out, instead of saving the initially clamped position near the top.
      transcript.scrollTop = restoringTop;
      expectedTop = transcript.scrollTop;
      return;
    }
    if (!stickToBottom) return;
    transcript.scrollTop = transcript.scrollHeight;
    expectedTop = transcript.scrollTop;
    savePosition();
  };

  const atBottom = () => transcript.scrollHeight - transcript.clientHeight - transcript.scrollTop <= 4;
  const releasePointer = () => {
    if (!pointerHeld) return;
    pointerHeld = false;
    stickToBottom = !selectingText() && atBottom();
    savePosition();
  };
  onMount(() => {
    const release = releasePointer;
    window.addEventListener("pagehide", savePosition);
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    onCleanup(() => {
      window.removeEventListener("pagehide", savePosition);
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    });
    if (typeof ResizeObserver === "undefined") return;
    // Restore history or follow the bottom as replay and layout settle.
    resizeObserver = new ResizeObserver(followBottom);
    resizeObserver.observe(transcript.firstElementChild!);
  });
  onCleanup(() => {
    disposed = true;
    resizeObserver?.disconnect();
  });

  onMount(() => void agents.ensureLoaded());

  const contentRevision = createMemo(() => {
    const last = props.nodes.at(-1);
    const reasoning = [...props.nodes].reverse().find(node => node.kind === "reasoning");
    return JSON.stringify([props.nodes.length, last?.id,
      last && "content" in last ? last.content : null,
      reasoning && "content" in reasoning ? reasoning.content : null]);
  });
  createEffect(() => {
    contentRevision();
    // Streaming must never revoke the reader's decision to inspect history.
    queueMicrotask(followBottom);
  });

  return (
    <div
      class="transcript-scroll"
      data-conversation-scroll
      ref={transcript}
      onWheel={(event) => {
        if (event.deltaY < 0) { pauseFollow(); return; }
        if (event.deltaY > 0 && atBottom() && !selectingText()) {
          restoringTop = undefined;
          stickToBottom = true;
          expectedTop = undefined;
          savePosition();
        }
      }}
      onPointerDown={() => { pointerHeld = true; pauseFollow(); }}
      onPointerUp={releasePointer}
      onPointerCancel={releasePointer}
      onTouchMove={pauseFollow}
      onKeyDown={(event) => {
        if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
          pauseFollow();
        } else if (event.key === "End" || (atBottom() && ["ArrowDown", "PageDown", " "].includes(event.key))) {
          restoringTop = undefined;
          stickToBottom = true;
          queueMicrotask(followBottom);
        }
      }}
      onScroll={() => {
        if (expectedTop !== undefined && Math.abs(transcript.scrollTop - expectedTop) < 1) return;
        if (!transcript.clientHeight || (storageKey && !props.nodes.length)) return;
        restoringTop = undefined;
        expectedTop = undefined;
        stickToBottom = !pointerHeld && !selectingText() && atBottom();
        savePosition();
      }}
    >
      <div class="transcript-column" aria-live="polite">
        <Show when={props.nodes.length > 0}>
          <ReasoningDisclosureProvider nodes={props.nodes}>
          <For each={props.nodes}>
            {(node) => {
              const contribution = plugins.conversationNodes.get(node.kind);
              return contribution ? (
                <Dynamic component={contribution.component} node={node} />
              ) : null;
            }}
          </For>
          </ReasoningDisclosureProvider>
        </Show>
        <Show when={props.nodes.length === 0 && props.fresh}>
          <FreshChatHero agentId={props.agentId} />
        </Show>
        <Show when={props.nodes.length === 0 && !props.fresh}>
          <div class="conversation-empty compact">
            <h2>Loading conversation…</h2>
            <p>Reading durable events from the local gateway.</p>
          </div>
        </Show>
      </div>
    </div>
  );
}
