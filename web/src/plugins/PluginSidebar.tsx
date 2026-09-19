import { removePlugin } from "../api/client";
import { DeletableSidebarRow } from "../components/DeletableSidebarRow";
import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal } from "solid-js";
import type { PluginView } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { LoadingState } from "../components/LoadingState";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { usePluginDirectory } from "./PluginDirectory";
import { PluginInstallDialog } from "./PluginInstallDialog";

function PluginSidebarItem(props: { plugin: PluginView }) {
  const directory = usePluginDirectory();
  const location = useLocation();
  const navigate = useNavigate();
  const state = () => props.plugin.warning
    ? "Needs attention"
    : props.plugin.running
    ? "Running"
    : props.plugin.enabled
    ? "Starting"
    : "Disabled";

  return (
    <DeletableSidebarRow name={props.plugin.name} kind="plugin" verb="Remove"
      description={<><p>This removes the installed package and its registry record from Hames.</p><p>The managed copy at <code>{props.plugin.package_path}</code> will be removed. Files outside Hames's plugin directory are not modified.</p></>}
      onDelete={async () => {
        const id = props.plugin.id;
        await removePlugin(id);
        directory.remove(id);
        if (location.pathname === `/plugins/${encodeURIComponent(id)}`) navigate("/plugins", { replace: true });
      }}>
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
    </DeletableSidebarRow>
  );
}

export function PluginSidebar() {
  const directory = usePluginDirectory();
  const search = useSidebarSearch();
  const matching = () => {
    const query = search().trim().toLocaleLowerCase();
    if (!query) return directory.plugins();
    return directory.plugins().filter((plugin) =>
      `${plugin.name}\n${plugin.id}\n${plugin.version}\n${plugin.capabilities.join(" ")}\n${plugin.warning}`
        .toLocaleLowerCase()
        .includes(query)
    );
  };
  const enabled = () => matching().filter((plugin) => plugin.enabled);
  const disabled = () => matching().filter((plugin) => !plugin.enabled);

  createEffect(() => void directory.ensureLoaded());

  return (
    <div class="plugin-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <LoadingState variant="sidebar" label="Loading plugins" />
      </Show>
      <Show when={directory.error()}>
        <div class="plugin-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="plugin-sidebar-list" aria-label="Installed plugins">
          <Show when={!search().trim() || matching().length > 0} fallback={
            <p class="context-empty">No matching plugins.</p>
          }>
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
          </Show>
        </nav>
      </Show>
    </div>
  );
}

export function PluginSidebarAction() {
  const directory = usePluginDirectory();
  const navigate = useNavigate();
  const [adding, setAdding] = createSignal(false);

  const installed = (plugin: PluginView) => {
    directory.upsert(plugin);
    setAdding(false);
    navigate(`/plugins/${encodeURIComponent(plugin.id)}`);
  };

  return (
    <>
      <Button
        variant="bare"
        size="small"
        class="sidebar-context-action sidebar-create-action sidebar-icon-action"
        aria-label="Add Plugin"
        title="Add Plugin"
        onClick={() => setAdding(true)}
      >
        <Icon name="action.add" size={15} />
      </Button>
      <Show when={adding()}>
        <PluginInstallDialog onClose={() => setAdding(false)} onInstalled={installed} />
      </Show>
    </>
  );
}
