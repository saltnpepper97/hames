import type { HamesEvent } from "../../api/types";

export type EventCategory = "message" | "reasoning" | "tool" | "decision" | "runtime";

const categoryLabels: Record<EventCategory, string> = {
  message: "Messages",
  reasoning: "Reasoning",
  tool: "Tools",
  decision: "Decisions",
  runtime: "Runtime",
};

export function eventCategory(type: string): EventCategory {
  if (type.includes("message")) return "message";
  if (type.includes("reasoning")) return "reasoning";
  if (type.includes("tool")) return "tool";
  if (
    type.startsWith("approval.") ||
    type.startsWith("question.") ||
    type.startsWith("queue.")
  ) return "decision";
  return "runtime";
}

export function eventCategoryLabel(category: EventCategory): string {
  return categoryLabels[category];
}

export function eventTypeLabel(type: string): string {
  return type
    .split(".")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1).replaceAll("_", " ")}`)
    .join(" · ");
}

function stringValue(payload: Record<string, unknown>, key: string): string {
  return typeof payload[key] === "string" ? payload[key] : "";
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

export function eventSummary(event: HamesEvent): string {
  const payload = event.payload;
  if (event.type === "context.compiled") {
    const sources = Array.isArray(payload.selected_sources) ? payload.selected_sources : [];
    const injectedTokens = sources.reduce((sum, source) => {
      if (!source || typeof source !== "object" || Array.isArray(source)) return sum;
      const value = (source as Record<string, unknown>).selected_tokens;
      return sum + (typeof value === "number" ? value : 0);
    }, 0);
    const requestTokens = typeof payload.estimated_input_tokens === "number"
      ? payload.estimated_input_tokens
      : 0;
    return `${sources.length} injected ${sources.length === 1 ? "source" : "sources"} · ${injectedTokens.toLocaleString()} tokens · ${requestTokens.toLocaleString()} request`;
  }
  const direct = ["summary", "content", "text", "question", "reason", "message"]
    .map((key) => stringValue(payload, key).trim())
    .find(Boolean);
  if (direct) return direct;
  const tool = stringValue(payload, "name").trim() || stringValue(payload, "tool_name").trim();
  const status = stringValue(payload, "status").trim();
  if (tool && status) return `${tool} · ${status}`;
  if (tool) return tool;
  if (status) return status;
  const model = stringValue(payload, "model").trim();
  if (model) return model;
  const keys = Object.keys(payload);
  if (keys.length === 0) return "No payload";
  const json = compactJson(payload);
  return json || `${keys.length} payload field${keys.length === 1 ? "" : "s"}`;
}

export function eventTime(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function eventDuration(events: readonly HamesEvent[]): string {
  const times = events
    .map((event) => new Date(event.created_at).getTime())
    .filter(Number.isFinite);
  if (times.length < 2) return "0 ms";
  const duration = Math.max(...times) - Math.min(...times);
  if (duration < 1000) return `${duration} ms`;
  if (duration < 60_000) return `${(duration / 1000).toFixed(duration < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(duration / 60_000);
  const seconds = Math.floor((duration % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function eventIsError(event: HamesEvent): boolean {
  return /(?:failed|error|rejected|cancelled)$/.test(event.type);
}
