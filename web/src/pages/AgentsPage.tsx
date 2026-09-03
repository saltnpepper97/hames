import { For, Show, createSignal, onMount } from "solid-js";
import { listAgents, updateAgentAvatar } from "../api/client";
import type { AgentAvatarConfig, AgentPublic } from "../api/types";
import { AgentAvatar } from "../agents/AgentAvatar";
import { AgentAvatarEditor } from "../agents/AgentAvatarEditor";
import { fallbackAvatar } from "../agents/color";

export function AgentsPage() {
  const [agents, setAgents] = createSignal<AgentPublic[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal("");
  const [editing, setEditing] = createSignal<AgentPublic>();
  const [saving, setSaving] = createSignal(false);
  const [saveError, setSaveError] = createSignal("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setAgents(await listAgents());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load agents");
    } finally {
      setLoading(false);
    }
  };

  const saveAvatar = async (avatar: AgentAvatarConfig) => {
    const agent = editing();
    if (!agent || saving()) return;
    setSaving(true);
    setSaveError("");
    try {
      const updated = await updateAgentAvatar(agent.id, avatar);
      setAgents((current) => current.map((item) => item.id === updated.id ? updated : item));
      setEditing(undefined);
    } catch (caught) {
      setSaveError(caught instanceof Error ? caught.message : "Unable to save avatar");
    } finally {
      setSaving(false);
    }
  };

  onMount(() => void load());

  return (
    <section class="page agents-page" aria-labelledby="agents-title">
      <div class="page-heading agents-heading">
        <div>
          <span class="eyebrow">Identity</span>
          <h1 id="agents-title">Agents</h1>
          <p>Portable roles with their own instructions, access, and a little visual personality.</p>
        </div>
        <Show when={!loading() && !error()}>
          <span class="agent-count">{agents().length} {agents().length === 1 ? "agent" : "agents"}</span>
        </Show>
      </div>

      <Show when={loading()}>
        <div class="agent-grid loading" aria-label="Loading agents">
          <span /><span />
        </div>
      </Show>

      <Show when={error()}>
        <div class="error-state">
          <div><span class="eyebrow">Gateway error</span><h2>Agents could not be loaded.</h2><p>{error()}</p></div>
          <button class="button" type="button" onClick={() => void load()}>Try again</button>
        </div>
      </Show>

      <Show when={!loading() && !error()}>
        <div class="agent-grid">
          <For each={agents()} fallback={<p class="agent-empty">No agents are installed.</p>}>
            {(agent) => {
              const avatar = () => agent.avatar ?? fallbackAvatar(agent.id);
              return (
                <article class="agent-card">
                  <button class="agent-avatar-button" type="button" aria-label={`Customize ${agent.name} avatar`} onClick={() => setEditing(agent)}>
                    <AgentAvatar config={avatar()} name={agent.name} size={86} />
                  </button>
                  <div class="agent-card-copy">
                    <h2>{agent.name}</h2>
                    <span class="agent-id">{agent.id}</span>
                    <span class="agent-authority">{agent.authority === "read_only" ? "Read only" : "Standard authority"}</span>
                  </div>
                  <button class="agent-customize" type="button" onClick={() => setEditing(agent)}>Customize avatar</button>
                </article>
              );
            }}
          </For>
        </div>
      </Show>

      <Show when={editing()} keyed>{(agent) => (
        <AgentAvatarEditor
          agentName={agent.name}
          initial={agent.avatar ?? fallbackAvatar(agent.id)}
          saving={saving()}
          error={saveError()}
          onSave={(avatar) => void saveAvatar(avatar)}
          onClose={() => { if (!saving()) { setEditing(undefined); setSaveError(""); } }}
        />
      )}</Show>
    </section>
  );
}
