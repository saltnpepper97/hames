import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { For, createSignal } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import { AgentTranscriptDrawer } from "./AgentTranscriptDrawer";
import { IconProvider } from "../shell/icons";
import { hamesIconPack } from "../plugins/icons/hames";
import type { Session } from "../api/types";
import type { DelegationNode } from "../chat/projection";
vi.mock("../agents/AgentDirectory", () => ({ useAgentDirectory: () => ({ ensureLoaded: async () => {}, agents: () => [{ id: "builder", name: "Builder" }, { id: "reviewer", name: "Reviewer" }] }) }));
const sessions = [
  { id: "build-child", parent_session_id: "parent", fork_event_id: "build", lineage_kind: "delegation", agent_id: "builder" },
  { id: "review-child", parent_session_id: "parent", fork_event_id: "review", lineage_kind: "delegation", agent_id: "reviewer" },
] as Session[];
vi.mock("../chat/components/MessageQueue", () => ({ MessageQueue: () => null }));
vi.mock("../api/client", () => ({ sendMessage: vi.fn().mockResolvedValue({ disposition: "queued" }) }));
vi.mock("../chat/sessionStream", () => ({ createSessionStream: (id: () => string) => ({ events: () => [{ id: id(), sequence: 1, session_id: id(), run_id: "run", type: "assistant.message", payload: { content: `Transcript of ${id()}`, status: "completed" } }], state: () => "live", liveOutput: () => undefined }) }));
vi.mock("../chat/components/ConversationViewport", () => ({ ConversationViewport: (props: { nodes: { content?: string }[] }) => <div><For each={props.nodes}>{node => <p>{node.content}</p>}</For></div> }));
afterEach(cleanup);
it("shows only worker transcript tabs and keeps them inside the parent conversation", async () => {
  const [selected, setSelected] = createSignal("builder");
  const workers: DelegationNode[] = ["builder", "reviewer"].map((agentId, i) => ({ id: i ? "review" : "build", kind: "delegation", runId: "run", agentId, model: "model", effort: "", status: i ? "working" : "completed" }));
  render(() => <IconProvider pack={hamesIconPack}><AgentTranscriptDrawer sessionId="parent" sessions={sessions} workers={workers} selectedAgent={selected()} onSelect={setSelected} onClose={() => {}} /></IconProvider>);
  await screen.findByText("Transcript of build-child");
  fireEvent.input(screen.getByRole("textbox", { name: "Message Builder" }), { target: { value: "Preserve this builder draft" } });
  fireEvent.click(screen.getByRole("tab", { name: /Reviewer/ }));
  expect(screen.getByRole("textbox", { name: "Message Reviewer" })).toHaveValue("");
  await screen.findByText("Transcript of review-child");
  expect(screen.queryByText("Transcript of build-child")).not.toBeInTheDocument();
  expect(screen.queryByText("Transcript of unrelated-child")).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Overview" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Inputs" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /Builder/ }));
  expect(screen.getByRole("textbox", { name: "Message Builder" })).toHaveValue("Preserve this builder draft");
});

it("maximizes and restores without changing the selected transcript", async () => {
  const [maximized, setMaximized] = createSignal(false);
  const workers: DelegationNode[] = [{ id: "build", kind: "delegation", runId: "run", agentId: "builder", model: "model", effort: "", status: "working" }];
  render(() => <IconProvider pack={hamesIconPack}><AgentTranscriptDrawer sessionId="parent" sessions={sessions} workers={workers} selectedAgent="builder" onSelect={() => {}} onClose={() => {}} maximized={maximized()} onToggleMaximize={() => setMaximized(!maximized())} /></IconProvider>);
  const transcript = await screen.findByText("Transcript of build-child");
  fireEvent.click(screen.getByRole("button", { name: "Maximize agent transcripts" }));
  expect(screen.getByRole("button", { name: "Restore sidebar" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Transcript of build-child")).toBe(transcript);
  fireEvent.click(screen.getByRole("button", { name: "Restore sidebar" }));
  expect(screen.getByRole("button", { name: "Maximize agent transcripts" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("Transcript of build-child")).toBe(transcript);
});
