import { For, createSignal } from "solid-js";
import type { AgentAvatarConfig, AgentAvatarEyes, AgentAvatarShape } from "../api/types";
import { AgentAvatar } from "./AgentAvatar";
import { ColorWheel } from "./ColorWheel";
import { avatarPalette } from "./color";
import { DialogFrame } from "../components/DialogFrame";

interface AgentAvatarEditorProps {
  agentName: string;
  initial: AgentAvatarConfig;
  saving: boolean;
  error: string;
  onSave: (avatar: AgentAvatarConfig) => void;
  onClose: () => void;
}

const shapes: { id: AgentAvatarShape; label: string }[] = [
  { id: "round", label: "Round" },
  { id: "square", label: "Soft square" },
  { id: "triangle", label: "Triangle" },
  { id: "cloud", label: "Cloud" },
  { id: "hex", label: "Hex" },
];

const eyes: { id: AgentAvatarEyes; label: string }[] = [
  { id: "dots", label: "Dots" },
  { id: "visor", label: "Visor" },
  { id: "happy", label: "Happy" },
];

export function AgentAvatarEditor(props: AgentAvatarEditorProps) {
  const [draft, setDraft] = createSignal<AgentAvatarConfig>({ ...props.initial });

  return (
    <DialogFrame
      eyebrow="Visual identity"
      title={`Customize ${props.agentName}`}
      class="avatar-dialog"
      onClose={props.onClose}
      footer={<>
        <span class="avatar-save-error" role="alert">{props.error}</span>
        <button class="button" type="button" onClick={props.onClose}>Cancel</button>
        <button class="button primary" type="button" disabled={props.saving} onClick={() => props.onSave(draft())}>
          {props.saving ? "Saving…" : "Save avatar"}
        </button>
      </>}
    >
        <div class="avatar-editor-layout">
          <div class="avatar-preview-panel">
            <AgentAvatar config={draft()} name={props.agentName} size={150} />
            <strong>{props.agentName}</strong>
            <span>{draft().color.toUpperCase()}</span>
          </div>

          <div class="avatar-controls">
            <fieldset>
              <legend>Shape</legend>
              <div class="avatar-option-grid shapes">
                <For each={shapes}>{(shape) => (
                  <button
                    type="button"
                    class="avatar-option"
                    classList={{ selected: draft().shape === shape.id }}
                    aria-pressed={draft().shape === shape.id}
                    onClick={() => setDraft((current) => ({ ...current, shape: shape.id }))}
                  >
                    <AgentAvatar config={{ ...draft(), shape: shape.id }} name={shape.label} size={44} animated={false} />
                    <span>{shape.label}</span>
                  </button>
                )}</For>
              </div>
            </fieldset>

            <fieldset>
              <legend>Eyes</legend>
              <div class="avatar-option-grid eyes">
                <For each={eyes}>{(eye) => (
                  <button
                    type="button"
                    class="avatar-option"
                    classList={{ selected: draft().eyes === eye.id }}
                    aria-pressed={draft().eyes === eye.id}
                    onClick={() => setDraft((current) => ({ ...current, eyes: eye.id }))}
                  >
                    <AgentAvatar config={{ ...draft(), eyes: eye.id }} name={eye.label} size={44} animated={false} />
                    <span>{eye.label}</span>
                  </button>
                )}</For>
              </div>
            </fieldset>

            <fieldset>
              <legend>Color</legend>
              <div class="avatar-color-layout">
                <ColorWheel value={draft().color} onInput={(color) => setDraft((current) => ({ ...current, color }))} />
                <div class="avatar-palette" aria-label="Suggested colors">
                  <For each={avatarPalette}>{(color) => (
                    <button
                      type="button"
                      aria-label={`Use ${color}`}
                      aria-pressed={draft().color === color}
                      classList={{ selected: draft().color === color }}
                      style={{ background: color }}
                      onClick={() => setDraft((current) => ({ ...current, color }))}
                    />
                  )}</For>
                </div>
              </div>
            </fieldset>
          </div>
        </div>
    </DialogFrame>
  );
}
