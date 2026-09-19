import { fireEvent, render, screen } from "@solidjs/testing-library";
import { createSignal, For } from "solid-js";
import { expect, it } from "vitest";
import type { ConversationNode } from "./projection";
import { ReasoningDisclosureProvider, reasoningDisclosureChoice } from "./reasoningDisclosure";

it("retains a choice across chunk replacements and resets for a new reasoning block", () => {
  const [nodes, setNodes] = createSignal<ConversationNode[]>([{id:"live-one",kind:"reasoning",content:"First",live:true}]);
  function Block(props: {node: ConversationNode}) {
    const [choice,setChoice] = reasoningDisclosureChoice(props.node.id);
    return <button aria-expanded={choice() ?? false} onClick={() => setChoice(!(choice() ?? false))}>Thinking</button>;
  }
  render(() => <ReasoningDisclosureProvider nodes={nodes()}><For each={nodes()}>{node=><Block node={node}/>}</For></ReasoningDisclosureProvider>);
  fireEvent.click(screen.getByRole("button"));
  setNodes([{id:"live-one",kind:"reasoning",content:"More",live:true}]);
  expect(screen.getByRole("button")).toHaveAttribute("aria-expanded","true");
  setNodes([]);
  setNodes([{id:"live-one",kind:"reasoning",content:"New block",live:true}]);
  expect(screen.getByRole("button")).toHaveAttribute("aria-expanded","false");
});
