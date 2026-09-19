import type { ConversationNode } from "./projection";

export type ToolConversationNode = Extract<ConversationNode, { kind: "tool" }>;

export type DiffRowKind = "file" | "hunk" | "add" | "delete" | "context" | "meta";

export interface DiffRow {
  kind: DiffRowKind;
  text: string;
  oldLine?: number;
  newLine?: number;
}

export interface DiffPresentation {
  rows: DiffRow[];
  added: number;
  removed: number;
  files: number;
  path: string;
}

export interface TerminalPresentation {
  command: string;
  cwd: string;
  stdout: string;
  stderr: string;
  exitCode?: number;
  durationSeconds?: number;
  running: boolean;
  state: "running" | "success" | "warning" | "error";
  truncated: boolean;
}

export interface TaskPresentation {
  title: string;
  completed: number;
  total: number;
  activeText: string;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function displayPath(value: string): string {
  if (value === "/dev/null") return value;
  return value.replace(/^[ab]\//, "");
}

export function parseUnifiedDiff(content: string | undefined): DiffPresentation | undefined {
  if (!content?.trim()) return undefined;
  const source = content.endsWith("\n") ? content.slice(0, -1) : content;
  const lines = source.split("\n");
  if (!lines.some((line) => line.startsWith("@@ ")) || !lines.some((line) => line.startsWith("+++ "))) {
    return undefined;
  }

  const rows: DiffRow[] = [];
  const paths = new Set<string>();
  let oldPath = "";
  let path = "";
  let oldLine: number | undefined;
  let newLine: number | undefined;
  let added = 0;
  let removed = 0;

  for (const line of lines) {
    if (line.startsWith("--- ")) {
      oldPath = displayPath(line.slice(4).split("\t", 1)[0] ?? "");
      continue;
    }
    if (line.startsWith("+++ ")) {
      const nextPath = displayPath(line.slice(4).split("\t", 1)[0] ?? "");
      path = nextPath === "/dev/null" ? oldPath : nextPath;
      if (path) paths.add(path);
      rows.push({ kind: "file", text: path || nextPath || oldPath || "Changed file" });
      continue;
    }
    if (line.startsWith("@@ ")) {
      const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
      if (match) {
        oldLine = Number(match[1]);
        newLine = Number(match[2]);
      }
      rows.push({ kind: "hunk", text: line });
      continue;
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      rows.push({ kind: "add", text: line.slice(1), newLine });
      if (newLine !== undefined) newLine += 1;
      added += 1;
      continue;
    }
    if (line.startsWith("-") && !line.startsWith("---")) {
      rows.push({ kind: "delete", text: line.slice(1), oldLine });
      if (oldLine !== undefined) oldLine += 1;
      removed += 1;
      continue;
    }
    if (line.startsWith(" ")) {
      rows.push({ kind: "context", text: line.slice(1), oldLine, newLine });
      if (oldLine !== undefined) oldLine += 1;
      if (newLine !== undefined) newLine += 1;
      continue;
    }
    rows.push({ kind: "meta", text: line });
  }

  return rows.length > 0
    ? { rows, added, removed, files: paths.size || 1, path: path || oldPath }
    : undefined;
}

function intendedDiffPresentation(node: ToolConversationNode): DiffPresentation | undefined {
  if (!["requested", "started", "running", "pending"].includes(node.status)) return undefined;
  const path = stringValue(node.arguments?.path);
  if (!path) return undefined;
  const rows: DiffRow[] = [{ kind: "file", text: path }];
  let added = 0;
  let removed = 0;
  const contentLines = (value: string) => {
    if (!value) return [];
    return (value.endsWith("\n") ? value.slice(0, -1) : value).split("\n");
  };
  if (node.name === "edit_file") {
    const oldText = stringValue(node.arguments?.old_text);
    const newText = stringValue(node.arguments?.new_text);
    contentLines(oldText).forEach((text, index) => {
      rows.push({ kind: "delete", text, oldLine: index + 1 });
      removed += 1;
    });
    contentLines(newText).forEach((text, index) => {
      rows.push({ kind: "add", text, newLine: index + 1 });
      added += 1;
    });
  } else if (node.name === "write_file") {
    const content = stringValue(node.arguments?.content);
    contentLines(content).forEach((text, index) => {
      rows.push({ kind: "add", text, newLine: index + 1 });
      added += 1;
    });
  } else {
    return undefined;
  }
  return { rows, added, removed, files: 1, path };
}

export function diffPresentation(node: ToolConversationNode): DiffPresentation | undefined {
  if (!["write_file", "edit_file"].includes(node.name)) return undefined;
  return parseUnifiedDiff(node.content) ?? intendedDiffPresentation(node);
}

function splitShellContent(content: string | undefined): { stdout: string; stderr: string } {
  if (!content?.startsWith("stdout:\n")) return { stdout: content ?? "", stderr: "" };
  const separator = "\nstderr:\n";
  const boundary = content.lastIndexOf(separator);
  if (boundary < 0) return { stdout: content.slice("stdout:\n".length), stderr: "" };
  return {
    stdout: content.slice("stdout:\n".length, boundary),
    stderr: content.slice(boundary + separator.length),
  };
}

// Terminal output is displayed as text, so remove control sequences rather than
// allowing invisible cursor movement or OSC payloads into the transcript.
export function plainTerminalText(value: string): string {
  return value
    .replace(/\u001B\][^\u0007]*(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\r(?!\n)/g, "\n")
    .replace(/\u0008/g, "");
}

export function terminalPresentation(node: ToolConversationNode): TerminalPresentation | undefined {
  if (node.name !== "shell") return undefined;
  const command = stringValue(node.arguments?.command);
  if (!command) return undefined;
  const structured = node.structuredData ?? {};
  const fallback = splitShellContent(node.content);
  const workspace = stringValue(node.arguments?.workspace) || "project";
  const cwd = workspace === "project"
    ? node.workingDirectory
    : workspace === "home"
      ? "~"
      : workspace;
  const exitCode = finiteNumber(structured.exit_code);
  const running = ["requested", "started", "running", "pending"].includes(node.status);
  const state = running
    ? "running"
    : ["failed", "error"].includes(node.status) || (exitCode !== undefined && exitCode !== 0)
      ? "error"
      : ["rejected", "cancelled", "stopped"].includes(node.status)
        ? "warning"
        : "success";
  return {
    command,
    cwd,
    stdout: plainTerminalText(stringValue(structured.stdout) || fallback.stdout).replace(/\n$/, ""),
    stderr: plainTerminalText(stringValue(structured.stderr) || fallback.stderr).replace(/\n$/, ""),
    exitCode,
    durationSeconds: node.durationSeconds,
    running,
    state,
    truncated: Boolean(node.truncated),
  };
}

export function taskPresentation(node: ToolConversationNode): TaskPresentation | undefined {
  if (!["task_list", "task_update"].includes(node.name)) return undefined;
  const taskList = node.structuredData?.task_list;
  if (!taskList || typeof taskList !== "object" || Array.isArray(taskList)) return undefined;
  const value = taskList as Record<string, unknown>;
  if (!Array.isArray(value.items)) return undefined;
  const items = value.items.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return [];
    const item = candidate as Record<string, unknown>;
    const status = stringValue(item.status);
    return [
      {
        text: stringValue(item.text),
        status,
      },
    ];
  });
  return {
    title: stringValue(value.title) || "Tasks",
    completed: items.filter((item) => item.status === "completed").length,
    total: items.length,
    activeText: items.find((item) => item.status === "in_progress")?.text ?? "",
  };
}

