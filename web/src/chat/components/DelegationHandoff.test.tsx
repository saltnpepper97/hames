import { render, screen } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { expect, it } from "vitest";
import { coreConversationNodes } from "../../plugins/core/conversationNodes";
import type { ConversationNode } from "../projection";

it("shows worker identity and actual model, then removes the working state on completion", () => {
  const View = coreConversationNodes.find(entry => entry.kind === "delegation")!.component;
  const [node, setNode] = createSignal<ConversationNode>({
    id: "request", kind: "delegation", runId: "run", agentId: "luna-reviewer",
    model: "codex/gpt-5.6-luna", effort: "xhigh", status: "working",
  });
  const { container } = render(() => <View node={node()} />);
  expect(screen.getByText("luna-reviewer · Working")).toBeInTheDocument();
  expect(screen.getByText("codex/gpt-5.6-luna · xhigh")).toBeInTheDocument();
  setNode(current => ({ ...current, status: "completed" } as ConversationNode));
  expect(screen.getByText("luna-reviewer · Finished")).toBeInTheDocument();
  expect(container.querySelector('[data-state="working"]')).toBeNull();
  expect(screen.queryByText(/Review passed/)).toBeNull();
});
