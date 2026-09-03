import { Dynamic } from "solid-js/web";
import { For, Show, createEffect } from "solid-js";
import type { ConversationNode } from "../projection";
import type { StreamState } from "../sessionStream";
import { useWebPlugins } from "../../shell/pluginContext";

interface ConversationViewportProps {
  nodes: readonly ConversationNode[];
  streamState: StreamState;
}

export function ConversationViewport(props: ConversationViewportProps) {
  const plugins = useWebPlugins();
  let transcript!: HTMLDivElement;
  let stickToBottom = true;

  createEffect(() => {
    const lastNode = props.nodes.at(-1);
    lastNode?.id;
    lastNode && "content" in lastNode ? lastNode.content : undefined;
    queueMicrotask(() => {
      if (stickToBottom && transcript) transcript.scrollTop = transcript.scrollHeight;
    });
  });

  return (
    <div
      class="transcript-scroll"
      data-conversation-scroll
      ref={transcript}
      onScroll={() => {
        stickToBottom =
          transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 96;
      }}
    >
      <div class="transcript-column" aria-live="polite">
        <Show
          when={props.nodes.length > 0}
          fallback={
            <div class="conversation-empty compact">
              <h2>
                {props.streamState === "connecting"
                  ? "Loading conversation…"
                  : "Start a conversation"}
              </h2>
              <p>
                {props.streamState === "connecting"
                  ? "Reading durable events from the local gateway."
                  : "Messages sent here run through the same Hames session as the TUI and REPL."}
              </p>
            </div>
          }
        >
          <For each={props.nodes}>
            {(node) => {
              const contribution = plugins.conversationNodes.get(node.kind);
              return contribution ? (
                <Dynamic component={contribution.component} node={node} />
              ) : null;
            }}
          </For>
        </Show>
      </div>
    </div>
  );
}
