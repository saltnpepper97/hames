import { describe, expect, it } from "vitest";
import { movedCommandMessage, parseWebCommand } from "./webCommands";

describe("web slash commands", () => {
  it("parses operational commands without treating their arguments as chat", () => {
    expect(parseWebCommand("/build-review preserve API")).toBeUndefined();
    expect(parseWebCommand("/review-pass preserve API", ["review-pass"])).toEqual({ kind: "custom", name: "review-pass", note: "preserve API" });
    expect(parseWebCommand("/dream")).toEqual({ kind: "dream" });
    expect(parseWebCommand("/dream extra")).toBeUndefined();
    expect(parseWebCommand("/compact")).toEqual({ kind: "compact" });
    expect(parseWebCommand("/fork event-42")).toEqual({ kind: "fork", at: "event-42" });
    expect(parseWebCommand("/goal pause")).toEqual({ kind: "goal", action: "pause" });
    expect(parseWebCommand("/goal finish the release")).toEqual({
      kind: "goal",
      action: "start",
      objective: "finish the release",
    });
  });

  it("redirects removed web commands to their native controls", () => {
    expect(movedCommandMessage("/sessions")).toBe("Choose a chat from the Chat sidebar.");
    expect(movedCommandMessage("/model gpt-5.6-sol")).toBe("Use the model picker below the composer.");
    expect(movedCommandMessage("/memory")).toBe("Use the Memory page.");
    expect(movedCommandMessage("/project-checks")).toBeUndefined();
  });
});
