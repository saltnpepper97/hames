import type { SkillCatalogEntry } from "../api/types";

export function canDeleteSkill(skill: SkillCatalogEntry | undefined): boolean {
  return skill?.source === "managed" && skill.scope !== "global";
}
