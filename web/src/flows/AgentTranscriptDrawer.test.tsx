import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { For, createSignal } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import { AgentTranscriptDrawer } from "./AgentTranscriptDrawer";
import { IconProvider } from "../shell/icons";
import { hamesIconPack } from "../plugins/icons/hames";
import type { DelegationNode } from "../chat/projection";
vi.mock("../agents/AgentDirectory", () => ({ useAgentDirectory: () => ({ ensureLoaded: async () => {}, agents: () => [{ id: "builder", name: "Builder" }, { id: "reviewer", name: "Reviewer" }] }) }));
vi.mock("../api/client", () => ({ getDelegatedSessions: async () => [
  { id: "build-child", parent_session_id: "parent", fork_event_id: "build", lineage_kind: "delegation", agent_id: "builder" },
  { id: "review-child", parent_session_id: "parent", fork_event_id: "review", lineage_kind: "delegation", agent_id: "reviewer" },
  { id: "unrelated-child", parent_session_id: "other", fork_event_id: "build", lineage_kind: "delegation", agent_id: "builder" },
] }));
vi.mock("../chat/sessionStream", () => ({ createSessionStream: (id: () => string) => ({ events: () => [{ id: id(), sequence: 1, session_id: id(), run_id: "run", type: "assistant.message", payload: { content: `Transcript of ${id()}`, status: "completed" } }], state: () => "live", liveOutput: () => undefined }) }));
vi.mock("../chat/components/ConversationViewport", () => ({ ConversationViewport: (props: { nodes: { content?: string }[] }) => <div><For each={props.nodes}>{node => <p>{node.content}</p>}</For></div> }));
afterEach(cleanup);
it("shows only worker transcript tabs and keeps them inside the parent conversation", async () => {
  const [selected, setSelected] = createSignal("builder");
  const workers: DelegationNode[] = ["builder", "reviewer"].map((agentId, i) => ({ id: i ? "review" : "build", kind: "delegation", runId: "run", agentId, model: "model", effort: "", status: i ? "working" : "completed" }));
  render(() => <IconProvider pack={hamesIconPack}><AgentTranscriptDrawer sessionId="parent" workers={workers} selectedAgent={selected()} onSelect={setSelected} onClose={() => {}} /></IconProvider>);
  await screen.findByText("Transcript of build-child");
  fireEvent.click(screen.getByRole("tab", { name: /Reviewer/ }));
  await screen.findByText("Transcript of review-child");
  expect(screen.queryByText("Transcript of build-child")).not.toBeInTheDocument();
  expect(screen.queryByText("Transcript of unrelated-child")).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Overview" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Inputs" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
