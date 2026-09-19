import { render, screen } from "@solidjs/testing-library";
import { expect, it, vi } from "vitest";
import { DelegationChatLink } from "./DelegationChatLink";
import { getDelegatedSessions } from "../../api/client";
vi.mock("../../api/client", () => ({ getDelegatedSessions: vi.fn() }));
it("links to the matching separate child chat", async () => {
  vi.mocked(getDelegatedSessions).mockResolvedValue([
    { id: "wrong", lineage_kind: "delegation", parent_session_id: "parent", fork_event_id: "other" },
    { id: "child", lineage_kind: "delegation", parent_session_id: "parent", fork_event_id: "request", agent_id: "qwen-builder", working_directory: "/tmp" },
  ] as Awaited<ReturnType<typeof getDelegatedSessions>>);
  render(() => <DelegationChatLink node={{ id: "request", kind: "delegation", runId: "run", agentId: "qwen-builder", parentSessionId: "parent", model: "qwen", effort: "", status: "working" }} />);
  expect(await screen.findByRole("link", { name: "Open chat" })).toHaveAttribute("href", "/chat/child");
  expect(screen.getByText("Working in a separate chat.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
