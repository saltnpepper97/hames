import { render, screen, waitFor, cleanup } from "@solidjs/testing-library";
import { createSignal, For } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import type { Session } from "../api/types";
import type { DelegationNode } from "../chat/projection";
import { createCurrentWorkerSessions } from "./currentWorkers";
const fetchSessions = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ getDelegatedSessions: fetchSessions }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("has no tab until a current child exists and ignores late responses from the previous run", async () => {
  let resolveOld!: (sessions: Session[]) => void;
  let resolveNew!: (sessions: Session[]) => void;
  fetchSessions.mockReturnValueOnce(new Promise<Session[]>(resolve => { resolveOld = resolve; }))
    .mockReturnValueOnce(new Promise<Session[]>(resolve => { resolveNew = resolve; }));
  const worker = (id: string, runId: string): DelegationNode => ({ id, runId, kind: "delegation", agentId: "builder", status: "working", model: "m", effort: "" });
  const [workers, setWorkers] = createSignal([worker("old", "old-run")]);
  render(() => {
    const sessions = createCurrentWorkerSessions(() => "parent", workers);
    return <For each={sessions()}>{session => <button role="tab">{session.id}</button>}</For>;
  });
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  setWorkers([worker("new", "new-run")]);
  const child = (id: string, fork_event_id: string): Session => ({ id, fork_event_id, lineage_kind: "delegation", parent_session_id: "parent" } as Session);
  resolveNew([child("old-child", "old"), child("new-child", "new")]);
  await screen.findByRole("tab", { name: "new-child" });
  resolveOld([child("old-child", "old")]);
  await waitFor(() => expect(screen.queryByRole("tab", { name: "old-child" })).not.toBeInTheDocument());
  expect(screen.getByRole("tab", { name: "new-child" })).toBeInTheDocument();
  setWorkers([]);
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
});
