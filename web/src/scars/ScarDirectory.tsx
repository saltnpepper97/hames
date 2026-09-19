import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listScars, recentSession } from "../api/client";
import type { Scar } from "../api/types";
import { useWorkspace } from "../shell/workspace";

const statusPriority: Record<Scar["status"], number> = {
  regressed: 0,
  candidate: 1,
  open: 2,
  repair_proposed: 3,
  guarded: 4,
  healed: 5,
  dismissed: 6,
};

const severityPriority: Record<Scar["severity"], number> = { high: 0, medium: 1, low: 2 };

function compareScars(left: Scar, right: Scar): number {
  return statusPriority[left.status] - statusPriority[right.status]
    || severityPriority[left.severity] - severityPriority[right.severity]
    || right.updated_at.localeCompare(left.updated_at);
}

interface ScarDirectoryContextValue {
  scars: Accessor<Scar[]>;
  sessionId: Accessor<string>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  add: (scar: Scar) => void;
  remove: (id: string) => void;
}

const ScarDirectoryContext = createContext<ScarDirectoryContextValue>();

export function useScarDirectory(): ScarDirectoryContextValue {
  const context = useContext(ScarDirectoryContext);
  if (!context) throw new Error("Scar directory rendered outside its provider");
  return context;
}

export function ScarDirectoryProvider(props: ParentProps) {
  const workspace = useWorkspace();
  const [scars, setScars] = createSignal<Scar[]>([]);
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
        setScars([]);
        setSessionId("");
        setLoadedPath(workingDirectory);
        return;
      }
      const visible = await listScars(session.id);
      setScars([...visible].sort(compareScars));
      setSessionId(session.id);
      setLoadedPath(workingDirectory);
    })()
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Unable to load Scars");
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

  const remove = (id: string) => setScars((current) =>
    current.filter((scar) => scar.id !== id)
  );
  const add = (scar: Scar) => setScars((current) =>
    [...current.filter((candidate) => candidate.id !== scar.id), scar].sort(compareScars)
  );

  return (
    <ScarDirectoryContext.Provider value={{
      scars,
      sessionId,
      loading,
      loaded: () => Boolean(loadedPath()),
      error,
      ensureLoaded,
      refresh,
      add,
      remove,
    }}>
      {props.children}
    </ScarDirectoryContext.Provider>
  );
}
