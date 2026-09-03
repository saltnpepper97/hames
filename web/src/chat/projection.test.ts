import { describe, expect, it } from "vitest";
import type { HamesEvent } from "../api/types";
import { projectConversation } from "./projection";

function event(
  sequence: number,
  type: string,
  payload: Record<string, unknown>,
  runId: string | null = "run-one",
): HamesEvent {
  return {
    id: `event-${sequence}`,
    sequence,
    session_id: "session-one",
    run_id: runId,
    agent_id: "default",
    type,
    schema_version: 1,
    created_at: "2026-09-02T18:00:00Z",
    causation_id: null,
    correlation_id: null,
    payload,
    blob_hash: null,
    payload_hash: `hash-${sequence}`,
    redaction_state: "clear",
  };
}

describe("conversation projection", () => {
  it("projects durable messages, tool state, and the active run", () => {
    const projection = projectConversation([
      event(1, "user.message", { content: "Inspect it", purpose: "turn" }),
      event(2, "run.started", {}),
      event(3, "assistant.reasoning", { content: "I will inspect it.", status: "completed" }),
      event(4, "tool.requested", { tool_call_id: "tool-one", name: "read", arguments: { path: "README.md" } }),
      event(5, "tool.completed", { tool_call_id: "tool-one", name: "read", status: "completed", summary: "Read README", content: "# Hames" }),
    ]);

    expect(projection.activeRunId).toBe("run-one");
    expect(projection.nodes).toEqual([
      expect.objectContaining({ kind: "user", content: "Inspect it" }),
      expect.objectContaining({ kind: "reasoning", content: "I will inspect it." }),
      expect.objectContaining({ kind: "tool", name: "read", status: "completed", summary: "Read README" }),
    ]);
  });

  it("settles runs and appends current transient output", () => {
    const projection = projectConversation(
      [event(1, "run.started", {}), event(2, "run.completed", {})],
      { runId: "run-two", reasoning: "Checking", text: "Answering" },
    );

    expect(projection.activeRunId).toBeUndefined();
    expect(projection.nodes.slice(-2)).toEqual([
      expect.objectContaining({ kind: "reasoning", content: "Checking", live: true }),
      expect.objectContaining({ kind: "assistant", content: "Answering", live: true }),
    ]);
  });

  it("folds resolved approvals and answered questions into their request cards", () => {
    const projection = projectConversation([
      event(1, "approval.requested", {
        approval_id: "approval-one",
        tool_call_id: "tool-one",
        request_hash: "a".repeat(64),
        name: "shell",
        reason: "Run tests",
        arguments: { command: "cargo test" },
        allow_session: true,
      }),
      event(2, "approval.resolved", {
        approval_id: "approval-one",
        request_hash: "a".repeat(64),
        decision: "approved",
      }),
      event(3, "question.requested", {
        question_id: "question-one",
        tool_call_id: "tool-two",
        question: "Continue?",
        options: ["Yes"],
      }),
      event(4, "question.answered", {
        question_id: "question-one",
        answer: "Yes",
      }),
    ]);

    expect(projection.nodes).toEqual([
      expect.objectContaining({ kind: "approval", status: "approved" }),
      expect.objectContaining({ kind: "question", status: "answered", answer: "Yes" }),
    ]);
  });
});
