import type { SkillCatalogEntry } from "../api/types";
import { movedWebCommandValues } from "./webCommands";

export interface SlashCommand {
  value: string;
  detail: string;
  argumentHint?: string;
}

export const coreSlashCommands: readonly SlashCommand[] = [
  { value: "/compact", detail: "Summarize older conversation" },
  { value: "/fork", detail: "Branch this conversation", argumentHint: "[event]" },
  {
    value: "/goal",
    detail: "Start or control autonomous work",
    argumentHint: "[objective|pause|resume|cancel]",
  },
  { value: "/heal", detail: "Repair active behavioral scars" },
  { value: "/stop", detail: "Close background terminals" },
  { value: "/dream", detail: "Reconcile memories, skills and scars now" },
  { value: "/connect", detail: "Connect and manage providers" },
  { value: "/flow", detail: "Run a saved flow in this chat", argumentHint: "<flow-id> <task> | <flow-id> --plan" },
];

export function skillSlashCommands(skills: readonly SkillCatalogEntry[]): SlashCommand[] {
  const reserved = new Set([
    ...coreSlashCommands.map((command) => command.value),
    ...movedWebCommandValues,
  ]);
  return skills
    .filter((skill) => !skill.archived && (skill.invocation === "user" || skill.invocation === "both"))
    .map((skill) => ({
      value: `/${skill.slug}`,
      detail: `Skill · ${skill.description}`,
      argumentHint: skill.argument_hint || undefined,
    }))
    .filter((command) => !reserved.has(command.value))
    .sort((left, right) => left.value.localeCompare(right.value));
}
