import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { durableEventTypes } from "./eventTypes";

describe("durable event subscriptions", () => {
  it("tracks every typed gateway event", () => {
    const registry = readFileSync(
      resolve(process.cwd(), "../src/hames/event_types.py"),
      "utf8",
    );
    const mapping = /EVENT_PAYLOADS:[^=]+\s*=\s*\{([\s\S]*?)\n\}/.exec(registry)?.[1] ?? "";
    const gatewayTypes = [...mapping.matchAll(/^\s+"([^"]+)":/gm)].map((match) => match[1]);

    expect(gatewayTypes.length).toBeGreaterThan(100);
    expect(new Set(durableEventTypes)).toEqual(new Set(gatewayTypes));
  });
});
