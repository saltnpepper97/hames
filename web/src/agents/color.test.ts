import { describe, expect, it } from "vitest";
import { contrastingEyeColor, fallbackAvatar, hexToHsv, hsvToHex } from "./color";

describe("agent avatar colors", () => {
  it("round trips representative colors", () => {
    for (const color of ["#2563eb", "#16a34a", "#ffffff", "#18181b"]) {
      expect(hsvToHex(hexToHsv(color))).toBe(color);
    }
  });

  it("keeps deterministic agent fallbacks", () => {
    expect(fallbackAvatar("default")).toEqual(fallbackAvatar("default"));
    expect(fallbackAvatar("default").color).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("selects legible eyes for light and dark shells", () => {
    expect(contrastingEyeColor("#ffffff")).toBe("#111827");
    expect(contrastingEyeColor("#facc15")).toBe("#111827");
    expect(contrastingEyeColor("#18181b")).toBe("#ffffff");
  });
});
