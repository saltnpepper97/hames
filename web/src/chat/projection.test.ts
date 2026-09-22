import { describe, expect, it } from "vitest";
import type { HamesEvent } from "../api/types";
import { projectConversation, withLiveOutput } from "./projection";

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
  const contextSource = {
    source_id: "agent.default.instructions", source_type: "agent",
    content_hash: "instructions-v1", selected_tokens: 100, truncation: "none",
    source_path: "/agents/default.md",
  };
  const contextPayload = {
    provider: "codex", model: "model-one", agent_id: "default",
    selected_sources: [contextSource], omitted_sources: [],
    source_order: [contextSource.source_id],
  };

  it("shows unchanged context once across calls and runs without changing the events", () => {
    const history = { source_id: "conversation.turn.one", source_type: "conversation", content_hash: "growing" };
    const reasoning = { source_id: "reasoning.one", source_type: "reasoning", visibility: "audit" };
    const events = [
      event(1, "context.compiled", { ...contextPayload, environment: { observed_at: "first", workspace: { cwd: "/project" } } }),
      event(2, "tool.completed", { name: "read", content: "file contents" }),
      event(3, "context.compiled", {
        ...contextPayload, request_hash: "new-request", request_snapshot_blob_hash: "new-blob",
        estimated_input_tokens: 5000, contributing_event_ids: ["new-message"],
        selected_sources: [Object.fromEntries(Object.entries(contextSource).reverse()), history],
        omitted_sources: [reasoning], source_order: [contextSource.source_id, history.source_id],
        environment: { workspace: { cwd: "/project" }, observed_at: "later" },
      }, "run-two"),
    ];
    const before = JSON.stringify(events);
    expect(projectConversation(events).nodes.filter((node) => node.kind === "context").map((node) => node.id)).toEqual(["event-1"]);
    expect(JSON.stringify(events)).toBe(before);
    expect(events.filter((entry) => entry.type === "context.compiled")).toHaveLength(2);
  });

  it.each([
    ["same-length content edits", { selected_sources: [{ ...contextSource, content_hash: "instructions-v2" }] }],
    ["source paths", { selected_sources: [{ ...contextSource, source_path: "/other.md" }] }],
    ["truncation", { selected_sources: [{ ...contextSource, truncation: "budget", selected_tokens: 50 }] }],
    ["omission", { selected_sources: [], omitted_sources: [contextSource] }],
    ["new tools", { selected_sources: [contextSource, { source_id: "tool.schemas", source_type: "tools", content_hash: "tools-v2" }] }],
    ["loaded Skills", { selected_sources: [contextSource, { source_id: "skill.one", source_type: "skill", content_hash: "skill-v1" }] }],
    ["compaction", { selected_sources: [contextSource, { source_id: "compaction.one", source_type: "compaction", content_hash: "summary" }] }],
    ["memory provenance", { selected_sources: [{ ...contextSource, provenance_event_ids: ["new-origin"] }] }],
    ["environment", { environment: { workspace: { cwd: "/other" } } }],
    ["model", { model: "model-two" }],
    ["reasoning effort", { reasoning_effort: "high" }],
  ])("shows meaningful changes to %s, including a return to prior context", (_label, change) => {
    const nodes = projectConversation([
      event(1, "context.compiled", contextPayload),
      event(2, "context.compiled", { ...contextPayload, ...change }),
      event(3, "context.compiled", contextPayload),
    ]).nodes;
    expect(nodes.map((node) => node.id)).toEqual(["event-1", "event-2", "event-3"]);
  });

  it("ignores git dirty counts when deciding whether context changed", () => {
    const nodes = projectConversation([
      event(1, "context.compiled", {
        ...contextPayload,
        environment: { workspace: { cwd: "/project", dirty: false, changed_files: 0 } },
      }),
      event(2, "context.compiled", {
        ...contextPayload,
        request_hash: "later",
        environment: { workspace: { cwd: "/project", dirty: true, changed_files: 4 } },
      }),
    ]).nodes;
    expect(nodes.filter((node) => node.kind === "context").map((node) => node.id)).toEqual(["event-1"]);
  });

  it("keeps context comparisons separate for interleaved sessions and agents", () => {
    const nodes = projectConversation([
      event(1, "context.compiled", contextPayload),
      { ...event(2, "context.compiled", contextPayload), session_id: "session-two" },
      { ...event(3, "context.compiled", contextPayload), agent_id: "reviewer" },
      event(4, "context.compiled", contextPayload),
      { ...event(5, "context.compiled", contextPayload), session_id: "session-two" },
      { ...event(6, "context.compiled", contextPayload), agent_id: "reviewer" },
    ]).nodes;
    expect(nodes.map((node) => node.id)).toEqual(["event-1", "event-2", "event-3"]);
  });

  it("projects durable messages, tool state, and the active run", () => {
    const projection = projectConversation([
      event(1, "user.message", { content: "Inspect it", purpose: "turn" }),
      event(2, "run.started", {}),
      event(3, "context.compiled", {
        provider: "codex",
        model: "gpt-5.6-sol",
        estimated_input_tokens: 30_000,
        selected_sources: [{
          source_id: "agent:default",
          source_type: "agent",
          selected_tokens: 1_200,
          estimated_tokens: 1_200,
          source_path: "/home/.hames/agents/default/AGENT.md",
        }],
        omitted_sources: [{
          source_id: "memory:old",
          source_type: "memory",
          estimated_tokens: 400,
          reason: "budget",
        }],
      }),
      event(4, "assistant.reasoning", { content: "I will inspect it.", status: "completed" }),
      event(5, "tool.requested", { tool_call_id: "tool-one", name: "read", arguments: { path: "README.md" } }),
      event(6, "tool.completed", {
        tool_call_id: "tool-one",
        name: "read",
        status: "completed",
        summary: "Read README",
        content: "# Hames",
        structured_data: { path: "README.md", lines: 1 },
        truncated: true,
        duration_seconds: 0.25,
      }),
    ]);

    expect(projection.activeRunId).toBe("run-one");
    expect(projection.nodes).toEqual([
      expect.objectContaining({ kind: "user", content: "Inspect it" }),
      expect.objectContaining({
        kind: "context",
        eventId: "event-3",
        provider: "codex",
        model: "gpt-5.6-sol",
        estimatedTokens: 30_000,
        selectedSources: [expect.objectContaining({ id: "agent:default", tokens: 1_200 })],
        omittedSources: [expect.objectContaining({ id: "memory:old", reason: "budget" })],
      }),
      expect.objectContaining({ kind: "reasoning", content: "I will inspect it.", agentId: "default" }),
      expect.objectContaining({
        kind: "tool",
        sessionId: "session-one",
        name: "read",
        status: "completed",
        summary: "Read README",
        structuredData: { path: "README.md", lines: 1 },
        truncated: true,
        durationSeconds: 0.25,
      }),
    ]);
  });

  it("settles runs and appends current transient output", () => {
    const projection = withLiveOutput(
      projectConversation([event(1, "run.started", {}), event(2, "run.completed", {})]),
      { runId: "run-two", reasoning: "Checking", text: "Answering" },
      "reviewer",
    );

    expect(projection.activeRunId).toBeUndefined();
    expect(projection.nodes.slice(-2)).toEqual([
      expect.objectContaining({ kind: "reasoning", content: "Checking", live: true, agentId: "reviewer" }),
      expect.objectContaining({ kind: "assistant", content: "Answering", live: true, agentId: "reviewer" }),
    ]);
  });

  it("folds context compaction into one lifecycle card and tracks it as active work", () => {
    const running = projectConversation([
      event(1, "context.compaction.started", {
        compaction_id: "compact-one",
        trigger: "manual",
        preserve_recent_turns: 4,
      }, "compact-one"),
    ]);
    expect(running.activeRunId).toBe("compact-one");
    expect(running.nodes).toEqual([
      expect.objectContaining({ kind: "compaction", compactionId: "compact-one", status: "running" }),
    ]);

    const completed = projectConversation([
      event(1, "context.compaction.started", {
        compaction_id: "compact-one",
        trigger: "manual",
      }, "compact-one"),
      event(2, "context.compaction.completed", {
        compaction_id: "compact-one",
        trigger: "manual",
        summary: "Retained the important decisions.",
        turns_compacted: 6,
        before_tokens: 30_000,
        after_tokens: 8_000,
        partial: false,
      }, "compact-one"),
    ]);
    expect(completed.activeRunId).toBeUndefined();
    expect(completed.nodes).toEqual([
      expect.objectContaining({
        kind: "compaction",
        status: "completed",
        summary: "Retained the important decisions.",
        turnsCompacted: 6,
        beforeTokens: 30_000,
        afterTokens: 8_000,
      }),
    ]);
  });

  it("folds wrap-up jobs and idle dreams into distinct transcript lifecycles", () => {
    const responseStarted = event(3, "model.response.started", {}, "memory-job");
    responseStarted.correlation_id = "memory-job";
    const running = projectConversation([
      event(1, "memory.job.queued", {
        job_id: "memory-job",
        kind: "extraction",
        status: "pending",
        attempts: 0,
      }, "source-run"),
      event(2, "memory.job.started", {
        job_id: "memory-job",
        kind: "extraction",
        status: "running",
        attempts: 1,
      }, "source-run"),
      responseStarted,
    ]);
    expect(running.nodes).toEqual([
      expect.objectContaining({
        kind: "maintenance",
        category: "wrap-up",
        phase: "running",
        detail: "Reviewing this turn for durable memory",
      }),
    ]);

    const completed = projectConversation([
      ...[
        event(1, "memory.job.queued", {
          job_id: "memory-job",
          kind: "extraction",
          status: "pending",
          attempts: 0,
        }, "source-run"),
        event(2, "memory.job.completed", {
          job_id: "memory-job",
          kind: "extraction",
          status: "completed",
          attempts: 1,
        }, "source-run"),
      ],
      event(3, "dream.started", { dream_id: "dream-one", status: "running" }, null),
      event(4, "dream.completed", {
        dream_id: "dream-one",
        status: "completed",
        memories_reconciled: 2,
        skills_reconciled: 1,
        scars_repaired: 1,
      }, null),
    ]);
    expect(completed.nodes).toEqual([
      expect.objectContaining({
        category: "wrap-up",
        phase: "completed",
        detail: "Turn memory reviewed",
      }),
      expect.objectContaining({
        category: "dream",
        phase: "completed",
        detail: "Reconciled 2 memories · 1 Skill · 1 scar",
      }),
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
      expect.objectContaining({
        kind: "question",
        answerType: "single_choice",
        minSelections: 1,
        maxSelections: 1,
        status: "answered",
        answer: "Yes",
      }),
    ]);
  });

  it("projects multiple-choice and text question contracts", () => {
    const projection = projectConversation([
      event(1, "question.requested", {
        question_id: "many",
        question: "Which checks?",
        answer_type: "multiple_choice",
        options: ["Unit", "Browser"],
        min_selections: 1,
        max_selections: 2,
      }),
      event(2, "question.requested", {
        question_id: "text",
        question: "What name?",
        answer_type: "text",
        placeholder: "Release name",
        options: [],
      }),
    ]);

    expect(projection.nodes).toEqual([
      expect.objectContaining({
        kind: "question",
        answerType: "multiple_choice",
        minSelections: 1,
        maxSelections: 2,
      }),
      expect.objectContaining({
        kind: "question",
        answerType: "text",
        placeholder: "Release name",
      }),
    ]);
  });

  it("projects the current task list while leaving task tool calls chronological", () => {
    const projection = projectConversation([
      event(1, "tasks.replaced", {
        title: "Transcript polish",
        revision: 1,
        items: [
          { id: "task-one", text: "Build task strip", status: "in_progress", position: 0 },
          { id: "task-two", text: "Run web tests", status: "pending", position: 1 },
        ],
      }),
      event(2, "task.updated", { task_id: "task-one", status: "completed" }),
      event(3, "task.updated", { task_id: "task-two", status: "in_progress" }),
      event(4, "tool.completed", {
        tool_call_id: "task-tool",
        name: "task_update",
        status: "completed",
        summary: "marked Run web tests in progress",
        structured_data: {
          task_list: {
            title: "Transcript polish",
            revision: 3,
            updated_at: "2026-09-02T18:00:03Z",
            items: [
              { id: "task-one", text: "Build task strip", status: "completed", position: 0 },
              { id: "task-two", text: "Run web tests", status: "in_progress", position: 1 },
            ],
          },
        },
      }),
    ]);

    expect(projection.tasks).toEqual({
      title: "Transcript polish",
      revision: 3,
      updatedAt: "2026-09-02T18:00:03Z",
      items: [
        { id: "task-one", text: "Build task strip", status: "completed", position: 0 },
        { id: "task-two", text: "Run web tests", status: "in_progress", position: 1 },
      ],
    });
    expect(projection.nodes).toEqual([
      expect.objectContaining({ kind: "tool", name: "task_update" }),
    ]);
  });
});


