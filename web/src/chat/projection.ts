import type { HamesEvent } from "../api/types";

export type ConversationNode =
  | {
      id: string;
      kind: "user" | "assistant" | "reasoning";
      content: string;
      live?: boolean;
    }
  | {
      id: string;
      kind: "tool";
      name: string;
      status: string;
      arguments?: Record<string, unknown>;
      summary?: string;
      content?: string;
    }
  | {
      id: string;
      kind: "notice";
      tone: "neutral" | "danger";
      content: string;
    };

export interface LiveOutput {
  runId: string;
  reasoning: string;
  text: string;
}

export interface ConversationProjection {
  nodes: ConversationNode[];
  activeRunId?: string;
}

function text(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function toolIdentity(event: HamesEvent): string {
  return text(event.payload, "tool_call_id") || event.id;
}

export function projectConversation(
  events: readonly HamesEvent[],
  live?: LiveOutput,
): ConversationProjection {
  const nodes: ConversationNode[] = [];
  const tools = new Map<string, Extract<ConversationNode, { kind: "tool" }>>();
  const activeRuns = new Set<string>();

  for (const event of [...events].sort((left, right) => left.sequence - right.sequence)) {
    if (event.type === "run.started" && event.run_id) activeRuns.add(event.run_id);
    if (
      ["run.completed", "run.failed", "run.cancelled"].includes(event.type) &&
      event.run_id
    ) {
      activeRuns.delete(event.run_id);
    }

    if (event.type === "user.message") {
      if (text(event.payload, "purpose") !== "plan_execution") {
        nodes.push({ id: event.id, kind: "user", content: text(event.payload, "content") });
      }
      continue;
    }
    if (event.type === "assistant.reasoning") {
      const content = text(event.payload, "content");
      if (content) nodes.push({ id: event.id, kind: "reasoning", content });
      continue;
    }
    if (event.type === "assistant.message") {
      const content = text(event.payload, "content");
      if (content) nodes.push({ id: event.id, kind: "assistant", content });
      continue;
    }
    if (["model.tool_call", "tool.requested", "tool.started"].includes(event.type)) {
      const id = toolIdentity(event);
      let tool = tools.get(id);
      if (!tool) {
        tool = {
          id: `tool-${id}`,
          kind: "tool",
          name: text(event.payload, "name") || "Tool",
          status: text(event.payload, "status") || event.type.split(".")[1] || "pending",
        };
        tools.set(id, tool);
        nodes.push(tool);
      }
      tool.name = text(event.payload, "name") || tool.name;
      tool.status = text(event.payload, "status") || event.type.split(".")[1] || tool.status;
      const argumentsValue = event.payload.arguments;
      if (argumentsValue && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)) {
        tool.arguments = argumentsValue as Record<string, unknown>;
      }
      continue;
    }
    if (["tool.completed", "tool.failed", "tool.rejected"].includes(event.type)) {
      const id = toolIdentity(event);
      let tool = tools.get(id);
      if (!tool) {
        tool = {
          id: `tool-${id}`,
          kind: "tool",
          name: text(event.payload, "name") || "Tool",
          status: event.type.split(".")[1] || "completed",
        };
        tools.set(id, tool);
        nodes.push(tool);
      }
      tool.status = text(event.payload, "status") || event.type.split(".")[1] || tool.status;
      tool.summary = text(event.payload, "summary");
      tool.content = text(event.payload, "content");
      continue;
    }
    if (event.type === "run.failed" || event.type === "runtime.error") {
      const content = text(event.payload, "message");
      if (content) {
        nodes.push({ id: event.id, kind: "notice", tone: "danger", content });
      }
      continue;
    }
    if (event.type === "run.cancelled") {
      nodes.push({ id: event.id, kind: "notice", tone: "neutral", content: "Run cancelled." });
    }
  }

  if (live?.reasoning) {
    nodes.push({
      id: `live-reasoning-${live.runId}`,
      kind: "reasoning",
      content: live.reasoning,
      live: true,
    });
  }
  if (live?.text) {
    nodes.push({
      id: `live-assistant-${live.runId}`,
      kind: "assistant",
      content: live.text,
      live: true,
    });
  }

  return { nodes, activeRunId: [...activeRuns].at(-1) };
}