export function taskCompactSummary(node: ToolConversationNode): string {
  const task = taskPresentation(node);
  if (!task) {
    return node.summary || (["requested", "started", "running", "pending"].includes(node.status)
      ? "Working…"
      : "Checklist updated");
  }
  const progress = `${task.completed}/${task.total} completed`;
  return task.activeText ? `${progress} · ${task.activeText}` : progress;
}

export function toolTitle(name: string): string {
  const titles: Record<string, string> = {
    shell: "Run",
    write_file: "Write",
    edit_file: "Edit",
    read_file: "Read",
    list_dir: "List",
    task_list: "Checked tasks",
    task_update: "Updated tasks",
  };
  return titles[name] ?? name.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function toolCompactSummary(node: ToolConversationNode): string {
  const terminal = terminalPresentation(node);
  if (terminal) return terminal.command.split("\n", 1)[0] ?? terminal.command;
  const diff = diffPresentation(node);
  if (diff) return `${diff.path} · +${diff.added} −${diff.removed}`;
  if (["task_list", "task_update"].includes(node.name)) return taskCompactSummary(node);
  const path = stringValue(node.arguments?.path);
  if (path || node.summary) return path || node.summary || "Details";
  return ["requested", "started", "running", "pending"].includes(node.status)
    ? "Working…"
    : "Details";
}

export function formatToolDuration(seconds: number | undefined): string {
  if (seconds === undefined) return "";
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1_000))}ms`;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}
