import type { ConversationNode } from "./projection";
export type EditNode = Extract<ConversationNode, { kind: "tool" }>;
export interface TranscriptEntry { node: ConversationNode; edits: EditNode[]; reads: EditNode[]; readPath?: string }
function eligible(node: ConversationNode): node is EditNode {
  return node.kind === "tool" && node.name === "edit_file" && node.status === "completed" &&
    !!node.runId && typeof node.arguments?.path === "string";
}
export function fileReadPath(node: ConversationNode): string | undefined {
  if (node.kind !== "tool" || node.status !== "completed" || !node.runId) return;
  if (node.name === "read_file" && typeof node.arguments?.path === "string") return node.arguments.path;
  if (node.name !== "shell" || typeof node.arguments?.command !== "string") return;
  // Only classify simple inspection commands, never pipelines, writes, or scripts.
  const command = node.arguments.command.trim();
  const match = /^(?:sed\s+-n\s+(?:'\d+(?:,\d+)?p'|"\d+(?:,\d+)?p"|\d+(?:,\d+)?p)|cat|(?:head|tail)(?:\s+-n\s+\d+)?)\s+(?:--\s+)?(?:'([^']+)'|"([^"$`]+)"|([^\s;|&<>$`"'\\]+))$/.exec(command);
  return match ? (match[1] ?? match[2] ?? match[3]) : undefined;
}
export function groupFileEdits(nodes: readonly ConversationNode[]): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  for (const node of nodes) {
    const previous = entries.at(-1);
    const first = previous?.node;
    const readPath = fileReadPath(node);
    if (eligible(node) && first && eligible(first) &&
      first.runId === node.runId && first.sessionId === node.sessionId &&
      first.workingDirectory === node.workingDirectory &&
      (first.arguments?.workspace ?? "project") === (node.arguments?.workspace ?? "project") &&
      first.arguments?.path === node.arguments?.path) {
      previous!.edits.push(node);
    } else if (readPath && previous?.readPath === readPath && first &&
      node.kind === "tool" && first.kind === "tool" &&
      first.runId === node.runId && first.sessionId === node.sessionId &&
      first.workingDirectory === node.workingDirectory &&
      (first.arguments?.workspace ?? "project") === (node.arguments?.workspace ?? "project")) {
      previous.reads.push(node);
    } else entries.push({ node, edits: eligible(node) ? [node] : [],
      reads: readPath ? [node as EditNode] : [], readPath });
  }
  return entries;
}
