import type { ProviderModel } from "../api/types";

export function modelReasoningEfforts(model: ProviderModel): string[] {
  if (model.reasoning_supported !== true) return [];
  if (model.reasoning_efforts.length === 0 || (
    model.reasoning_efforts.length === 1 && model.reasoning_efforts[0] === "on"
  )) {
    return ["on", "off"];
  }

  const efforts = model.reasoning_efforts.filter((effort) => effort !== "default");
  if (!efforts.includes("off")) efforts.push("off");
  return efforts;
}

export function reasoningEffortLabel(value: string): string {
  if (value === "xhigh") return "Extra high";
  if (value === "on") return "On";
  if (value === "off") return "Off";
  return `${value[0]?.toUpperCase() ?? ""}${value.slice(1)}`;
}
