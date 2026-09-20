import { describe, expect, it } from "vitest";
import { groupFileEdits, type EditNode } from "./editGroups";
const edit = (id: string, extra: Partial<EditNode> = {}): EditNode => ({
  id, kind: "tool", name: "edit_file", status: "completed", runId: "run", sessionId: "chat",
  workingDirectory: "/project", arguments: { path: "plan.md" }, ...extra,
});
describe("file edit grouping", () => {
  it("collapses repeated edits while retaining every original diff", () => {
    const nodes = Array.from({ length: 16 }, (_, i) => edit(String(i), { content: `diff ${i}` }));
    const entries = groupFileEdits(nodes);
    expect(entries).toHaveLength(1);
    expect(entries[0]!.edits).toEqual(nodes);
  });
  it("preserves messages, failures, other files, workspaces, and run boundaries", () => {
    const nodes = [edit("1"), edit("2", { status: "failed" }), edit("3"),
      { id: "message", kind: "assistant" as const, content: "Checking the result" },
      edit("4"), edit("5", { runId: "next" }),
      edit("6", { arguments: { path: "other.md" } }),
      edit("7", { arguments: { path: "other.md", workspace: "scratch" } })];
    expect(groupFileEdits(nodes)).toHaveLength(nodes.length);
  });
});
