import { retireAgent } from "../api/client";
import { DeletableSidebarRow } from "../components/DeletableSidebarRow";
import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import type { AgentDetail } from "../api/types";
import { AgentCreateDialog } from "../chat/components/AgentCreateDialog";
import { Button } from "../components/Button";
import { LoadingState } from "../components/LoadingState";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { useWorkspace } from "../shell/workspace";
import { AgentAvatar } from "./AgentAvatar";
import { useAgentDirectory } from "./AgentDirectory";
import { fallbackAvatar } from "./color";

function authorityLabel(authority: string): string {
  return authority === "read_only" ? "Read only" : "Standard authority";
}

export function AgentSidebar() {
  const directory = useAgentDirectory();
  const search = useSidebarSearch();
  const navigate = useNavigate();
  const location = useLocation();
  const agents = createMemo(() => {
    const query = search().trim().toLocaleLowerCase();
    if (!query) return directory.agents();
    return directory.agents().filter((agent) =>
      `${agent.name}\n${agent.id}\n${agent.authority}`.toLocaleLowerCase().includes(query)
    );
  });

  onMount(() => void directory.ensureLoaded());

  return (
    <div class="agent-sidebar-content">
      <Show when={directory.loading() && directory.agents().length === 0}>
        <LoadingState variant="sidebar" label="Loading agents" />
      </Show>

      <Show when={directory.error()}>
        <div class="agent-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>

      <Show when={!directory.loading() && !directory.error()}>
        <nav class="agent-sidebar-list" aria-label="Available agents">
          <For
            each={agents()}
            fallback={<p class="context-empty">{search().trim() ? "No matching agents." : "No agents are installed."}</p>}
          >
            {(agent) => (
              <DeletableSidebarRow name={agent.name} kind="agent" eligible={agent.id !== "default"}
                description={<p>This retires the agent capsule so it can no longer be selected for new work. Existing session history remains attributed to this agent.</p>}
                onDelete={async () => {
                  await retireAgent(agent.id);
                  directory.remove(agent.id);
                  if (location.pathname === `/agents/${encodeURIComponent(agent.id)}`) navigate("/agents", { replace: true });
                }}>
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
                    <span>{authorityLabel(agent.authority)}</span>
                  </span>
                </A>
              </DeletableSidebarRow>
            )}
          </For>
        </nav>
      </Show>
    </div>
  );
}

export function AgentSidebarAction() {
  const directory = useAgentDirectory();
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [creating, setCreating] = createSignal(false);

  const openCreatedAgent = (agent: AgentDetail) => {
    directory.update(agent);
    setCreating(false);
    navigate(`/agents/${encodeURIComponent(agent.id)}`);
  };

  return (
    <>
      <Button
        variant="bare"
        size="small"
        class="sidebar-context-action sidebar-create-action sidebar-icon-action"
        aria-label="Create Agent"
        title="Create Agent"
        disabled={workspace.connection() !== "connected"}
        onClick={() => setCreating(true)}
      >
        <Icon name="action.add" size={15} />
      </Button>
      <Show when={creating()}>
        <AgentCreateDialog
          onClose={() => setCreating(false)}
          onCreated={openCreatedAgent}
          submitLabel="Create agent"
        />
      </Show>
    </>
  );
}
