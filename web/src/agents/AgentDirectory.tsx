import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listAgents } from "../api/client";
import type { AgentPublic } from "../api/types";

interface AgentDirectoryContextValue {
  agents: Accessor<AgentPublic[]>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  update: (agent: AgentPublic) => void;
}

const AgentDirectoryContext = createContext<AgentDirectoryContextValue>();

export function useAgentDirectory(): AgentDirectoryContextValue {
  const context = useContext(AgentDirectoryContext);
  if (!context) throw new Error("Agent directory rendered outside its provider");
  return context;
}

export function AgentDirectoryProvider(props: ParentProps) {
  const [agents, setAgents] = createSignal<AgentPublic[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [loaded, setLoaded] = createSignal(false);
  const [error, setError] = createSignal("");
  let pending: Promise<void> | undefined;

  const refresh = (): Promise<void> => {
    if (pending) return pending;
    setLoading(true);
    setError("");
    pending = listAgents()
      .then((next) => {
        setAgents(next);
        setLoaded(true);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Unable to load agents");
      })
      .finally(() => {
        setLoading(false);
        pending = undefined;
      });
    return pending;
  };

  const ensureLoaded = () => loaded() ? Promise.resolve() : refresh();

  const update = (updated: AgentPublic) => {
    setAgents((current) => {
      const index = current.findIndex((agent) => agent.id === updated.id);
      if (index < 0) return [...current, updated];
      return current.map((agent) => agent.id === updated.id ? updated : agent);
    });
  };

  return (
    <AgentDirectoryContext.Provider
      value={{ agents, loading, loaded, error, ensureLoaded, refresh, update }}
    >
      {props.children}
    </AgentDirectoryContext.Provider>
  );
}
