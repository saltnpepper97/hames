import { createEffect, createMemo, createSignal, onCleanup, Show } from "solid-js";
import { listProviders, probeProvider } from "../api/client";
import type { AgentDetail, ProviderModel } from "../api/types";
import { Button } from "../components/Button";
import { modelReasoningEfforts } from "../chat/modelCapabilities";
import { Select } from "../components/Select";

type Selection = AgentDetail["default_model"];
type ModelChoice = { provider: string; model: ProviderModel };

export function AgentDefaultModel(props: { value: Selection; ready?: boolean; inheritLabel?: string; onChange: (value: Selection) => void }) {
  const [choices, setChoices] = createSignal<ModelChoice[]>([]);
  const [loading, setLoading] = createSignal(false);
  let initialized = false;
  let disposed = false;
  onCleanup(() => { disposed = true; });
  const [error, setError] = createSignal("");
  const key = (provider: string, model: string) => JSON.stringify([provider, model]);
  const selected = () => props.value ? key(props.value.provider, props.value.model) : "";
  const options = createMemo(() => {
    const values = choices().map(choice => ({ value: key(choice.provider, choice.model.id), label: `${choice.provider}/${choice.model.id}` }));
    if (props.value && !values.some(option => option.value === selected())) {
      values.unshift({ value: selected(), label: `${props.value.provider}/${props.value.model}` });
    }
    return [{ value: "", label: props.inheritLabel ?? "No default — use chat model" }, ...values];
  });
  const efforts = createMemo(() => {
    const model = choices().find(choice => key(choice.provider, choice.model.id) === selected())?.model;
    const values = model ? modelReasoningEfforts(model) : [];
    if (props.value?.reasoning_effort && !values.includes(props.value.reasoning_effort)) values.push(props.value.reasoning_effort);
    return [{ value: "", label: "Provider default" }, ...values.filter(Boolean).map(value => ({ value, label: value }))];
  });
  const load = async () => {
    if (loading() || disposed || props.ready === false) return;
    setLoading(true); setError("");
    try {
      const profiles = await listProviders();
      const results = await Promise.allSettled(profiles.map(async profile => {
        const probe = await probeProvider(profile.id);
        if (!probe.reachable) throw new Error(`${profile.id} unavailable`);
        return probe.models.map(model => ({ provider: profile.id, model }));
      }));
      if (disposed) return;
      setChoices(results.flatMap(result => result.status === "fulfilled" ? result.value : [])
        .sort((a, b) => a.provider.localeCompare(b.provider) || a.model.id.localeCompare(b.model.id, undefined, { numeric: true })));
      if (results.some(result => result.status === "rejected")) setError("Some providers are unavailable. Saved defaults are preserved.");
    } catch (caught) { if (!disposed) setError(caught instanceof Error ? caught.message : "Unable to load models"); }
    finally { if (!disposed) setLoading(false); }
  };
  createEffect(() => {
    if (props.ready !== false && !initialized) { initialized = true; void load(); }
  });
  return <div class="agent-field-grid">
    <div class="agent-model-selection">
      <Select ariaLabel="Default model" value={selected()} options={options()} onValueChange={value => {
        if (!value) { props.onChange(null); return; }
        const [provider, model] = JSON.parse(value) as [string, string];
        props.onChange({ provider, model, reasoning_effort: "" });
      }} />
      <Button disabled={props.ready === false} loading={loading()} onClick={() => void load()}>Refresh</Button>
    </div>
    <Show when={props.value}>
      <Select ariaLabel="Default reasoning effort" value={props.value?.reasoning_effort || ""} options={efforts()} onValueChange={reasoning_effort => {
        if (props.value) props.onChange({ ...props.value, reasoning_effort });
      }} />
    </Show>
    <Show when={error()}><p role="status">{error()}</p></Show>
  </div>;
}
