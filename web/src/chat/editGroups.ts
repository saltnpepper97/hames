import type { ConversationNode } from "./projection";
export type EditNode = Extract<ConversationNode, { kind: "tool" }>;
export interface TranscriptEntry { node: ConversationNode; edits: EditNode[] }
function eligible(node: ConversationNode): node is EditNode {
  return node.kind === "tool" && node.name === "edit_file" && node.status === "completed" &&
    !!node.runId && typeof node.arguments?.path === "string";
}
export function groupFileEdits(nodes: readonly ConversationNode[]): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  for (const node of nodes) {
    const previous = entries.at(-1);
    const first = previous?.node;
    if (eligible(node) && first && eligible(first) &&
      first.runId === node.runId && first.sessionId === node.sessionId &&
      first.workingDirectory === node.workingDirectory &&
      (first.arguments?.workspace ?? "project") === (node.arguments?.workspace ?? "project") &&
      first.arguments?.path === node.arguments?.path) {
      previous!.edits.push(node);
    } else entries.push({ node, edits: eligible(node) ? [node] : [] });
  }
  return entries;
}
