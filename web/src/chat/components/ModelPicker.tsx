import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import {
  HamesApiError,
  listProviders,
  probeProvider,
  updateSessionSelection,
} from "../../api/client";
import type { ProviderModel, ProviderProfile, Session } from "../../api/types";
import { Icon } from "../../shell/icons";
import { modelReasoningEfforts, reasoningEffortLabel } from "../modelCapabilities";

interface ModelPickerProps {
  session: Session;
  disabled: boolean;
  onSessionUpdated: (session: Session) => void;
  onError: (message: string) => void;
}

interface ProviderGroup {
  profile: ProviderProfile;
  models: ProviderModel[];
}

interface ModelIdentity {
  provider: string;
  model: string;
}

type PickerPane = "root" | "models" | "effort";

function providerLabel(profile: ProviderProfile): string {
  if (profile.adapter === "llama_cpp") return "llama.cpp";
  if (profile.adapter === "ollama") return "Ollama";
  if (profile.adapter === "openai") return "OpenAI API";
  if (profile.adapter === "codex") return "Codex / ChatGPT";
  return profile.id;
}

function mutationError(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not update the model.";
}

function modelDetail(model: ProviderModel): string {
  if (model.parameter_size) return model.parameter_size;
  if (model.status && model.status !== "available") return model.status;
  if (model.context_length) return `${Math.round(model.context_length / 1000)}k context`;
  return "Available";
}

