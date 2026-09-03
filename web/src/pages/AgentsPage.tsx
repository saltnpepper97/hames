import { useNavigate } from "@solidjs/router";
import { For, Show, createSignal, onMount } from "solid-js";
import { listAgents } from "../api/client";
import type { AgentPublic } from "../api/types";
import { AgentAvatar } from "../agents/AgentAvatar";
import { fallbackAvatar } from "../agents/color";
import { Icon } from "../shell/icons";
import { Button } from "../components/Button";

export function AgentsPage() {
  const [agents, setAgents] = createSignal<AgentPublic[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal("");
  const navigate = useNavigate();

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
          <Button onClick={() => void load()}>Try again</Button>
        </div>
      </Show>

      <Show when={!loading() && !error()}>
        <div class="agent-grid">
          <For each={agents()} fallback={<p class="agent-empty">No agents are installed.</p>}>
            {(agent) => {
              const avatar = () => agent.avatar ?? fallbackAvatar(agent.id);
              return (
                <article class="agent-card">
                  <Button
                    variant="icon"
                    class="agent-edit"
                    aria-label={`Edit ${agent.name}`}
                    title={`Edit ${agent.name}`}
                    onClick={() => navigate(`/agents/${encodeURIComponent(agent.id)}`)}
                  >
                    <Icon name="action.edit" size={16} />
                  </Button>
                  <Button variant="bare" class="agent-avatar-button" aria-label={`Open ${agent.name}`} onClick={() => navigate(`/agents/${encodeURIComponent(agent.id)}`)}>
                    <AgentAvatar config={avatar()} name={agent.name} size={86} />
                  </Button>
                  <div class="agent-card-copy">
                    <h2>{agent.name}</h2>
                    <span class="agent-id">{agent.id}</span>
                    <span class="agent-authority">{agent.authority === "read_only" ? "Read only" : "Standard authority"}</span>
                  </div>
                </article>
              );
            }}
          </For>
        </div>
      </Show>

    </section>
  );
}
