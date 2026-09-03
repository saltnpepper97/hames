import { A } from "@solidjs/router";
import { For, Show, createEffect } from "solid-js";
import type { PluginView } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { Icon } from "../shell/icons";
import { usePluginDirectory } from "./PluginDirectory";

function PluginSidebarItem(props: { plugin: PluginView }) {
  const state = () => props.plugin.warning
    ? "Needs attention"
    : props.plugin.running
    ? "Running"
    : props.plugin.enabled
    ? "Starting"
    : "Disabled";

  return (
    <A
      href={`/plugins/${encodeURIComponent(props.plugin.id)}`}
      class="plugin-sidebar-item"
      activeClass="active"
      end
    >
      <span class="plugin-sidebar-icon"><Icon name="nav.plugins" size={16} /></span>
      <span class="plugin-sidebar-copy">
        <strong>{props.plugin.name}</strong>
        <span>{state()} · v{props.plugin.version}</span>
      </span>
      <span class="plugin-sidebar-state" data-state={state().toLowerCase().replace(" ", "-")} aria-hidden="true" />
    </A>
  );
}

export function PluginSidebar() {
  const directory = usePluginDirectory();
  const enabled = () => directory.plugins().filter((plugin) => plugin.enabled);
  const disabled = () => directory.plugins().filter((plugin) => !plugin.enabled);

  createEffect(() => void directory.ensureLoaded());

  return (
    <div class="plugin-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <div class="plugin-sidebar-loading" aria-label="Loading plugins"><span /><span /><span /></div>
      </Show>
      <Show when={directory.error()}>
        <div class="plugin-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="plugin-sidebar-list" aria-label="Installed plugins">
          <CollapsibleSidebarGroup
            label="Enabled"
            meta={<span>{enabled().length}</span>}
            class="plugin-sidebar-group"
          >
            <For each={enabled()} fallback={<p class="plugin-group-empty">No enabled plugins</p>}>
              {(plugin) => <PluginSidebarItem plugin={plugin} />}
            </For>
          </CollapsibleSidebarGroup>
          <CollapsibleSidebarGroup
            label="Disabled"
            meta={<span>{disabled().length}</span>}
            class="plugin-sidebar-group"
          >
            <For each={disabled()} fallback={<p class="plugin-group-empty">No disabled plugins</p>}>
              {(plugin) => <PluginSidebarItem plugin={plugin} />}
            </For>
          </CollapsibleSidebarGroup>
        </nav>
      </Show>
    </div>
  );
}
