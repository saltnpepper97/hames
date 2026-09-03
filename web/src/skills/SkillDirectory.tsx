import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listAvailableSkills, recentSession } from "../api/client";
import type { SkillCatalogEntry } from "../api/types";
import { useWorkspace } from "../shell/workspace";

interface SkillDirectoryContextValue {
  skills: Accessor<SkillCatalogEntry[]>;
  sessionId: Accessor<string>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SkillDirectoryContext = createContext<SkillDirectoryContextValue>();

export function useSkillDirectory(): SkillDirectoryContextValue {
  const context = useContext(SkillDirectoryContext);
  if (!context) throw new Error("Skill directory rendered outside its provider");
  return context;
}

export function SkillDirectoryProvider(props: ParentProps) {
  const workspace = useWorkspace();
  const [skills, setSkills] = createSignal<SkillCatalogEntry[]>([]);
  const [sessionId, setSessionId] = createSignal("");
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
        setSkills([]);
        setSessionId("");
        setLoadedPath(workingDirectory);
        return;
      }
      const catalog = await listAvailableSkills(session.id);
      setSkills([...catalog].sort((left, right) => left.name.localeCompare(right.name)));
      setSessionId(session.id);
      setLoadedPath(workingDirectory);
    })()
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Unable to load Skills");
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
    <SkillDirectoryContext.Provider value={{
      skills,
      sessionId,
      loading,
      loaded: () => Boolean(loadedPath()),
      error,
      ensureLoaded,
      refresh,
    }}>
      {props.children}
    </SkillDirectoryContext.Provider>
  );
}
