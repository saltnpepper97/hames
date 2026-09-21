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

it("groups adjacent native and sed reads of the same file without losing output", () => {
  const a = edit("a", { name: "read_file", content: "one", arguments: { path: "src/main.rs" } });
  const b = edit("b", { name: "shell", content: "two", arguments: { command: "sed -n '20,60p' src/main.rs" } });
  const entries = groupFileEdits([a, b]);
  expect(entries).toHaveLength(1);
  expect(entries[0]!.reads).toEqual([a, b]);
  expect(entries[0]!.readPath).toBe("src/main.rs");
});
it("does not group failed reads, mutations, compound commands, or other runs", () => {
  const commands = ["sed -n '1,20p' file", "sed -i 's/a/b/' file", "cat file > other", "cat file && rm other"];
  const nodes = commands.map((command, i) => edit(String(i), { name: "shell", arguments: { command } }));
  expect(groupFileEdits(nodes)).toHaveLength(4);
  const read = edit("read", { name: "read_file", arguments: { path: "file" } });
  expect(groupFileEdits([read, { ...read, id: "failed", status: "failed" }, { ...read, id: "new", runId: "new" }])).toHaveLength(3);
});
