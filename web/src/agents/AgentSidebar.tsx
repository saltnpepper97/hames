import { A } from "@solidjs/router";
import { For, Show, onMount } from "solid-js";
import { Button } from "../components/Button";
import { AgentAvatar } from "./AgentAvatar";
import { useAgentDirectory } from "./AgentDirectory";
import { fallbackAvatar } from "./color";

function authorityLabel(authority: string): string {
  return authority === "read_only" ? "Read only" : "Standard authority";
}

export function AgentSidebar() {
  const directory = useAgentDirectory();

  onMount(() => void directory.ensureLoaded());

  return (
    <div class="agent-sidebar-content">
      <Show when={directory.loading() && directory.agents().length === 0}>
        <div class="agent-sidebar-loading" aria-label="Loading agents"><span /><span /></div>
      </Show>

      <Show when={directory.error()}>
        <div class="agent-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>

      <Show when={!directory.loading() && !directory.error()}>
        <nav class="agent-sidebar-list" aria-label="Available agents">
          <For each={directory.agents()} fallback={<p class="context-empty">No agents are installed.</p>}>
            {(agent) => (
              <A
                href={`/agents/${encodeURIComponent(agent.id)}`}
                class="agent-sidebar-item"
                activeClass="active"
                end
              >
                <AgentAvatar
                  config={agent.avatar ?? fallbackAvatar(agent.id)}
                  name={agent.name}
                  size={42}
                />
                <span class="agent-sidebar-copy">
                  <strong>{agent.name}</strong>
                  <span>{agent.id} · {authorityLabel(agent.authority)}</span>
                </span>
              </A>
            )}
          </For>
        </nav>
      </Show>
    </div>
  );
}
