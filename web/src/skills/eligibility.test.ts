import { describe, expect, it } from "vitest";
import type { SkillCatalogEntry } from "../api/types";
import { canDeleteSkill } from "./eligibility";

describe("sidebar Skill deletion eligibility", () => {
  it.each([
    ["managed", "workspace", true], ["managed", "agent", true], ["managed", "global", false],
    ["portable", "workspace", false], ["portable", "global", false], ["builtin", "global", false],
  ] as const)("uses provenance for %s / %s", (source, scope, expected) => {
    expect(canDeleteSkill({ source, scope } as SkillCatalogEntry)).toBe(expected);
  });
  it("does not offer deletion for a missing record", () => expect(canDeleteSkill(undefined)).toBe(false));
});
