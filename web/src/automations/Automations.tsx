import { A, useNavigate, useParams } from "@solidjs/router";
import { createContext, createEffect, createMemo, createSignal, For, onCleanup, Show, useContext } from "solid-js";
import type { ParentProps } from "solid-js";
import { listAutomations, saveAutomation, runAutomation, deleteAutomation } from "../api/client";
import type { Automation, AutomationCatalog, AutomationSpec } from "../api/client";
import { useWorkspace } from "../shell/workspace";
import { useAgentDirectory } from "../agents/AgentDirectory";
import { AgentDefaultModel } from "../agents/AgentDefaultModel";
import { Button } from "../components/Button";
import { DialogFrame } from "../components/DialogFrame";
import { TextField } from "../components/FormField";
import { Select } from "../components/Select";
import { Switch } from "../components/Switch";
import { Icon } from "../shell/icons";
import { useSidebarSearch } from "../components/SidebarSearchContext";

function createDirectory() {
  const [data, setData] = createSignal<AutomationCatalog>({ items: [], runs: [], native_notifications: false });
  const [error, setError] = createSignal("");
  let pending = false;
  let disposed = false;
  onCleanup(() => { disposed = true; });
  const refresh = async () => {
    if (pending || disposed) return;
    pending = true;
    try { const next = await listAutomations(); if (!disposed) { setData(next); setError(""); } }
    catch (e) { if (!disposed) setError(e instanceof Error ? e.message : "Automations could not be loaded"); }
    finally { pending = false; }
  };
  return { data, error, refresh };
}
const Directory = createContext<ReturnType<typeof createDirectory>>();
function useAutomations() {
  const value = useContext(Directory);
  if (!value) throw Error("Missing automation directory");
  return value;
}
export function AutomationProvider(props: ParentProps) {
  const workspace = useWorkspace();
  const directory = createDirectory();
  const seen = new Set<string>();
  let initialized = false;
  createEffect(() => {
    if (workspace.connection() !== "connected") return;
    void directory.refresh();
    const timer = setInterval(() => void directory.refresh(), 15000);
    onCleanup(() => clearInterval(timer));
  });
  createEffect(() => {
    const catalog = directory.data();
    const finished = catalog.runs.filter(run => run.finished_at);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
    for (const run of finished) {
      if (!seen.has(run.id) && initialized && !(local && catalog.native_notifications)
        && "Notification" in window && Notification.permission === "granted") {
        const item = catalog.items.find(item => item.id === run.automation_id);
        if (item && item.notify !== "off" && run.status !== "skipped"
          && (item.notify === "results" || run.status !== "completed")) {
          const key = `hames.automation.notice.${run.id}`;
          if (!localStorage.getItem(key)) {
            localStorage.setItem(key, "shown");
            const notice = new Notification(item.title, { body: run.status === "completed" ? "Automation completed. Open Hames to view results." : "Automation needs attention.", tag: run.id });
            notice.onclick = () => { window.focus(); if (run.session_id) location.assign(`/chat/${run.session_id}`); notice.close(); };
          }
        }
      }
      seen.add(run.id);
    }
    if (catalog.items.length || catalog.runs.length) initialized = true;
  });
  return <Directory.Provider value={directory}>{props.children}</Directory.Provider>;
}
const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function schedule(spec: AutomationSpec) {
  const repeat = spec.frequency === "daily" ? "Every day" : spec.frequency === "once" ? spec.date : spec.weekdays.map(day => days[day]).join(", ");
  return `${repeat} at ${spec.time} · ${spec.timezone}`;
}
function dateTime(value: string | null) { return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—"; }
function definition(item: Automation): AutomationSpec { const { id: _id, next_run: _next, updated_at: _updated, ...spec } = item; return spec; }

export function AutomationSidebar() {
  const directory = useAutomations();
  const search = useSidebarSearch();
  const items = createMemo(() => directory.data().items.filter(item => `${item.title} ${item.instructions}`.toLowerCase().includes(search().toLowerCase())));
  return <nav class="automation-sidebar" aria-label="Scheduled tasks">
    <A href="/automations" end class="automation-sidebar-item" activeClass="active"><Icon name="nav.automations" /><span>Overview</span></A>
    <For each={items()} fallback={<p class="context-empty">No automations yet.</p>}>{item =>
      <A href={`/automations/${item.id}`} class="automation-sidebar-item" activeClass="active">
        <Icon name="nav.automations" size={20} />
        <span><strong>{item.title}</strong><small>{item.enabled ? `Next: ${dateTime(item.next_run)}` : "Paused"}</small></span>
      </A>
    }</For>
  </nav>;
}
export function AutomationSidebarAction() {
  const navigate = useNavigate();
  return <Button variant="bare" class="sidebar-context-action sidebar-create-action sidebar-icon-action" aria-label="Create automation" title="Create automation" onClick={() => navigate("/automations/new")}><Icon name="action.add" size={15} /></Button>;
}

function AutomationEditor(props: { initial?: Automation; onClose: () => void; onSaved: (item: Automation) => void }) {
  const workspace = useWorkspace();
  const agents = useAgentDirectory();
  const [step, setStep] = createSignal(0);
  const [error, setError] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [draft, setDraft] = createSignal<AutomationSpec>(props.initial ? definition(props.initial) : {
    title: "", instructions: "", working_directory: "", agent_id: "default",
    provider: "", model: "", reasoning_effort: "", frequency: "daily", time: "10:00",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", weekdays: [0,1,2,3,4], date: "", enabled: false,
    catch_up: true, retries: 0, notify: "results",
  });
  createEffect(() => { void agents.ensureLoaded(); });
  const update = <K extends keyof AutomationSpec>(key: K, value: AutomationSpec[K]) => setDraft(current => ({ ...current, [key]: value }));
  const next = () => {
    setError("");
    if (step() === 0 && (!draft().title.trim() || !draft().instructions.trim())) { setError("Enter a name and task."); return; }
    if (step() === 1) {
      try { new Intl.DateTimeFormat(undefined, { timeZone: draft().timezone }); }
      catch { setError("Enter a valid timezone, such as America/Halifax."); return; }
      if (!draft().time || draft().frequency === "once" && !draft().date || draft().frequency === "weekly" && !draft().weekdays.length) { setError("Complete the schedule before continuing."); return; }
    }
    setStep(step() + 1);
  };
  const save = async (enabled: boolean) => {
    setSaving(true); setError("");
    try { props.onSaved(await saveAutomation({ ...draft(), enabled }, props.initial?.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to save automation"); }
    finally { setSaving(false); }
  };
  return <DialogFrame class="automation-dialog" eyebrow={`Automation · ${step()+1} of 3`} title={props.initial ? "Edit automation" : "Create automation"} onClose={() => { if (!saving()) props.onClose(); }} footer={<>
    <span role="alert" class="automation-error">{error()}</span>
    <Button disabled={saving()} onClick={props.onClose}>Cancel</Button>
    <Show when={step() > 0}><Button disabled={saving()} onClick={() => setStep(step()-1)}>Back</Button></Show>
    <Show when={step() < 2} fallback={<>
      <Button disabled={saving()} onClick={() => void save(false)}>Save paused</Button>
      <Button variant="primary" loading={saving()} onClick={() => void save(true)}>{props.initial?.enabled ? "Save changes" : "Enable automation"}</Button>
    </>}><Button variant="primary" onClick={next}>Next</Button></Show>
  </>}>
    <div class="automation-editor">
      <Show when={step() === 0}>
        <TextField label="Name" placeholder="Morning mail" value={draft().title} onInput={e => update("title", e.currentTarget.value)} />
        <label class="automation-field">What should Hames do?<textarea class="text-input" rows={5} value={draft().instructions} placeholder="Check my mail and summarize anything that needs my attention." onInput={e => update("instructions", e.currentTarget.value)} /></label>
        <label class="automation-field">Workspace (optional)<Select ariaLabel="Automation workspace" value={draft().working_directory} options={[{ value: "", label: "General — no project workspace" }, ...workspace.workspaces().map(item => ({ value: item.path, label: item.title }))]} onValueChange={value => update("working_directory", value)} /></label>
        <label class="automation-field">Agent<Select ariaLabel="Automation agent" value={draft().agent_id} options={agents.agents().map(item => ({ value: item.id, label: item.name }))} onValueChange={value => setDraft(current => ({ ...current, agent_id: value, provider: "", model: "", reasoning_effort: "" }))} /></label>
        <p class="automation-hint">Uses the agent’s default model unless you choose one below. Account access comes from the agent’s configured tools and connections.</p>
        <AgentDefaultModel inheritLabel="Use agent’s default model" value={draft().provider ? { provider: draft().provider, model: draft().model, reasoning_effort: draft().reasoning_effort } : null} onChange={value => setDraft(current => ({ ...current, provider: value?.provider ?? "", model: value?.model ?? "", reasoning_effort: value?.reasoning_effort ?? "" }))} />
      </Show>
      <Show when={step() === 1}>
        <label class="automation-field">Repeat<Select ariaLabel="Repeat" value={draft().frequency} options={[{value:"once",label:"Once"},{value:"daily",label:"Every day"},{value:"weekly",label:"Selected weekdays"}]} onValueChange={value => update("frequency", value as AutomationSpec["frequency"])} /></label>
        <Show when={draft().frequency === "weekly"}><div class="automation-weekdays" role="group" aria-label="Weekdays"><For each={days}>{(day,index) => <Button variant="choice" aria-pressed={draft().weekdays.includes(index())} onClick={() => update("weekdays", draft().weekdays.includes(index()) ? draft().weekdays.filter(d=>d!==index()) : [...draft().weekdays,index()].sort())}>{day}</Button>}</For></div></Show>
        <Show when={draft().frequency === "once"}><TextField label="Date" type="date" value={draft().date} onInput={e=>update("date", e.currentTarget.value)} /></Show>
        <fieldset><legend>What time?</legend><div class="automation-time-options"><For each={["08:00","09:00","10:00"]}>{time => <Button variant="choice" aria-pressed={draft().time === time} onClick={()=>update("time",time)}>{time}</Button>}</For></div><TextField label="Custom time" type="time" value={draft().time} onInput={e=>update("time",e.currentTarget.value)} /></fieldset>
        <TextField label="Timezone" value={draft().timezone} onInput={e=>update("timezone",e.currentTarget.value)} />
        <Switch label="Catch up after sleep" description="Run once after a missed schedule. Never build a backlog." checked={draft().catch_up} onCheckedChange={value=>update("catch_up",value)} />
        <label class="automation-field">Retry temporary failures<Select ariaLabel="Retries" value={String(draft().retries)} options={[{value:"0",label:"No automatic retries"},{value:"1",label:"Retry once"},{value:"2",label:"Retry twice"}]} onValueChange={value=>update("retries",Number(value))} /></label>
        <label class="automation-field">Notify me<Select ariaLabel="Notify me" value={draft().notify} options={[{value:"results",label:"Results and failures"},{value:"failures",label:"Failures only"},{value:"off",label:"Off"}]} onValueChange={value=>update("notify",value as AutomationSpec["notify"])} /></label>
      </Show>
      <Show when={step() === 2}><h3>{draft().title}</h3><p class="automation-task-text">{draft().instructions}</p><dl class="automation-facts"><dt>Schedule</dt><dd>{schedule(draft())}</dd><dt>Agent</dt><dd>{agents.agents().find(a=>a.id===draft().agent_id)?.name ?? draft().agent_id}</dd><dt>Model</dt><dd>{draft().model ? `${draft().provider}/${draft().model}` : "Agent default"}</dd><dt>Workspace</dt><dd>{draft().working_directory || "General"}</dd><dt>Notifications</dt><dd>{draft().notify === "results" ? "Results and failures" : draft().notify === "failures" ? "Failures only" : "Off"}</dd></dl><p class="automation-hint">Each run opens its own chat. Private result text stays out of system notifications. Reading mail does not authorize sending replies.</p></Show>
    </div>
  </DialogFrame>;
}

export function AutomationPage() {
  const directory = useAutomations();
  const params = useParams<{ automationId?: string }>();
  const navigate = useNavigate();
  const selected = createMemo(() => directory.data().items.find(item=>item.id===params.automationId));
  const [editing, setEditing] = createSignal(false);
  const [deleting, setDeleting] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [noticePermission, setNoticePermission] = createSignal("Notification" in window ? Notification.permission : "denied");
  createEffect(() => { params.automationId; setEditing(false); setDeleting(false); setError(""); });
  const act = async (action: () => Promise<unknown>) => { setBusy(true); setError(""); try { await action(); await directory.refresh(); } catch(e) { setError(e instanceof Error ? e.message : "Action failed"); } finally { setBusy(false); } };
  const local = ["localhost","127.0.0.1","[::1]"].includes(location.hostname);
  const runs = createMemo(()=>directory.data().runs.filter(run=>!selected() || run.automation_id===selected()?.id));
  return <section class="page automation-page">
    <div class="page-heading"><div><span class="eyebrow">Scheduled work</span><h1>{selected()?.title ?? "Automations"}</h1><p>{selected() ? schedule(selected()!) : "Set a task once. Hames takes care of the schedule."}</p></div>
      <Show when={selected()} fallback={<Button variant="primary" onClick={()=>navigate("/automations/new")}>Create automation</Button>}>{item=><div class="automation-actions"><Button disabled={busy() || runs().some(run=>["pending","running"].includes(run.status))} onClick={()=>void act(()=>runAutomation(item().id))}>Run now</Button><Button onClick={()=>setEditing(true)}>Edit</Button><details class="automation-more"><summary aria-label="More automation actions"><Icon name="action.more" /></summary><Button onClick={()=>setDeleting(true)}>Delete</Button></details></div>}</Show>
    </div>
    <Show when={error() || directory.error()}><p role="alert" class="automation-error">{error() || directory.error()}</p></Show>
    <Show when={!(local && directory.data().native_notifications)}><div class="automation-notification-note"><span>{noticePermission()==="granted" ? "Browser notifications are enabled while Hames is open." : noticePermission()==="denied" ? "Browser notifications are blocked. Allow notifications in your browser’s site settings." : "Enable browser notifications for results on this device."}</span><Button disabled={noticePermission()!=="default"} onClick={async()=>setNoticePermission(await Notification.requestPermission())}>Enable notifications</Button></div></Show>
    <Show when={selected()}>{item=><>
      <Switch label="Enabled" description={item().enabled ? `Next run: ${dateTime(item().next_run)}` : "Paused. Run now is still available."} checked={item().enabled} onCheckedChange={enabled=>{if(!busy())void act(()=>saveAutomation({...definition(item()),enabled},item().id));}} />
      <section class="automation-card"><h2>Task</h2><p class="automation-task-text">{item().instructions}</p><dl class="automation-facts"><dt>Agent / model</dt><dd>{item().agent_id} · {item().provider}/{item().model}</dd><dt>Workspace</dt><dd>{item().working_directory || "General"}</dd><dt>After downtime</dt><dd>{item().catch_up ? "Catch up once" : "Skip missed runs"}</dd></dl></section>
    </>}</Show>
    <Show when={!selected()}><section class="automation-card"><h2>Upcoming</h2><For each={directory.data().items.filter(item=>item.enabled).sort((a,b)=>(a.next_run??"").localeCompare(b.next_run??""))} fallback={<p>No upcoming runs. Create an automation or enable a paused one.</p>}>{item=><A class="automation-upcoming" href={`/automations/${item.id}`}><strong>{item.title}</strong><span>{dateTime(item.next_run)}</span></A>}</For></section></Show>
    <section class="automation-card"><h2>Run history</h2><For each={runs()} fallback={<p>Results will appear here, with a link to each run’s chat.</p>}>{run=><div class="automation-run"><div><strong>{selected() ? dateTime(run.created_at) : directory.data().items.find(item=>item.id===run.automation_id)?.title}</strong><span class={`automation-status is-${run.status}`}>{run.status}{run.attempt ? ` · retry ${run.attempt}` : ""}</span><Show when={run.message && run.message.toLowerCase() !== run.status}><p>{run.message}</p></Show></div><Show when={run.session_id}><A href={`/chat/${run.session_id}`}>Open chat</A></Show></div>}</For></section>
    <Show when={params.automationId === "new" || editing()}><AutomationEditor initial={editing()?selected():undefined} onClose={()=>{setEditing(false);if(params.automationId==="new")navigate("/automations");}} onSaved={item=>{setEditing(false);void directory.refresh();navigate(`/automations/${item.id}`);}} /></Show>
    <Show when={deleting() && selected()}><DialogFrame eyebrow="Remove schedule" title="Delete automation?" onClose={()=>setDeleting(false)} footer={<><Button onClick={()=>setDeleting(false)}>Cancel</Button><Button variant="destructive" loading={busy()} onClick={()=>void act(async()=>{await deleteAutomation(selected()!.id);setDeleting(false);navigate("/automations");})}>Delete</Button></>}><p class="automation-editor">Delete this schedule and its run list? Its conversation transcripts remain available.</p></DialogFrame></Show>
  </section>;
}
