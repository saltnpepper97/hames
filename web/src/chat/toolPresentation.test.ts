import { describe, expect, it } from "vitest";
import type { ToolConversationNode } from "./toolPresentation";
import {
  formatToolDuration,
  diffPresentation,
  parseUnifiedDiff,
  plainTerminalText,
  taskCompactSummary,
  taskPresentation,
  terminalPresentation,
  toolCompactSummary,
  toolTitle,
} from "./toolPresentation";

function tool(overrides: Partial<ToolConversationNode> = {}): ToolConversationNode {
  return {
    id: "tool-one",
    kind: "tool",
    sessionId: "session-one",
    workingDirectory: "/work/hames",
    name: "shell",
    status: "completed",
    arguments: { command: "pnpm test", workspace: "project" },
    ...overrides,
  };
}

describe("tool presentation", () => {
  it("parses unified diffs with paths, line numbers, and totals", () => {
    const diff = parseUnifiedDiff([
      "--- a/src/app.ts",
      "+++ b/src/app.ts",
      "@@ -4,3 +4,3 @@ function run() {",
      " keep()",
      "-oldValue()",
      "+newValue()",
      " done()",
    ].join("\n"));

    expect(diff).toMatchObject({ path: "src/app.ts", files: 1, added: 1, removed: 1 });
    expect(diff?.rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "delete", oldLine: 5, text: "oldValue()" }),
      expect.objectContaining({ kind: "add", newLine: 5, text: "newValue()" }),
    ]));
  });

  it("uses structured shell streams and preserves useful execution metadata", () => {
    const terminal = terminalPresentation(tool({
      content: "stdout:\nfallback\nstderr:\n",
      structuredData: { stdout: "passed\n", stderr: "warning\n", exit_code: 2 },
      durationSeconds: 1.25,
      truncated: true,
    }));

    expect(terminal).toEqual({
      command: "pnpm test",
      cwd: "/work/hames",
      stdout: "passed",
      stderr: "warning",
      exitCode: 2,
      durationSeconds: 1.25,
      running: false,
      state: "error",
      truncated: true,
    });
  });

  it("falls back to the legacy shell body and removes terminal controls", () => {
    const terminal = terminalPresentation(tool({
      content: "stdout:\n\u001b[32mok\u001b[0m\nstderr:\n",
    }));
    expect(terminal?.stdout).toBe("ok");
    expect(plainTerminalText("title\u001b]0;secret\u0007safe")).toBe("titlesafe");
  });

  it("derives compact human summaries", () => {
    expect(toolTitle("edit_file")).toBe("Edit");
    expect(toolTitle("plugin.inspect")).toBe("Plugin.inspect");
    expect(toolCompactSummary(tool())).toBe("pnpm test");
    expect(formatToolDuration(0.042)).toBe("42ms");
    expect(formatToolDuration(1.25)).toBe("1.3s");
  });

  it("shows the intended edit while the tool is running", () => {
    const diff = diffPresentation(tool({
      name: "edit_file",
      status: "started",
      arguments: { path: "src/app.ts", old_text: "before", new_text: "after" },
    }));
    expect(diff).toMatchObject({ path: "src/app.ts", added: 1, removed: 1 });
  });

  it("does not invent an added blank line for a full deletion", () => {
    const diff = diffPresentation(tool({
      name: "edit_file",
      status: "started",
      arguments: { path: "src/app.ts", old_text: "remove me\n", new_text: "" },
    }));
    expect(diff).toMatchObject({ added: 0, removed: 1 });
  });

  it("summarizes task tools from their projected checklist", () => {
    const node = tool({
      name: "task_update",
      summary: "marked the first task completed",
      structuredData: {
        task_list: {
          title: "Transcript polish",
          items: [
            { id: "one", text: "Build the strip", status: "completed", position: 0 },
            { id: "two", text: "Run tests", status: "in_progress", position: 1 },
            { id: "three", text: "Inspect it", status: "pending", position: 2 },
          ],
        },
      },
    });

    expect(taskPresentation(node)).toEqual({
      title: "Transcript polish",
      completed: 1,
      total: 3,
      activeText: "Run tests",
    });
    expect(taskCompactSummary(node)).toBe("1/3 completed · Run tests");
    expect(toolCompactSummary(node)).toBe("1/3 completed · Run tests");
    expect(toolTitle("task_update")).toBe("Updated tasks");
  });
});
