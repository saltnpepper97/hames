import type { DelegationNode } from "../projection";
export function DelegationChatLink(props: { node: DelegationNode }) {
  return <div class="delegation-chat-link"><button type="button" class="delegation-transcript-link" onClick={() => window.dispatchEvent(new CustomEvent("hames:open-worker", { detail: { sessionId: props.node.parentSessionId, agentId: props.node.agentId } }))}>View transcript</button></div>;
}
