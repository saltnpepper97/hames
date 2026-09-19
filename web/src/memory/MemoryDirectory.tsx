import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listMemories, recentSession } from "../api/client";
import type { MemoryLayer, MemoryRecord } from "../api/types";
import { useWorkspace } from "../shell/workspace";

const layers: readonly MemoryLayer[] = ["relationship", "semantic", "episodic"];
const pageSize = 200;

interface MemoryDirectoryContextValue {
  records: Accessor<MemoryRecord[]>;
  sessionId: Accessor<string>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  add: (record: MemoryRecord) => void;
  remove: (id: string) => void;
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
  const [sessionId, setSessionId] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [loadedPath, setLoadedPath] = createSignal("");
  const [error, setError] = createSignal("");
  let pending: Promise<void> | undefined;

  const refresh = (): Promise<void> => {
    if (pending) return pending;
    const workingDirectory = workspace.workingDirectory();
    if (!workingDirectory) return Promise.resolve();

    setLoading(true);
    setError("");
    pending = (async () => {
      const session = workspace.sessions()[0] ?? await recentSession(workingDirectory);
      if (!session) {
        setRecords([]);
        setSessionId("");
        setLoadedPath(workingDirectory);
        return;
      }
      const grouped = await Promise.all(layers.map((layer) => loadLayer(session.id, layer)));
      setRecords(grouped.flat());
      setSessionId(session.id);
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
    const workingDirectory = workspace.workingDirectory();
    return workingDirectory && loadedPath() === workingDirectory ? Promise.resolve() : refresh();
  };

  const add = (record: MemoryRecord) => setRecords((current) => [
    record,
    ...current.filter((candidate) => candidate.id !== record.id),
  ]);
  const remove = (id: string) => setRecords((current) =>
    current.filter((record) => record.id !== id)
  );

  return (
    <MemoryDirectoryContext.Provider
      value={{
        records,
        sessionId,
        loading,
        loaded: () => Boolean(loadedPath()),
        error,
        ensureLoaded,
        refresh,
        add,
        remove,
      }}
    >
      {props.children}
    </MemoryDirectoryContext.Provider>
  );
}
