import { describe, expect, it } from "vitest";
import { fallbackAvatar, hexToHsv, hsvToHex } from "./color";

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
});