describe("delegated worker activity", () => {
  const start = event(1, "run.started", {});
  const qwen = event(2, "delegation.requested", {target_agent_id: "qwen-builder", provider: "local", model: "qwen", reasoning_effort: "xhigh"});
  const done = {...event(3, "delegation.completed", {status: "completed"}), causation_id: qwen.id};
  const luna = event(4, "delegation.requested", {target_agent_id: "luna-reviewer", provider: "codex", model: "luna", reasoning_effort: "xhigh"});
  it("replays the actual model and hands status to the next worker", () => {
    const result = projectConversation([start, qwen, done, luna]);
    expect(result.activeWorker).toMatchObject({agentId: "luna-reviewer", model: "codex/luna", effort: "xhigh"});
    expect(result.nodes.filter(n => n.kind === "delegation").map(n => n.status)).toEqual(["completed", "working"]);
  });
  it("clears failed and cancelled workers without claiming a review passed", () => {
    const failed = {...event(5, "delegation.failed", {status: "failed"}), causation_id: luna.id};
    expect(projectConversation([start, qwen, done, luna, failed]).activeWorker).toBeUndefined();
    const followup = {...event(6, "delegation.followup.completed", {status: "completed"}), causation_id: luna.id};
    expect(projectConversation([start, qwen, done, luna, failed, followup]).nodes
      .find(n => n.kind === "delegation" && n.id === luna.id)).toMatchObject({status: "completed"});
    const cancelled = projectConversation([start, qwen, event(7, "run.cancelled", {})]);
    expect(cancelled.activeWorker).toBeUndefined();
    expect(cancelled.nodes.find(n => n.kind === "delegation")).toMatchObject({status: "cancelled"});
  });
  it("keeps workers visible when their lead stops or is steered", () => {
    for (const reason of ["stopped", "steered"]) {
      const stopped = event(7, "run.cancelled", { reason, children_preserved: true });
      const result = projectConversation([start, qwen, stopped]);
      expect(result.activeWorker).toMatchObject({ agentId: "qwen-builder", status: "working" });
      expect(result.activeRunId).toBeUndefined();
      const finished = projectConversation([start, qwen, stopped, done]);
      expect(finished.activeWorker).toBeUndefined();
      expect(finished.nodes.find(n => n.kind === "delegation")).toMatchObject({ status: "completed" });
    }
  });
  it("does not invent model details for older events", () => {
    const result = projectConversation([start, event(2, "delegation.requested", {target_agent_id: "old-agent"})]);
    expect(result.activeWorker).toMatchObject({agentId: "old-agent", model: "", effort: ""});
  });
});

