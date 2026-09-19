import { For, Show, createEffect, createResource, createSignal, onCleanup, onMount } from "solid-js";
import { connectProvider, disconnectProvider, listConnections, testConnection } from "../api/client";
import type { ProviderConnection } from "../api/client";
import { Button } from "../components/Button";
import { TextField } from "../components/FormField";
import { DialogFrame } from "../components/DialogFrame";

const statusLabels = {
  not_connected: "Not connected",
  not_checked: "Not checked",
  connected: "Connected",
  unavailable: "Connection unavailable",
};

export function Connections(props: { ready?: boolean } = {}) {
  const [loadError, setLoadError] = createSignal(false);
  const [connections, { mutate, refetch }] = createResource(async () => {
    setLoadError(false);
    try { return await listConnections(); }
    catch { setLoadError(true); return []; }
  });
  const [selected, setSelected] = createSignal<ProviderConnection>();
  const [key, setKey] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [checking, setChecking] = createSignal("");
  let disposed = false;
  let initialized = false;
  let refreshing = false;
  let revision = 0;
  let lastCheck = 0;
  async function refreshStatus() {
    if (props.ready === false || disposed || refreshing || busy() || document.visibilityState === "hidden") return;
    refreshing = true;
    lastCheck = Date.now();
    const generation = revision;
    try {
      for (const connection of connections() ?? []) {
        if (disposed || busy() || generation !== revision) break;
        if (!connection.configured) continue;
        setChecking(connection.id);
        try {
          const rows = await testConnection(connection.id);
          if (disposed || generation !== revision) break;
          const checked = rows.find(row => row.id === connection.id);
          if (checked) mutate(current => current?.map(row => row.id === checked.id ? checked : row));
        } catch {
          // A failed status request is not proof the provider disconnected.
          if (!disposed && generation === revision) setError("Could not refresh connection status. Use Test to retry.");
        }
      }
    } finally {
      refreshing = false;
      if (!disposed) setChecking("");
    }
  }
  createEffect(() => {
    if (props.ready !== false && !connections.loading && !loadError() && connections() && !initialized) {
      initialized = true;
      void refreshStatus();
    }
  });
  onMount(() => {
    const refreshIfDue = () => { if (Date.now() - lastCheck >= 60_000) void refreshStatus(); };
    const timer = setInterval(refreshIfDue, 60_000);
    document.addEventListener("visibilitychange", refreshIfDue);
    onCleanup(() => {
      disposed = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfDue);
    });
  });
  const close = () => {
    if (busy()) return;
    setSelected(undefined);
    setKey("");
    setError("");
  };
  async function act(id: string, action: () => Promise<ProviderConnection[]>, dismiss = false) {
    if (busy()) return;
    revision += 1;
    setBusy(id);
    setError("");
    try {
      mutate(await action());
      if (dismiss) {
        setSelected(undefined);
        setKey("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update this connection.");
    } finally {
      setBusy("");
    }
  }
  return (
    <section id="settings-connections" class="settings-page-section" aria-labelledby="settings-connections-title">
      <header class="settings-page-section-heading">
        <h2 id="settings-connections-title">Connections</h2>
        <p>Connect a provider, then choose its model in any chat.</p>
      </header>
      <Show when={connections.loading}><p>Loading connections…</p></Show>
      <Show when={loadError()}><p role="alert">Could not load connections. <Button variant="quiet" onClick={() => refetch()}>Retry</Button></p></Show>
      <div class="connections-list">
        <For each={connections()}>{(connection) => (
          <div class="connection-row">
            <div class="connection-copy">
              <strong>{connection.name}</strong>
              <span class="provider-connection-status" data-status={connection.status}>{checking() === connection.id && connection.status === "not_checked" ? "Checking…" : statusLabels[connection.status]}{connection.source === "environment" ? " · Environment" : connection.source === "saved" ? " · Key saved" : ""}</span>
              <Show when={connection.models.length}>
                <details class="connection-models">
                  <summary>{connection.models.length} {connection.model_source === "documented" ? "documented models" : "models"}</summary>
                  <ul>
                    <For each={[...connection.models].sort((left, right) => left.localeCompare(right, "en", { numeric: true, sensitivity: "base" }))}>
                      {(model) => <li>{model}</li>}
                    </For>
                  </ul>
                </details>
              </Show>
              <Show when={!connection.can_connect && !connection.can_disconnect && connection.source !== "environment"}>
                <small>Managed through Hames setup</small>
              </Show>
            </div>
            <div class="connection-actions">
              <Show when={connection.configured}>
                <Button variant="quiet" size="sm" loading={checking() === connection.id} disabled={!!busy() || !!checking()} onClick={() => act(connection.id, () => testConnection(connection.id))}>Test</Button>
              </Show>
              <Show when={connection.can_disconnect}>
                <Button variant="quiet" size="sm" disabled={!!busy()} onClick={() => act(connection.id, () => disconnectProvider(connection.id))}>Disconnect</Button>
              </Show>
              <Show when={connection.can_connect}>
                <Button variant={connection.can_disconnect ? "quiet" : "secondary"} size="sm" disabled={!!busy()} onClick={() => { setError(""); setKey(""); setSelected(connection); }}>
                  {connection.can_disconnect ? "Replace key" : "Connect"}
                </Button>
              </Show>
            </div>
          </div>
        )}</For>
      </div>
      <Show when={error() && !selected()}><p role="alert">{error()}</p></Show>
      <Show when={selected()}>{(connection) => (
        <DialogFrame eyebrow="Provider connection" title={`Connect ${connection().name}`} onClose={close}
          footer={<><Button variant="quiet" disabled={!!busy()} onClick={close}>Cancel</Button><Button variant="primary" loading={!!busy()} disabled={!key().trim()} onClick={() => act(connection().id, () => connectProvider(connection().id, key().trim()), true)}>Connect</Button></>}>
          <div class="connection-form">
            <p>Enter your API key. Hames saves it privately on this computer.</p>
            <Show when={connection().id === "zai_coding"}><p>Uses your Coding Plan endpoint. Your plan must allow access from this tool.</p></Show>
            <a href={connection().key_url} target="_blank" rel="noopener noreferrer">Get an API key ↗</a>
            <TextField label="API key" id="provider-api-key" type="password" autocomplete="off" spellcheck={false} value={key()} disabled={!!busy()} onInput={(event) => setKey(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Enter" && key().trim()) void act(connection().id, () => connectProvider(connection().id, key().trim()), true); }} />
            <Show when={error()}><p role="alert">{error()}</p></Show>
          </div>
        </DialogFrame>
      )}</Show>
    </section>
  );
}
