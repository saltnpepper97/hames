import { A, useNavigate, useParams } from "@solidjs/router";
import { For, Show, createSignal } from "solid-js";
import { useAgentDirectory } from "../agents/AgentDirectory";
import { saveFlow, deleteFlow } from "../api/client";
import { Button } from "../components/Button";
import { DeleteConfirmationDialog } from "../components/DeleteConfirmationDialog";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { useFlows } from "./FlowDirectory";
import type { FlowItem, FlowRecipe } from "./types";

export function FlowSidebar() {
  const flows = useFlows(); const search = useSidebarSearch();
  return <nav class="flow-sidebar" aria-label="Flows"><For each={flows.items().filter(item => `${item.recipe?.name || item.id}`.toLowerCase().includes(search().toLowerCase()))}>{item => <A class="automation-sidebar-item" activeClass="active" href={`/flows/edit/${item.id}`}><Icon name="nav.flows" size={18} /><span><strong>{item.recipe?.name || item.id}</strong><small>{item.id}</small></span></A>}</For></nav>;
}
export function FlowSidebarAction() { const navigate = useNavigate(); return <Button variant="bare" class="sidebar-context-action sidebar-create-action sidebar-icon-action" aria-label="Create flow" onClick={() => navigate("/flows/new")}><Icon name="action.add" size={15} /></Button>; }
function RecipeEditor(props: { item?: FlowItem }) {
  const flows = useFlows(); const agents = useAgentDirectory(); const navigate = useNavigate();
  void agents.ensureLoaded();
  const [value, setValue] = createSignal<FlowRecipe>(props.item?.recipe || { name: "", coordinator: "", instructions: "" });
  const [identifier, setIdentifier] = createSignal(props.item?.id || "");
  const [error, setError] = createSignal(""); const [saved, setSaved] = createSignal(false); const [busy, setBusy] = createSignal(false); const [deleting, setDeleting] = createSignal(false);
  const patch = (change: Partial<FlowRecipe>) => { setValue({ ...value(), ...change }); setSaved(false); };
  const persist = async () => { setBusy(true); setError(""); try { if (!props.item && flows.items().some(item => item.id === identifier())) throw Error("That identifier is already in use."); await saveFlow(identifier(), value()); await flows.refresh(); setSaved(true); if (!props.item) navigate(`/flows/edit/${identifier()}`); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  return <section class="page flow-recipe-page"><header class="flow-recipe-heading"><div><span class="eyebrow">Flow</span><h1>{props.item ? value().name : "New flow"}</h1></div><Button variant="primary" disabled={!value().name.trim() || !value().coordinator || !/^[a-z][a-z0-9-]{0,62}$/.test(identifier())} loading={busy()} onClick={() => void persist()}>Save</Button></header>
    <p class="flow-recipe-intro">A coordinator, a team, and instructions for how they work together. Everything happens in your normal chat.</p>
    <Show when={error()}><p role="alert">{error()}</p></Show><Show when={saved()}><p role="status">Saved</p></Show>
    <label class="flow-recipe-field">Name<input value={value().name} onInput={e => patch({ name: e.currentTarget.value })} /></label>
    <label class="flow-recipe-field">Identifier<input readOnly={Boolean(props.item)} value={identifier()} onInput={e => setIdentifier(e.currentTarget.value)} placeholder="build-review" /><small>Use <code>/flow {identifier() || "identifier"} your task</code> in the chat composer.</small></label>
    <label class="flow-recipe-field">Coordinator<select value={value().coordinator} onChange={e => patch({ coordinator: e.currentTarget.value })}><option value="">Choose an agent</option><For each={agents.agents()}>{agent => <option value={agent.id}>{agent.name}</option>}</For></select><small>This agent leads the main conversation. Its configured workers and tools are managed in <A href="/agents">Agents</A>.</small></label>
    <label class="flow-recipe-field">How should the team work?<textarea rows={10} value={value().instructions} onInput={e => patch({ instructions: e.currentTarget.value })} placeholder="Delegate implementation to builder, review the actual work, and involve finisher when needed. Return the final report here." /><small>Plain-language instructions. The coordinator decides the next step and asks you when it needs a real decision.</small></label>
    <Show when={props.item}><Button variant="destructive" onClick={() => setDeleting(true)}>Delete flow</Button></Show>
    <Show when={deleting()}><DeleteConfirmationDialog eyebrow="Delete flow" title={`Delete ${value().name}?`} confirmLabel="Delete flow" onClose={() => setDeleting(false)} onConfirm={async () => { await deleteFlow(identifier()); await flows.refresh(); navigate("/flows"); }}><p>Your conversations and worker transcripts will be kept.</p></DeleteConfirmationDialog></Show>
  </section>;
}
export function FlowsPage() {
  const params = useParams<{ view?: string; id?: string }>(); const flows = useFlows();
  return <Show when={params.view === "new"} fallback={<Show when={params.id} keyed fallback={<section class="page flow-recipe-page"><h1>Flows</h1><p>Save a way for your agents to work together, then start it in any chat.</p><A href="/flows/new">Create a flow</A><For each={flows.items()}>{item => <A class="automation-sidebar-item" href={`/flows/edit/${item.id}`}><strong>{item.recipe?.name || item.id}</strong><code>/flow {item.id}</code></A>}</For><Show when={flows.error()}><p role="alert">{flows.error()}</p></Show></section>}>{id => <Show when={flows.items().find(item => item.id === id)} fallback={<p>{flows.loaded() ? "Flow not found" : "Loading…"}</p>}>{item => <Show when={item().recipe} fallback={<p role="alert">{item().error}</p>}><RecipeEditor item={item()} /></Show>}</Show>}</Show>}><RecipeEditor /></Show>;
}
