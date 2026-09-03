import { For, Show, createSignal } from "solid-js";
import type { PluginCapability, PluginView } from "../api/types";
import { disablePlugin, enablePlugin } from "../api/client";
import { Button } from "../components/Button";
import { DetailHeading } from "../components/DetailHeading";
import { DetailStatStrip } from "../components/DetailStatStrip";
import { Separator } from "../components/Separator";
import { SettingsSection } from "../components/SettingsSection";
import { Switch } from "../components/Switch";
import { Icon } from "../shell/icons";
import { usePluginDirectory } from "../plugins/PluginDirectory";
import { PluginPermissionList } from "../plugins/PluginPermissionList";
import { PluginRemoveDialog } from "../plugins/PluginRemoveDialog";

interface PluginPageProps {
  plugin: PluginView;
  onAdd: () => void;
  onRemoved: (pluginId: string) => void;
}

const capabilityCopy: Readonly<Record<PluginCapability, { title: string; description: string }>> = {
  tool: { title: "Tools", description: "Makes operations available to agents through the Hames tool registry." },
  context: { title: "Context", description: "Can contribute relevant context to a model request." },
  event: { title: "Events", description: "Can observe selected events emitted by the runtime." },
};

function runtimeState(plugin: PluginView): string {
  if (plugin.warning) return "Needs attention";
  if (plugin.running) return "Running";
  if (plugin.enabled) return "Starting";
  return "Disabled";
}

export function PluginPage(props: PluginPageProps) {
  const directory = usePluginDirectory();
  const [changingState, setChangingState] = createSignal(false);
  const [actionError, setActionError] = createSignal("");
  const [confirmingRemove, setConfirmingRemove] = createSignal(false);

  const setEnabled = async (enabled: boolean) => {
    if (changingState()) return;
    setChangingState(true);
    setActionError("");
    try {
      const updated = enabled
        ? await enablePlugin(props.plugin.id)
        : await disablePlugin(props.plugin.id);
      directory.upsert(updated);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to change plugin state");
    } finally {
      setChangingState(false);
    }
  };

  return (
    <section class="page plugin-page" aria-labelledby="plugin-title">
      <DetailHeading
        id="plugin-title"
        eyebrow="Installed plugin"
        title={props.plugin.name}
        summary={`A local Hames extension registered as ${props.plugin.id}. Review what it can do and which broker permissions it receives before enabling it.`}
        context={<p><code>{props.plugin.package_path}</code></p>}
        badges={
          <div class="plugin-heading-actions">
            <span class="plugin-state-badge" data-state={runtimeState(props.plugin).toLowerCase().replace(" ", "-")}>
              {runtimeState(props.plugin)}
            </span>
            <Button size="small" onClick={props.onAdd}>Add plugin</Button>
          </div>
        }
      />

      <DetailStatStrip
        label="Plugin status"
        items={[
          { label: "State", value: runtimeState(props.plugin) },
          { label: "Version", value: props.plugin.version || "Unknown" },
          { label: "Capabilities", value: String(props.plugin.capabilities.length) },
          { label: "Permissions", value: String(props.plugin.permissions.length) },
        ]}
      />

      <div class="plugin-settings-stack">
        <SettingsSection
          title="Runtime"
          description="Plugins install disabled. Starting one launches its isolated worker and registers the capabilities it advertises."
          action={
            <Switch
              class="plugin-enable-switch"
              label={props.plugin.enabled ? "Enabled" : "Disabled"}
              checked={props.plugin.enabled}
              disabled={changingState()}
              onCheckedChange={(enabled) => void setEnabled(enabled)}
            />
          }
        >
          <div class="plugin-runtime-panel">
            <div class="plugin-runtime-state">
              <span class="plugin-runtime-icon" data-running={props.plugin.running ? "true" : "false"}>
                <Icon name="nav.plugins" size={19} />
              </span>
              <div>
                <strong>{props.plugin.running ? "Worker connected" : props.plugin.enabled ? "Worker unavailable" : "Worker stopped"}</strong>
                <p>{props.plugin.running
                  ? "The plugin worker is active and answering through the Hames protocol."
                  : props.plugin.enabled
                  ? "Hames has this plugin enabled, but no worker is currently connected."
                  : "Enable this plugin when you want its capabilities available to agents."}</p>
              </div>
            </div>
            <Show when={props.plugin.warning}>
              <p class="plugin-runtime-warning" role="alert">{props.plugin.warning}</p>
            </Show>
            <Show when={actionError()}>
              <p class="plugin-runtime-warning" role="alert">{actionError()}</p>
            </Show>
          </div>
        </SettingsSection>
      </div>

      <Separator label="Capabilities" />
      <div class="plugin-capability-grid">
        <For each={props.plugin.capabilities}>{(capability) => (
          <article>
            <span>{capability.charAt(0).toUpperCase()}</span>
            <div><h2>{capabilityCopy[capability].title}</h2><p>{capabilityCopy[capability].description}</p></div>
          </article>
        )}</For>
      </div>

      <Show when={props.plugin.tools.length > 0}>
        <div class="plugin-tools-panel">
          <h2>Registered tools</h2>
          <ul><For each={props.plugin.tools}>{(tool) => <li><code>{tool}</code></li>}</For></ul>
        </div>
      </Show>

      <Separator label="Broker permissions" />
      <PluginPermissionList permissions={props.plugin.permissions} />

      <Separator label="Package record" />
      <dl class="plugin-package-record">
        <div><dt>Plugin ID</dt><dd><code>{props.plugin.id}</code></dd></div>
        <div><dt>Entrypoint</dt><dd><code>{props.plugin.entrypoint}</code></dd></div>
        <div><dt>Fingerprint</dt><dd><code>{props.plugin.fingerprint}</code></dd></div>
        <div><dt>Installed path</dt><dd><code>{props.plugin.package_path}</code></dd></div>
      </dl>

      <section class="plugin-danger-zone">
        <div><h2>Remove plugin</h2><p>Stop the worker and remove this installed package from Hames.</p></div>
        <Button class="plugin-remove-button" onClick={() => setConfirmingRemove(true)}>Remove</Button>
      </section>

      <Show when={confirmingRemove()}>
        <PluginRemoveDialog
          plugin={props.plugin}
          onClose={() => setConfirmingRemove(false)}
          onRemoved={() => props.onRemoved(props.plugin.id)}
        />
      </Show>
    </section>
  );
}
