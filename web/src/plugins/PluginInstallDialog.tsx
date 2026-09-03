import { For, Show, createSignal } from "solid-js";
import { inspectPlugin, installPlugin } from "../api/client";
import type { PluginInspectView, PluginView } from "../api/types";
import { Button } from "../components/Button";
import { Checkbox } from "../components/Checkbox";
import { DialogFrame } from "../components/DialogFrame";
import { TextField } from "../components/FormField";
import { Separator } from "../components/Separator";
import { PluginPermissionList } from "./PluginPermissionList";

interface PluginInstallDialogProps {
  onClose: () => void;
  onInstalled: (plugin: PluginView) => void;
}

const capabilityCopy: Readonly<Record<string, string>> = {
  tool: "Adds callable tools",
  context: "Contributes context",
  event: "Observes runtime events",
};

export function PluginInstallDialog(props: PluginInstallDialogProps) {
  const [path, setPath] = createSignal("");
  const [inspection, setInspection] = createSignal<PluginInspectView>();
  const [reviewed, setReviewed] = createSignal(false);
  const [busy, setBusy] = createSignal<"inspect" | "install" | "">("");
  const [error, setError] = createSignal("");

  const inspect = async () => {
    const selectedPath = path().trim();
    if (!selectedPath || busy()) return;
    setBusy("inspect");
    setError("");
    try {
      setInspection(await inspectPlugin(selectedPath));
      setReviewed(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to inspect plugin package");
    } finally {
      setBusy("");
    }
  };

  const install = async () => {
    if (!inspection() || busy() || (inspection()!.permissions.length > 0 && !reviewed())) return;
    setBusy("install");
    setError("");
    try {
      props.onInstalled(await installPlugin(path().trim()));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to install plugin");
    } finally {
      setBusy("");
    }
  };

  const close = () => { if (!busy()) props.onClose(); };
  const footer = () => inspection() ? (
    <>
      <Show when={error()}><span class="plugin-dialog-error" role="alert">{error()}</span></Show>
      <Button variant="quiet" disabled={Boolean(busy())} onClick={() => {
        setInspection(undefined);
        setReviewed(false);
        setError("");
      }}>Back</Button>
      <Button
        variant="primary"
        loading={busy() === "install"}
        disabled={inspection()!.permissions.length > 0 && !reviewed()}
        onClick={() => void install()}
      >Install plugin</Button>
    </>
  ) : (
    <>
      <Show when={error()}><span class="plugin-dialog-error" role="alert">{error()}</span></Show>
      <Button variant="quiet" disabled={Boolean(busy())} onClick={close}>Cancel</Button>
      <Button
        variant="primary"
        loading={busy() === "inspect"}
        disabled={!path().trim()}
        onClick={() => void inspect()}
      >Inspect package</Button>
    </>
  );

  return (
    <DialogFrame
      eyebrow={inspection() ? "Permission review" : "Local package"}
      title={inspection() ? `Review ${inspection()!.name}` : "Add a plugin"}
      class="plugin-install-dialog"
      footer={footer()}
      onClose={close}
    >
      <div class="plugin-install-body">
        <Show when={inspection()} keyed fallback={
          <div class="plugin-path-step">
            <div class="plugin-dialog-intro">
              <span class="plugin-dialog-glyph" aria-hidden="true">+</span>
              <div>
                <h3>Install from this computer</h3>
                <p>Choose a package directory containing a valid <code>plugin.toml</code>. Hames inspects it before anything is installed.</p>
              </div>
            </div>
            <TextField
              label="Package directory"
              value={path()}
              placeholder="/home/you/projects/my-plugin"
              helper="Enter a path that the local Hames gateway can read."
              spellcheck={false}
              autocomplete="off"
              autofocus
              onInput={(event) => setPath(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void inspect();
                }
              }}
            />
          </div>
        }>{(plugin) => (
          <div class="plugin-review-step">
            <section class="plugin-manifest-summary">
              <div>
                <span class="plugin-package-mark" aria-hidden="true">{plugin.name.charAt(0).toUpperCase()}</span>
                <div>
                  <h3>{plugin.name}</h3>
                  <p><code>{plugin.id}</code> · version {plugin.version}</p>
                </div>
              </div>
              <dl>
                <div><dt>Entrypoint</dt><dd><code>{plugin.entrypoint}</code></dd></div>
                <div><dt>Files</dt><dd>{plugin.files.length}</dd></div>
                <div><dt>Fingerprint</dt><dd title={plugin.fingerprint}><code>{plugin.fingerprint.slice(0, 12)}</code></dd></div>
              </dl>
            </section>

            <Separator label="Capabilities" meta={<span>{plugin.capabilities.length}</span>} />
            <ul class="plugin-capability-list">
              <For each={plugin.capabilities}>{(capability) => (
                <li><strong>{capability}</strong><span>{capabilityCopy[capability] ?? "Declared plugin capability"}</span></li>
              )}</For>
            </ul>

            <Separator label="Requested permissions" meta={<span>{plugin.permissions.length}</span>} />
            <PluginPermissionList permissions={plugin.permissions} />

            <Show when={plugin.permissions.length > 0}>
              <Checkbox
                class="plugin-permission-approval"
                checked={reviewed()}
                onCheckedChange={setReviewed}
                label="I reviewed these permissions"
                description="Installation copies the package into Hames. It remains disabled until you explicitly enable it."
              />
            </Show>
            <Show when={plugin.permissions.length === 0}>
              <p class="plugin-install-note">This package requests no broker permissions. It will remain disabled after installation.</p>
            </Show>
          </div>
        )}</Show>
      </div>
    </DialogFrame>
  );
}
