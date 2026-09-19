import { For, Show, createSignal, onCleanup } from "solid-js";
import {
  discardPluginUpload,
  inspectPlugin,
  inspectPluginUpload,
  installPlugin,
  installPluginUpload,
} from "../api/client";
import type { PluginInspectView, PluginUploadFile, PluginView } from "../api/types";
import { Button } from "../components/Button";
import { Checkbox } from "../components/Checkbox";
import { DialogFrame } from "../components/DialogFrame";
import { TextField } from "../components/FormField";
import { Separator } from "../components/Separator";
import { Icon } from "../shell/icons";
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

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Unable to read ${file.name}`));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

function uploadPath(file: File, stripRoot: boolean): string {
  const raw = file.webkitRelativePath || file.name;
  const parts = raw.split("/").filter(Boolean);
  return stripRoot && parts.length > 1 ? parts.slice(1).join("/") : parts.join("/");
}

async function packageFiles(files: FileList): Promise<PluginUploadFile[]> {
  const selected = [...files];
  const roots = new Set(selected.map((file) => file.webkitRelativePath.split("/")[0]).filter(Boolean));
  const stripRoot = roots.size === 1 && !selected.some((file) => file.webkitRelativePath === "plugin.toml");
  return Promise.all(selected.map(async (file) => ({
    path: uploadPath(file, stripRoot),
    data_base64: await toBase64(file),
  })));
}

export function PluginInstallDialog(props: PluginInstallDialogProps) {
  const [path, setPath] = createSignal("");
  const [source, setSource] = createSignal<"computer" | "gateway">("computer");
  const [uploadId, setUploadId] = createSignal("");
  const [inspection, setInspection] = createSignal<PluginInspectView>();
  const [reviewed, setReviewed] = createSignal(false);
  const [busy, setBusy] = createSignal<"inspect" | "install" | "">("");
  const [error, setError] = createSignal("");
  let folderInput!: HTMLInputElement;
  let installed = false;

  const discardUpload = () => {
    const id = uploadId();
    setUploadId("");
    if (id) void discardPluginUpload(id).catch(() => undefined);
  };

  onCleanup(() => {
    if (!installed) discardUpload();
  });

  const inspectPath = async () => {
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

  const inspectFiles = async (files: FileList | null) => {
    if (!files?.length || busy()) return;
    discardUpload();
    setBusy("inspect");
    setError("");
    try {
      const result = await inspectPluginUpload(await packageFiles(files));
      setUploadId(result.upload_id);
      setInspection(result.plugin);
      setReviewed(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to inspect plugin package");
    } finally {
      setBusy("");
      if (folderInput) folderInput.value = "";
    }
  };

  const install = async () => {
    const plugin = inspection();
    if (!plugin || busy() || (plugin.permissions.length > 0 && !reviewed())) return;
    setBusy("install");
    setError("");
    try {
      const result = uploadId()
        ? await installPluginUpload(uploadId())
        : await installPlugin(path().trim());
      installed = true;
      setUploadId("");
      props.onInstalled(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to install plugin");
    } finally {
      setBusy("");
    }
  };

  const back = () => {
    discardUpload();
    setInspection(undefined);
    setReviewed(false);
    setError("");
  };
  const close = () => { if (!busy()) props.onClose(); };

  const footer = () => inspection() ? (
    <>
      <Show when={error()}><span class="plugin-dialog-error" role="alert">{error()}</span></Show>
      <Button variant="quiet" disabled={Boolean(busy())} onClick={back}>Back</Button>
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
      <Show when={source() === "gateway"}>
        <Button
          variant="primary"
          loading={busy() === "inspect"}
          disabled={!path().trim()}
          onClick={() => void inspectPath()}
        >Inspect package</Button>
      </Show>
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
            <input
              ref={(element) => {
                folderInput = element;
                element.setAttribute("webkitdirectory", "");
              }}
              class="plugin-file-input"
              type="file"
              multiple
              hidden
              tabIndex={-1}
              aria-label="Choose plugin folder"
              onChange={(event) => void inspectFiles(event.currentTarget.files)}
            />
            <Show when={source() === "computer"} fallback={
              <div class="plugin-gateway-path">
                <div class="plugin-dialog-intro">
                  <span class="plugin-dialog-glyph" aria-hidden="true"><Icon name="action.folder" size={17} /></span>
                  <div>
                    <h3>Use a gateway folder</h3>
                    <p>For local development, inspect a directory already readable by the Hames gateway.</p>
                  </div>
                </div>
                <TextField
                  label="Package directory"
                  value={path()}
                  placeholder="/home/you/projects/my-plugin"
                  helper="The folder must contain plugin.toml at its root."
                  spellcheck={false}
                  autocomplete="off"
                  autofocus
                  onInput={(event) => setPath(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void inspectPath();
                    }
                  }}
                />
                <Button variant="bare" class="plugin-source-switch" onClick={() => setSource("computer")}>
                  Upload from this computer instead
                </Button>
              </div>
            }>
              <div class="plugin-folder-picker">
                <span class="plugin-folder-picker-icon" aria-hidden="true"><Icon name="nav.plugins" size={23} /></span>
                <div>
                  <h3>Install from this computer</h3>
                  <p>Select a plugin folder. Hames uploads and inspects it before showing any requested permissions.</p>
                </div>
                <Button
                  variant="primary"
                  loading={busy() === "inspect"}
                  onClick={() => folderInput.click()}
                >Choose plugin folder</Button>
                <Button variant="bare" class="plugin-source-switch" onClick={() => setSource("gateway")}>
                  Use a path on this gateway
                </Button>
              </div>
            </Show>
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
