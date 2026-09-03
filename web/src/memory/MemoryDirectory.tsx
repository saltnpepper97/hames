import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listMemories, recentSession } from "../api/client";
import type { MemoryLayer, MemoryRecord } from "../api/types";
import { useWorkspace } from "../shell/workspace";

const layers: readonly MemoryLayer[] = ["relationship", "semantic", "episodic"];
const pageSize = 200;

interface MemoryDirectoryContextValue {
  records: Accessor<MemoryRecord[]>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
}

const MemoryDirectoryContext = createContext<MemoryDirectoryContextValue>();

export function useMemoryDirectory(): MemoryDirectoryContextValue {
  const context = useContext(MemoryDirectoryContext);
  if (!context) throw new Error("Memory directory rendered outside its provider");
  return context;
}

async function loadLayer(sessionId: string, layer: MemoryLayer): Promise<MemoryRecord[]> {
  const records: MemoryRecord[] = [];
  const seen = new Set<string>();
  let offset = 0;

  while (true) {
    const page = await listMemories(sessionId, layer, offset, pageSize);
    const fresh = page.filter((record) => !seen.has(record.id));
    for (const record of fresh) seen.add(record.id);
    records.push(...fresh);
    if (page.length < pageSize || fresh.length === 0) break;
    offset += page.length;
  }

  return records;
}

export function MemoryDirectoryProvider(props: ParentProps) {
  const workspace = useWorkspace();
  const [records, setRecords] = createSignal<MemoryRecord[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [loadedPath, setLoadedPath] = createSignal("");
  const [error, setError] = createSignal("");
  let pending: Promise<void> | undefined;

  const refresh = (): Promise<void> => {
    if (pending) return pending;
    const workingDirectory = workspace.snapshot()?.bootstrap.working_directory ?? "";
    if (!workingDirectory) return Promise.resolve();

    setLoading(true);
    setError("");
    pending = (async () => {
      const session = workspace.sessions()[0] ?? await recentSession(workingDirectory);
      if (!session) {
        setRecords([]);
        setLoadedPath(workingDirectory);
        return;
      }
      const grouped = await Promise.all(layers.map((layer) => loadLayer(session.id, layer)));
      setRecords(grouped.flat());
      setLoadedPath(workingDirectory);
    })()
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Unable to load memory");
      })
      .finally(() => {
        setLoading(false);
        pending = undefined;
      });
    return pending;
  };

  const ensureLoaded = () => {
    const workingDirectory = workspace.snapshot()?.bootstrap.working_directory ?? "";
    return workingDirectory && loadedPath() === workingDirectory ? Promise.resolve() : refresh();
  };

  return (
    <MemoryDirectoryContext.Provider
      value={{
        records,
        loading,
        loaded: () => Boolean(loadedPath()),
        error,
        ensureLoaded,
        refresh,
      }}
    >
      {props.children}
    </MemoryDirectoryContext.Provider>
  );
}
