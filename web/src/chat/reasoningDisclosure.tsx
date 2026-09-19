import { createContext, createEffect, createSignal, useContext, type Accessor, type ParentProps, type Setter } from "solid-js";
import type { ConversationNode } from "./projection";

type Choice = [Accessor<boolean | undefined>, Setter<boolean | undefined>];
const ReasoningChoices = createContext<Map<string, Choice>>();

export function ReasoningDisclosureProvider(props: ParentProps<{ nodes: readonly ConversationNode[] }>) {
  const choices = new Map<string, Choice>();
  createEffect(() => {
    const current = new Set(props.nodes.filter(node => node.kind === "reasoning").map(node => node.id));
    for (const id of choices.keys()) if (!current.has(id)) choices.delete(id);
  });
  return <ReasoningChoices.Provider value={choices}>{props.children}</ReasoningChoices.Provider>;
}

/** Live projection objects can be replaced on each chunk; the user's choice
 * belongs to the reasoning block, not to a particular renderer instance. */
export function reasoningDisclosureChoice(id: string): Choice {
  const choices = useContext(ReasoningChoices);
  const existing = choices?.get(id);
  if (existing) return existing;
  const choice = createSignal<boolean>();
  choices?.set(id, choice);
  return choice;
}
