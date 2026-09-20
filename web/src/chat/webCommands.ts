export type WebCommand =
  | { kind: "compact" }
  | { kind: "custom"; name: string; note: string }
  | { kind: "dream" }
  | { kind: "fork"; at?: string }
  | { kind: "goal"; action: "show" | "start" | "pause" | "resume" | "cancel"; objective?: string }
  | { kind: "heal" }
  | { kind: "stop" };

const movedCommandGuidance: Readonly<Record<string, string>> = {
  "/flow": "Select a coordinator in the agent picker and describe your task directly.",
  "/new": "Use New chat in the Chat sidebar.",
  "/clear": "Use New chat or delete this chat from the Chat sidebar.",
  "/sessions": "Choose a chat from the Chat sidebar.",
  "/resume": "Choose a chat from the Chat sidebar.",
  "/queue": "Queue management belongs beside the composer, not in the command palette.",
  "/model": "Use the model picker below the composer.",
  "/effort": "Use the model picker below the composer.",
  "/agent": "Use the agent picker below the composer or the Agents page.",
  "/mode": "Use the interaction mode control below the composer.",
  "/themes": "Use the Appearance section in Settings.",
  "/status": "Session status is shown in the chat header.",
  "/project": "Workspace details are shown in the chat header and Settings.",
  "/gateway": "Gateway status is shown in the app header and Settings.",
  "/events": "Open the Events tab in this chat.",
  "/inspect": "Open the Events tab in this chat.",
  "/context": "Open Prompt context in the transcript for context changes, or Events for every request snapshot.",
  "/details": "Expand transcript details directly.",
  "/memory": "Use the Memory page.",
  "/skills": "Use the Skills page.",
  "/scars": "Use the Scars page.",
  "/plugins": "Use the Plugins page.",
  "/mcp": "Use the MCP section in Settings.",
  "/help": "Web help will live in the browser UI.",
  "/cancel": "Use the Stop button beside the composer.",
  "/quit": "Close this browser tab when you are finished.",
};

export const movedWebCommandValues = Object.freeze(Object.keys(movedCommandGuidance));

export function parseWebCommand(content: string, customNames: readonly string[] = []): WebCommand | undefined {
  const match = /^\s*\/(\S+)(?:\s+([\s\S]*?))?\s*$/.exec(content);
  if (!match) return undefined;
  const name = `/${(match[1] ?? "").toLocaleLowerCase()}`;
  const argument = (match[2] ?? "").trim();
  if (customNames.includes(name.slice(1))) return { kind: "custom", name: name.slice(1), note: argument };
  if (name === "/dream" && !argument) return { kind: "dream" };
  if (name === "/compact" && !argument) return { kind: "compact" };
  if (name === "/fork") return { kind: "fork", at: argument || undefined };
  if (name === "/heal" && !argument) return { kind: "heal" };
  if (name === "/stop" && !argument) return { kind: "stop" };
  if (name !== "/goal") return undefined;
  if (!argument) return { kind: "goal", action: "show" };
  const action = argument.toLocaleLowerCase();
  if (action === "pause" || action === "resume" || action === "cancel") {
    return { kind: "goal", action };
  }
  return { kind: "goal", action: "start", objective: argument };
}

export function movedCommandMessage(content: string): string | undefined {
  const name = /^\s*(\/\S+)/.exec(content)?.[1]?.toLocaleLowerCase();
  return name ? movedCommandGuidance[name] : undefined;
}
