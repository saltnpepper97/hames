import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { useAgentDirectory } from "../agents/AgentDirectory";
import { getDelegatedSessions } from "../api/client";
import type { Session } from "../api/types";
import { ConversationViewport } from "../chat/components/ConversationViewport";
import { createSessionStream } from "../chat/sessionStream";
import { projectConversation, withLiveOutput } from "../chat/projection";
import type { DelegationNode } from "../chat/projection";
import { Button } from "../components/Button";
import { Icon } from "../shell/icons";
function WorkerTranscript(props: { session: Session }) {
  const stream = createSessionStream(() => props.session.id);
  const projection = createMemo(() => withLiveOutput(projectConversation(stream.events(), props.session.working_directory), stream.liveOutput(), props.session.agent_id));
  return <ConversationViewport sessionId={props.session.id} nodes={projection().nodes} streamState={stream.state()} agentId={props.session.agent_id} />;
}
export function AgentTranscriptDrawer(props: { sessionId: string; workers: DelegationNode[]; selectedAgent: string; onSelect: (id: string) => void; onClose: () => void }) {
  const agents = useAgentDirectory(); void agents.ensureLoaded();
  const name = (id: string) => agents.agents().find(agent => agent.id === id)?.name || id;
  const [sessions, setSessions] = createSignal<Session[]>([]); const [error, setError] = createSignal("");
  createEffect(() => {
    const parent = props.sessionId;
    props.workers.length;
    let disposed = false;
    let loading = false;
    const load = async () => { if (loading) return; loading = true; try { const children = await getDelegatedSessions(); if (!disposed) { setSessions(children.filter(child => child.lineage_kind === "delegation" && child.parent_session_id === parent)); setError(""); } } catch (e) { if (!disposed) setError(String(e)); } finally { loading = false; } };
    void load(); const timer = setInterval(() => void load(), 2500);
    onCleanup(() => { disposed = true; clearInterval(timer); });
  });
  const workerIds = createMemo(() => [...new Set(props.workers.map(worker => worker.agentId))]);
  const selected = () => workerIds().includes(props.selectedAgent) ? props.selectedAgent : props.workers.find(worker => worker.status === "working")?.agentId || workerIds().at(-1);
  const history = createMemo(() => props.workers.filter(worker => worker.agentId === selected()).map(worker => sessions().find(session => session.fork_event_id === worker.id)).filter((session): session is Session => Boolean(session)));
  const latest = () => history().at(-1);
  return <aside class="agent-transcript-drawer" id="chat-flow-panel" aria-label="Agent transcripts" onKeyDown={event => { if (event.key === "Escape") props.onClose(); }}>
    <header><strong>Agents</strong><Button variant="bare" aria-label="Close agent transcripts" onClick={props.onClose}><Icon name="action.close" size={18} /></Button></header>
    <div class="agent-transcript-tabs" role="tablist" aria-label="Agents" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')); const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (at + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); tabs[next]?.click(); tabs[next]?.focus();
    }}><For each={workerIds()}>{id => <button type="button" role="tab" aria-selected={selected() === id} tabIndex={selected() === id ? 0 : -1} onClick={() => props.onSelect(id)}>{name(id)}<Show when={props.workers.some(worker => worker.agentId === id && worker.status === "working")}><span class="agent-working-dot" aria-label="Working" /></Show></button>}</For></div>
    <Show when={error()}><p role="alert">{error()}</p></Show>
    <Show when={history().length > 1}><details class="agent-earlier-transcripts"><summary>Earlier work</summary><For each={history().slice(0, -1)}>{session => <details><summary>{session.title || name(session.agent_id)}</summary><Show when={true}><WorkerTranscript session={session} /></Show></details>}</For></details></Show>
    <Show when={latest()?.id} keyed fallback={<p class="context-empty">Waiting for the worker transcript…</p>}>{id => <WorkerTranscript session={history().find(session => session.id === id)!} />}</Show>
  </aside>;
}
