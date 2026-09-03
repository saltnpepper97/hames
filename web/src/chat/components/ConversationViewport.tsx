import { Dynamic } from "solid-js/web";
import { For, Show, createEffect, onMount } from "solid-js";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import type { ConversationNode } from "../projection";
import type { StreamState } from "../sessionStream";
import { useWebPlugins } from "../../shell/pluginContext";
import { FreshChatHero } from "./FreshChatHero";

interface ConversationViewportProps {
  nodes: readonly ConversationNode[];
  streamState: StreamState;
  fresh?: boolean;
  agentId: string;
}

export function ConversationViewport(props: ConversationViewportProps) {
  const plugins = useWebPlugins();
  const agents = useAgentDirectory();
  let transcript!: HTMLDivElement;
  let stickToBottom = true;

  onMount(() => void agents.ensureLoaded());

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
          fallback={props.fresh
            ? <FreshChatHero agentId={props.agentId} />
            : (
              <div class="conversation-empty compact">
                <h2>Loading conversation…</h2>
                <p>Reading durable events from the local gateway.</p>
              </div>
            )}
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
