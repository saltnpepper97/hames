import { A } from "@solidjs/router";
import { For, Show, createMemo, createResource, createSignal } from "solid-js";
import { getFlowRun, getFlowRuns } from "../api/client";
import { ConversationViewport } from "../chat/components/ConversationViewport";
import { projectConversation } from "../chat/projection";
import { useAgentDirectory } from "../agents/AgentDirectory";
import { Button } from "../components/Button";

export function FlowHistory(props: { identifier: string }) {
  const agents = useAgentDirectory(); void agents.ensureLoaded();
  const [runs, { refetch }] = createResource(() => props.identifier, getFlowRuns);
  const [selected, setSelected] = createSignal("");
  const [tab, setTab] = createSignal(0);
  const [eventsView, setEventsView] = createSignal(false);
  const [detail] = createResource(() => selected() ? { id: props.identifier, run: selected() } : undefined, value => getFlowRun(value.id, value.run));
  const transcript = createMemo(() => !detail.loading && !detail.error ? detail()?.transcripts[tab()] : undefined);
  const projection = createMemo(() => transcript() ? projectConversation(transcript()!.events, transcript()!.session.working_directory) : undefined);
  return <section class="flow-history"><header class="flow-recipe-heading"><h2>Run history</h2><Button variant="bare" onClick={() => void refetch()}>Refresh</Button></header>
    <Show when={runs.error || detail.error}><p role="alert">Could not load flow history. Try refreshing.</p></Show>
    <Show when={runs.loading}><p>Loading runs…</p></Show>
    <div class="flow-history-runs"><For each={runs.error ? [] : runs()} fallback={<p class="flow-recipe-intro">Recorded runs will appear here. Starting a new run keeps previous transcripts in this history.</p>}>{run => <div class="flow-history-run"><button type="button" aria-expanded={selected() === run.run_id} onClick={() => { setSelected(selected() === run.run_id ? "" : run.run_id); setTab(0); }}><strong>{run.title}</strong><small>{new Date(run.created_at).toLocaleString()} · {run.status}</small></button><A href={`/chat/${run.session_id}`}>Open chat</A></div>}</For></div>
    <Show when={selected()}><section class="flow-history-detail" aria-label="Recorded flow run">
      <Show when={detail.loading}><p>Loading transcripts…</p></Show>
      <div class="flow-history-tabs"><For each={detail.loading || detail.error ? [] : detail()?.transcripts}>{(item, index) => <button type="button" aria-pressed={tab() === index()} onClick={() => setTab(index())}>{index() === 0 ? "Coordinator" : (agents.agents().find(agent => agent.id === item.session.agent_id)?.name || item.session.agent_id)}</button>}</For><Button variant="bare" onClick={() => setEventsView(!eventsView())}>{eventsView() ? "Transcript" : "Events"}</Button></div>
      <Show when={transcript()} keyed>{item => <Show when={!eventsView()} fallback={<ol class="flow-history-events"><For each={item.events}>{event => <li><time>{new Date(event.created_at).toLocaleTimeString()}</time> <code>{event.type}</code><details><summary>Details</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details></li>}</For></ol>}><ConversationViewport sessionId={`flow-history:${selected()}:${item.session.id}`} nodes={projection()?.nodes ?? []} streamState="live" agentId={item.session.agent_id} /></Show>}</Show>
    </section></Show>
  </section>;
}
