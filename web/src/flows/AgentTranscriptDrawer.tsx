import { WorkerComposer } from "./WorkerComposer";
import { createStore } from "solid-js/store";
import { For, Show, createMemo } from "solid-js";
import { useAgentDirectory } from "../agents/AgentDirectory";
import type { Session } from "../api/types";
import { ConversationViewport } from "../chat/components/ConversationViewport";
import { createSessionStream } from "../chat/sessionStream";
import { projectConversation, withLiveOutput } from "../chat/projection";
import type { DelegationNode } from "../chat/projection";
import { Button } from "../components/Button";
import { Icon } from "../shell/icons";
function WorkerTranscript(props: { session: Session; agentName: string; draft: string; onDraft: (draft: string) => void }) {
  const stream = createSessionStream(() => props.session.id);
  const projection = createMemo(() => withLiveOutput(projectConversation(stream.events(), props.session.working_directory), stream.liveOutput(), props.session.agent_id));
  return <><ConversationViewport sessionId={props.session.id} nodes={projection().nodes} streamState={stream.state()} agentId={props.session.agent_id} />
    <WorkerComposer sessionId={props.session.id} agentName={props.agentName} draft={props.draft} onDraft={props.onDraft}
      activeRunId={projection().activeRunId} held={[...stream.events()].reverse().find(event => event.type === "delegation.control")?.payload.state === "held"}
      revision={`${stream.state()}:${stream.events().filter(event => event.type.startsWith("queue.")).at(-1)?.id ?? ""}`} /></>;
}
export function AgentTranscriptDrawer(props: { sessionId: string; sessions: Session[]; workers: DelegationNode[]; selectedAgent: string; onSelect: (id: string) => void; onClose: () => void; maximized?: boolean; onToggleMaximize?: () => void }) {
  const [drafts, setDrafts] = createStore<Record<string, string>>({});
  const agents = useAgentDirectory(); void agents.ensureLoaded();
  const name = (id: string) => agents.agents().find(agent => agent.id === id)?.name || id;
  const workerIds = createMemo(() => [...new Set(props.workers.filter(worker => props.sessions.some(session => session.fork_event_id === worker.id)).map(worker => worker.agentId))]);
  const selected = () => workerIds().includes(props.selectedAgent) ? props.selectedAgent : props.workers.find(worker => worker.status === "working" && workerIds().includes(worker.agentId))?.agentId || workerIds().at(-1);
  const history = createMemo(() => props.workers.filter(worker => worker.agentId === selected()).map(worker => props.sessions.find(session => session.fork_event_id === worker.id)).filter((session): session is Session => Boolean(session)));
  const latest = () => history().at(-1);
  return <aside class="agent-transcript-drawer" id="chat-flow-panel" aria-label="Agent transcripts" onKeyDown={event => { if (event.key === "Escape") props.onClose(); }}>
    <header><strong>Agents</strong><div class="agent-transcript-actions">
      <Button variant="bare" class="agent-transcript-maximize" aria-label={props.maximized ? "Restore sidebar" : "Maximize agent transcripts"}
        title={props.maximized ? "Restore sidebar" : "Maximize"} aria-pressed={Boolean(props.maximized)}
        onClick={props.onToggleMaximize}><Icon name={props.maximized ? "action.restore" : "action.maximize"} size={18} /></Button>
      <Button variant="bare" aria-label="Close agent transcripts" onClick={props.onClose}><Icon name="action.close" size={18} /></Button></div></header>
    <div class="agent-transcript-tabs" role="tablist" aria-label="Agents" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')); const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (at + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); tabs[next]?.click(); tabs[next]?.focus();
    }}><For each={workerIds()}>{id => <button type="button" role="tab" aria-selected={selected() === id} tabIndex={selected() === id ? 0 : -1} onClick={() => props.onSelect(id)}>{name(id)}<Show when={props.workers.some(worker => worker.agentId === id && worker.status === "working")}><span class="agent-working-dot" aria-label="Working" /></Show></button>}</For></div>
    <Show when={latest()?.id} keyed fallback={<p class="context-empty">Waiting for the worker transcript…</p>}>{id => <WorkerTranscript session={history().find(session => session.id === id)!} agentName={name(selected()!)} draft={drafts[id] ?? ""} onDraft={draft => setDrafts(id, draft)} />}</Show>
  </aside>;
}