export function ModelPicker(props: ModelPickerProps) {
  const [open, setOpen] = createSignal(false);
  const [pane, setPane] = createSignal<PickerPane>("root");
  const [groups, setGroups] = createSignal<ProviderGroup[]>([]);
  const [pending, setPending] = createSignal<ModelIdentity>();
  const [loadingCatalog, setLoadingCatalog] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [pickerError, setPickerError] = createSignal("");
  const [failures, setFailures] = createSignal<string[]>([]);
  let root!: HTMLDivElement;
  let catalogRequest = 0;

  const currentIdentity = (): ModelIdentity => ({
    provider: props.session.provider,
    model: props.session.model,
  });
  const effortIdentity = () => pending() ?? currentIdentity();
  const effortModel = createMemo(() => {
    const identity = effortIdentity();
    return groups()
      .find((group) => group.profile.id === identity.provider)
      ?.models.find((model) => model.id === identity.model);
  });
  const effortChoices = createMemo(() => {
    const model = effortModel();
    return model ? modelReasoningEfforts(model) : [];
  });

  const fail = (error: unknown) => {
    const message = mutationError(error);
    setPickerError(message);
    props.onError(message);
  };

  const loadCatalog = async () => {
    const request = ++catalogRequest;
    setLoadingCatalog(true);
    setPickerError("");
    setFailures([]);
    try {
      const profiles = await listProviders();
      const results = await Promise.all(profiles.map(async (profile) => {
        try {
          const probe = await probeProvider(profile.id);
          if (!probe.reachable) {
            throw new HamesApiError(probe.error?.message ?? `${profile.id} is unavailable`);
          }
          return { profile, models: probe.models, error: "" };
        } catch (error) {
          return { profile, models: [], error: mutationError(error) };
        }
      }));
      if (request !== catalogRequest) return;
      setGroups(results
        .filter((result) => result.models.length > 0)
        .map(({ profile, models }) => ({ profile, models })));
      setFailures(results
        .filter((result) => result.error)
        .map((result) => `${providerLabel(result.profile)}: ${result.error}`));
    } catch (error) {
      if (request === catalogRequest) fail(error);
    } finally {
      if (request === catalogRequest) setLoadingCatalog(false);
    }
  };

  const toggle = () => {
    if (open()) {
      setOpen(false);
      return;
    }
    setPane("root");
    setPending();
    setOpen(true);
    void loadCatalog();
  };

  const commit = async (identity: ModelIdentity, effort: string) => {
    if (saving()) return;
    setSaving(true);
    setPickerError("");
    try {
      const updated = await updateSessionSelection(
        props.session.id,
        identity.provider,
        identity.model,
        effort,
      );
      props.onSessionUpdated(updated);
      setOpen(false);
      setPane("root");
      setPending();
    } catch (error) {
      fail(error);
    } finally {
      setSaving(false);
    }
  };

  const chooseModel = (provider: string, model: ProviderModel) => {
    const identity = { provider, model: model.id };
    const efforts = modelReasoningEfforts(model);
    if (efforts.length === 0) {
      void commit(identity, "off");
      return;
    }
    setPending(identity);
    setPane("effort");
  };

  const openCurrentEffort = () => {
    setPending(currentIdentity());
    setPane("effort");
  };

  const back = () => {
    if (pane() === "effort" && pending() && (
      pending()!.provider !== props.session.provider || pending()!.model !== props.session.model
    )) {
      setPane("models");
      return;
    }
    setPane("root");
    setPending();
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
    <div
      class="model-picker"
      ref={root}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open()) return;
        event.preventDefault();
        if (pane() === "root") setOpen(false);
        else back();
      }}
      onFocusOut={(event) => {
        if (!root.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        class="model-trigger"
        type="button"
        aria-label={`Model and thinking: ${props.session.model}, ${reasoningEffortLabel(props.session.reasoning_effort)}`}
        aria-haspopup="menu"
        aria-expanded={open()}
        title={`${props.session.provider} / ${props.session.model} · ${reasoningEffortLabel(props.session.reasoning_effort)}`}
        disabled={props.disabled || saving()}
        onClick={toggle}
      >
        <span class="model-trigger-label">{props.session.model}</span>
        <span class="model-trigger-effort">{reasoningEffortLabel(props.session.reasoning_effort)}</span>
        <Icon name="action.expand" size={13} />
      </button>

      <Show when={open()}>
        <div class="model-picker-popover" role="menu" aria-label="Model and thinking">
          <Show when={pane() === "root"}>
            <button class="model-picker-cell" type="button" role="menuitem" onClick={() => setPane("models")}>
              <span>Model</span>
              <span>{props.session.model}</span>
              <Icon name="action.next" size={14} />
            </button>
            <Show when={loadingCatalog() || effortChoices().length > 0}>
              <button class="model-picker-cell" type="button" role="menuitem" onClick={openCurrentEffort}>
                <span>Thinking</span>
                <span>{reasoningEffortLabel(props.session.reasoning_effort)}</span>
                <Icon name="action.next" size={14} />
              </button>
            </Show>
          </Show>

          <Show when={pane() !== "root"}>
            <button class="model-picker-back" type="button" onClick={back}>
              <Icon name="action.back" size={16} />
              <span>{pane() === "models" ? "Model and thinking" : "Back"}</span>
            </button>
          </Show>

          <Show when={pane() === "models"}>
            <Show when={!loadingCatalog()} fallback={<div class="model-picker-state">Loading models…</div>}>
              <div class="model-groups">
                <For each={groups()} fallback={<div class="model-picker-state">No models reported.</div>}>
                  {(group) => (
                    <section class="model-group" role="group" aria-label={providerLabel(group.profile)}>
                      <div class="model-group-heading">{providerLabel(group.profile)}</div>
                      <For each={group.models}>
                        {(model) => {
                          const selected = () =>
                            group.profile.id === props.session.provider && model.id === props.session.model;
                          return (
                            <button
                              type="button"
                              role="menuitemradio"
                              aria-checked={selected()}
                              disabled={saving()}
                              onClick={() => chooseModel(group.profile.id, model)}
                            >
                              <span>
                                <strong>{model.id}</strong>
                                <small>{modelDetail(model)}</small>
                              </span>
                              <span class="model-selected-mark">
                                <Show when={selected()}><Icon name="action.selected" size={14} /></Show>
                              </span>
                            </button>
                          );
                        }}
                      </For>
                    </section>
                  )}
                </For>
              </div>
            </Show>
          </Show>

          <Show when={pane() === "effort"}>
            <div class="model-pane-heading">
              <strong>{effortIdentity().model}</strong>
              <span>Choose thinking to finish</span>
            </div>
            <Show when={!loadingCatalog()} fallback={<div class="model-picker-state">Loading thinking…</div>}>
              <div class="model-picker-options">
                <For each={effortChoices()} fallback={
                  <div class="model-picker-state">This model does not offer thinking levels.</div>
                }>
                  {(effort) => {
                    const selected = () =>
                      effortIdentity().provider === props.session.provider &&
                      effortIdentity().model === props.session.model &&
                      effort === props.session.reasoning_effort;
                    return (
                      <button
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected()}
                        disabled={saving()}
                        onClick={() => void commit(effortIdentity(), effort)}
                      >
                        <span>{reasoningEffortLabel(effort)}</span>
                        <span class="model-selected-mark">
                          <Show when={selected()}><Icon name="action.selected" size={14} /></Show>
                        </span>
                      </button>
                    );
                  }}
                </For>
              </div>
            </Show>
          </Show>

          <For each={failures()}>
            {(failure) => <div class="model-picker-warning">{failure}</div>}
          </For>
          <Show when={pickerError()}>
            <div class="model-picker-error" role="alert">{pickerError()}</div>
          </Show>
        </div>
      </Show>
    </div>
  );
}
