import { createContext, createSignal, onCleanup, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import {
  authorSkill as requestSkillAuthoring,
  listAvailableSkills,
  listSkillJobs,
  recentSession,
} from "../api/client";
import type { SkillCatalogEntry, SkillJob } from "../api/types";
import { useWorkspace } from "../shell/workspace";

interface SkillDirectoryContextValue {
  skills: Accessor<SkillCatalogEntry[]>;
  sessionId: Accessor<string>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  authoringJob: Accessor<SkillJob | undefined>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  authorSkill: (goal: string, scope: "workspace" | "agent") => Promise<SkillJob>;
  remove: (slug: string) => void;
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
  const [authoringJob, setAuthoringJob] = createSignal<SkillJob>();
  let pending: Promise<void> | undefined;
  let jobPollTimer: ReturnType<typeof setTimeout> | undefined;

  const refresh = (): Promise<void> => {
    if (pending) return pending;
    const workingDirectory = workspace.workingDirectory();
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
    const workingDirectory = workspace.workingDirectory();
    return workingDirectory && loadedPath() === workingDirectory ? Promise.resolve() : refresh();
  };

  const trackAuthoringJob = (authoringSessionId: string, jobId: string) => {
    clearTimeout(jobPollTimer);
    jobPollTimer = setTimeout(() => {
      void listSkillJobs(authoringSessionId)
        .then(async (jobs) => {
          const job = jobs.find((candidate) => candidate.id === jobId);
          if (!job) return;
          setAuthoringJob(job);
          if (["pending", "running", "budget_wait"].includes(job.status)) {
            trackAuthoringJob(authoringSessionId, jobId);
          } else if (job.status === "completed") {
            await refresh();
          }
        })
        .catch(() => undefined);
    }, 1_500);
  };

  const authorSkill = async (goal: string, scope: "workspace" | "agent") => {
    await ensureLoaded();
    let authoringSessionId = sessionId();
    if (!authoringSessionId) {
      const session = await workspace.createChat();
      authoringSessionId = session.id;
      setSessionId(session.id);
    }
    const job = await requestSkillAuthoring(authoringSessionId, goal, scope);
    setAuthoringJob(job);
    trackAuthoringJob(authoringSessionId, job.id);
    return job;
  };

  onCleanup(() => clearTimeout(jobPollTimer));

  return (
    <SkillDirectoryContext.Provider value={{
      skills,
      sessionId,
      loading,
      loaded: () => Boolean(loadedPath()),
      error,
      authoringJob,
      ensureLoaded,
      refresh,
      authorSkill,
      remove: (slug) => setSkills((current) => current.filter((skill) => skill.slug !== slug)),
    }}>
      {props.children}
    </SkillDirectoryContext.Provider>
  );
}
