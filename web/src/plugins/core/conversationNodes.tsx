import { Show } from "solid-js";
import type { JSX } from "solid-js";
import type { ConversationNode } from "../../chat/projection";
import type { ConversationNodeContribution } from "../../shell/plugins";

function ToolNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "tool") return <></>;
  const node = props.node;
  const detail = () => {
    if (node.content) return node.content;
    if (node.arguments) return JSON.stringify(node.arguments, null, 2);
    return "No details were recorded.";
  };

  return (
    <details class="tool-node">
      <summary>
        <span>{node.name}</span>
        <span class={`tool-state ${node.status}`}>{node.status}</span>
        <Show when={node.summary}>
          <span class="tool-summary">{node.summary}</span>
        </Show>
      </summary>
      <pre>{detail()}</pre>
    </details>
  );
}

function ReasoningNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "reasoning") return <></>;
  const node = props.node;
  return (
    <details class="reasoning-node" open={node.live || undefined}>
      <summary>{node.live ? "Thinking" : "Reasoning"}</summary>
      <div class="message-copy">{node.content}</div>
    </details>
  );
}

function NoticeNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "notice") return <></>;
  return <p class={`conversation-notice ${props.node.tone}`}>{props.node.content}</p>;
}

function MessageNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "user" && props.node.kind !== "assistant") return <></>;
  const node = props.node;
  return (
    <article class={`message-node ${node.kind}`}>
      <div class="message-copy">{node.content}</div>
      <Show when={node.live}>
        <span class="live-cursor" aria-label="Streaming response" />
      </Show>
    </article>
  );
}

export const coreConversationNodes = [
  { kind: "user", component: MessageNode },
  { kind: "assistant", component: MessageNode },
  { kind: "reasoning", component: ReasoningNode },
  { kind: "tool", component: ToolNode },
  { kind: "notice", component: NoticeNode },
] satisfies readonly ConversationNodeContribution[];
