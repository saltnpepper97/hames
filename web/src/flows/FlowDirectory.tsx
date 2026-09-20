import { createContext, createEffect, createSignal, onCleanup, useContext } from "solid-js";
import type { ParentProps } from "solid-js";
import { listFlows } from "../api/client";
import { useWorkspace } from "../shell/workspace";
import type { FlowItem } from "./types";
function createDirectory() {
  const workspace = useWorkspace();
  const [items, setItems] = createSignal<FlowItem[]>([]);
  const [error, setError] = createSignal("");
  const [loaded, setLoaded] = createSignal(false);
  let generation = 0;
  let disposed = false;
  onCleanup(() => { disposed = true; });
  const refresh = async () => {
    const request = ++generation;
    try { const response = await listFlows(); if (!disposed && request === generation) { setItems(response.items); setError(""); setLoaded(true); } }
    catch (e) { if (!disposed && request === generation) { setError(String(e)); setLoaded(true); } }
  };
  createEffect(() => { if (workspace.connection() === "connected") void refresh(); });
  return { items, error, loaded, refresh };
}
const Context = createContext<ReturnType<typeof createDirectory>>();
export function FlowProvider(props: ParentProps) { return <Context.Provider value={createDirectory()}>{props.children}</Context.Provider>; }
export function useFlows() { const value = useContext(Context); if (!value) throw Error("Missing flow directory"); return value; }
