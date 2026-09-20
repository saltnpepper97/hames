import { fireEvent, render } from "@solidjs/testing-library";
import { expect, it, vi } from "vitest";
import { createSignal } from "solid-js";
import { ConversationViewport } from "./ConversationViewport";
import type { EditNode } from "../editGroups";
vi.mock("../../shell/icons", () => ({ Icon: () => <span /> }));
vi.mock("../../agents/AgentDirectory", () => ({ useAgentDirectory: () => ({ ensureLoaded: async () => {} }) }));
vi.mock("../../shell/pluginContext", () => ({ useWebPlugins: () => ({ conversationNodes: new Map([
  ["tool", { component: (props: { node: EditNode }) => <p>{props.node.content} {props.node.status}</p> }],
]) }) }));
it("shows one collapsed row, preserves its expansion, and updates live tool status", () => {
  const base: EditNode = { id: "a", kind: "tool", name: "edit_file", status: "completed", runId: "r", sessionId: "s", workingDirectory: "/p", arguments: { path: "plan.md" }, content: "first diff" };
  const [nodes, setNodes] = createSignal<EditNode[]>([base, { ...base, id: "b", content: "second diff" }]);
  const { container, getByText } = render(() => <ConversationViewport nodes={nodes()} streamState="live" agentId="default" />);
  const group = container.querySelector<HTMLDetailsElement>(".edit-group")!;
  expect(group.open).toBe(false);
  expect(group.querySelector("summary")?.textContent).toContain("plan.md · 2 edits");
  fireEvent.click(group.querySelector("summary")!);
  expect(group.open).toBe(true);
  expect(getByText("first diff completed")).toBeInTheDocument();
  setNodes([...nodes(), { ...base, id: "c", content: "third diff" }]);
  expect(container.querySelector(".edit-group")).toBe(group);
  expect(group.open).toBe(true);
  expect(group.querySelector("summary")?.textContent).toContain("3 edits");
  setNodes([{ ...base, status: "started" }]);
  expect(getByText("first diff started")).toBeInTheDocument();
  setNodes([{ ...base, status: "failed" }]);
  expect(getByText("first diff failed")).toBeInTheDocument();
});