it("shows a stopping child until cancellation is confirmed without ending the parent run", () => {
  const start = event(1, "run.started", {});
  const child = event(2, "delegation.requested", {target_agent_id: "worker"});
  const stopping = {...event(3, "delegation.stopping", {status: "stopping"}), causation_id: child.id};
  const cancelled = {...event(4, "delegation.failed", {status: "cancelled"}), causation_id: child.id};
  const pending = projectConversation([start, child, stopping]);
  expect(pending.activeWorker?.status).toBe("stopping");
  expect(pending.activeRunId).toBe(start.run_id);
  const done = projectConversation([start, child, stopping, cancelled]);
  expect(done.activeWorker).toBeUndefined();
  expect(done.nodes.find(n => n.kind === "delegation")).toMatchObject({status: "cancelled"});
  expect(done.activeRunId).toBe(start.run_id);
});

it("exposes a worker transcript while delegation is still running", () => {
  const requested = event(1, "delegation.requested", { target_agent_id: "builder" });
  const started = { ...event(2, "delegation.started", { child_session_id: "worker-chat" }), causation_id: requested.id };
  const result = projectConversation([requested, started]);
  expect(result.nodes.find(n => n.kind === "delegation")).toMatchObject({ childSessionId: "worker-chat", status: "working" });
  const completed = { ...event(3, "delegation.completed", { child_session_id: "worker-chat", status: "completed" }), causation_id: requested.id };
  expect(projectConversation([requested, completed]).nodes.find(n => n.kind === "delegation"))
    .toMatchObject({ childSessionId: "worker-chat", status: "completed" });
});
