import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { AgentAvatar } from "../../agents/AgentAvatar";
import { fallbackAvatar } from "../../agents/color";
import { updateSessionAgent } from "../../api/client";
import type { AgentDetail, Session } from "../../api/types";
import { Button } from "../../components/Button";
import { DropdownSurface } from "../../components/DropdownSurface";
import { Icon } from "../../shell/icons";
import { AgentCreateDialog } from "./AgentCreateDialog";

interface AgentPickerProps {
  session: Session;
  disabled?: boolean;
  onSessionUpdated: (session: Session) => void;
}

export function AgentPicker(props: AgentPickerProps) {
  const directory = useAgentDirectory();
  const [open, setOpen] = createSignal(false);
  const [creating, setCreating] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  let root!: HTMLDivElement;
  const selected = createMemo(() =>
    directory.agents().find((agent) => agent.id === props.session.agent_id),
  );
  const selectedName = () => selected()?.name ?? props.session.agent_id;

  onMount(() => void directory.ensureLoaded());

  const choose = async (agentId: string) => {
    if (saving() || props.disabled || agentId === props.session.agent_id) {
      setOpen(false);
      return;
    }
    setSaving(true);
    setError("");
    try {
      props.onSessionUpdated(await updateSessionAgent(props.session.id, agentId));
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to change this chat's agent.");
    } finally {
      setSaving(false);
    }
  };

  const useCreated = async (agent: AgentDetail) => {
    directory.update(agent);
    props.onSessionUpdated(await updateSessionAgent(props.session.id, agent.id));
    setCreating(false);
    setOpen(false);
  };

  createEffect(() => {
    if (!open()) return;
    const closeOutside = (event: MouseEvent) => {
      if (!root.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    onCleanup(() => document.removeEventListener("mousedown", closeOutside));
  });

  return (
    <>
      <div
        class="agent-picker"
        ref={root}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || !open()) return;
          event.preventDefault();
          setOpen(false);
        }}
        onFocusOut={(event) => {
          if (!root.contains(event.relatedTarget as Node | null)) setOpen(false);
        }}
      >
        <Button
          variant="bare"
          class="agent-picker-trigger"
          aria-label={`Agent: ${selectedName()}`}
          aria-haspopup="menu"
          aria-expanded={open()}
          disabled={saving() || props.disabled}
          title={props.disabled ? "Finish the active run before changing agents" : "Choose agent"}
          onClick={() => {
            setOpen((value) => !value);
            setError("");
            if (!directory.loaded()) void directory.ensureLoaded();
          }}
        >
          <AgentAvatar
            config={selected()?.avatar ?? fallbackAvatar(props.session.agent_id)}
            name={selectedName()}
            size={25}
          />
          <span>{selectedName()}</span>
          <Icon name="action.expand" size={13} />
        </Button>
        <DropdownSurface
          open={open()}
          class="agent-picker-popover"
          role="menu"
          ariaLabel="Choose agent"
        >
          <div class="agent-picker-heading">Agent for this chat</div>
          <Show when={!directory.loading()} fallback={<div class="agent-picker-state">Loading agents…</div>}>
            <For each={directory.agents()} fallback={<div class="agent-picker-state">No agents found.</div>}>
              {(agent) => (
                <Button
                  variant="bare"
                  class="agent-picker-option"
                  role="menuitemradio"
                  aria-checked={agent.id === props.session.agent_id}
                  disabled={saving()}
                  onClick={() => void choose(agent.id)}
                >
                  <AgentAvatar
                    config={agent.avatar ?? fallbackAvatar(agent.id)}
                    name={agent.name}
                    size={29}
                  />
                  <span>
                    <strong>{agent.name}</strong>
                    <small>{agent.id}</small>
                  </span>
                  <Show when={agent.id === props.session.agent_id}>
                    <Icon name="action.selected" size={15} />
                  </Show>
                </Button>
              )}
            </For>
          </Show>
          <Show when={directory.error()}>
            <div class="agent-picker-error" role="alert">{directory.error()}</div>
          </Show>
          <Show when={error()}>
            <div class="agent-picker-error" role="alert">{error()}</div>
          </Show>
          <div class="agent-picker-divider" />
          <Button
            variant="bare"
            class="agent-picker-create"
            role="menuitem"
            disabled={props.disabled}
            onClick={() => {
              setOpen(false);
              setCreating(true);
            }}
          >
            <span class="agent-picker-create-icon"><Icon name="action.add" size={15} /></span>
            <span>Create agent</span>
          </Button>
        </DropdownSurface>
      </div>
      <Show when={creating()}>
        <AgentCreateDialog
          onClose={() => setCreating(false)}
          onCreated={useCreated}
        />
      </Show>
    </>
  );
}
