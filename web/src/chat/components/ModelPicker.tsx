import { A } from "@solidjs/router";
import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import {
  HamesApiError,
  listProviders,
  probeProvider,
  updateSessionSelection,
} from "../../api/client";
import type { ProviderModel, ProviderProfile, Session } from "../../api/types";
import { Button } from "../../components/Button";
import { LoadingState } from "../../components/LoadingState";
import { DropdownSurface } from "../../components/DropdownSurface";
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
type PickerDirection = "forward" | "back";

function providerLabel(profile: ProviderProfile): string {
  if (profile.adapter === "llama_cpp") return "llama.cpp";
  if (profile.adapter === "ollama") return "Ollama";
  if (profile.adapter === "openai") return "OpenAI API";
  if (profile.adapter === "xai") return "Grok API";
  if (profile.adapter === "mimo") return "Xiaomi MiMo API";
  if (profile.adapter === "mimo_token_plan") return "Xiaomi MiMo Token Plan";
  if (profile.adapter === "deepseek") return "DeepSeek API";
  if (profile.adapter === "zai") return "Z.ai API";
  if (profile.adapter === "zai_coding") return "Z.ai Coding Plan";
  if (profile.adapter === "grok") return "Grok Build";
  if (profile.adapter === "codex") return "Codex / ChatGPT";
  return profile.id;
}

function shouldProbe(profile: ProviderProfile, sessionProvider: string): boolean {
  if (profile.id === sessionProvider) return true;
  if (["openai", "xai", "grok", "codex", "deepseek", "zai", "zai_coding", "mimo", "mimo_token_plan"].includes(profile.adapter)) return true;
  return Boolean(profile.configured_model.trim());
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
  const [direction, setDirection] = createSignal<PickerDirection>("forward");
  const [groups, setGroups] = createSignal<ProviderGroup[]>([]);
  const [pending, setPending] = createSignal<ModelIdentity>();
  const [loadingCatalog, setLoadingCatalog] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [pickerError, setPickerError] = createSignal("");
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
    try {
      const profiles = await listProviders();
      const connectedProfiles = profiles.filter((profile) =>
        shouldProbe(profile, props.session.provider)
      );
      const results = await Promise.all(connectedProfiles.map(async (profile) => {
        try {
          const probe = await probeProvider(profile.id);
          if (!probe.reachable) {
            throw new HamesApiError(probe.error?.message ?? `${providerLabel(profile)} is not hooked up`);
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
    setDirection("forward");
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
    setDirection("forward");
    setPane("effort");
  };

  const openCurrentEffort = () => {
    setPending(currentIdentity());
    setDirection("forward");
    setPane("effort");
  };

  const back = () => {
    setDirection("back");
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
        // Switching panes removes the focused row; that is not an outside focus move.
        if (event.relatedTarget && !root.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <Button
        variant="bare"
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
      </Button>

      <DropdownSurface
        open={open()}
        class="model-picker-popover"
        morph
        layout={pane()}
        direction={direction()}
        role="menu"
        ariaLabel="Model and thinking"
      >
          <Show when={pane() === "root"}>
            <A class="model-picker-cell" role="menuitem" href="/settings/connections" onClick={() => setOpen(false)}>Connect provider</A>
            <Button
              variant="bare"
              class="model-picker-cell"
              role="menuitem"
              onClick={() => {
                setDirection("forward");
                setPane("models");
              }}
            >
              <span>Model</span>
              <span>{props.session.model}</span>
              <Icon name="action.next" size={14} />
            </Button>
            <Show when={loadingCatalog() || effortChoices().length > 0}>
              <Button variant="bare" class="model-picker-cell" role="menuitem" onClick={openCurrentEffort}>
                <span>Thinking</span>
                <span>{reasoningEffortLabel(props.session.reasoning_effort)}</span>
                <Icon name="action.next" size={14} />
              </Button>
            </Show>
          </Show>

          <Show when={pane() !== "root"}>
            <Button variant="bare" class="model-picker-back" onClick={back}>
              <Icon name="action.back" size={16} />
              <span>{pane() === "models" ? "Model and thinking" : "Back"}</span>
            </Button>
          </Show>

          <Show when={pane() === "models"}>
            <Show when={!loadingCatalog()} fallback={<LoadingState variant="inline" label="Loading models" class="model-picker-state" />}>
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
                            <Button
                              variant="bare"
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
                            </Button>
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
            <Show when={!loadingCatalog()} fallback={<LoadingState variant="inline" label="Loading thinking" class="model-picker-state" />}>
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
                      <Button
                        variant="bare"
                        role="menuitemradio"
                        aria-checked={selected()}
                        disabled={saving()}
                        onClick={() => void commit(effortIdentity(), effort)}
                      >
                        <span>{reasoningEffortLabel(effort)}</span>
                        <span class="model-selected-mark">
                          <Show when={selected()}><Icon name="action.selected" size={14} /></Show>
                        </span>
                      </Button>
                    );
                  }}
                </For>
              </div>
            </Show>
          </Show>

          <Show when={pickerError()}>
            <div class="model-picker-error" role="alert">{pickerError()}</div>
          </Show>
      </DropdownSurface>
    </div>
  );
}
