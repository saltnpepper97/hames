import { Match, Switch } from "solid-js";
import type { AgentAvatarConfig } from "../api/types";

interface AgentAvatarProps {
  config: AgentAvatarConfig;
  name: string;
  size?: number;
  animated?: boolean;
}

export function AgentAvatar(props: AgentAvatarProps) {
  const size = () => props.size ?? 72;
  const faceY = () => props.config.shape === "triangle" ? 40 : 31;
  const faceHeight = () => props.config.shape === "triangle" ? 20 : 24;
  const eyeY = () => props.config.shape === "triangle" ? 50 : 43;
  const animationOffset = () => {
    const seed = [...props.name].reduce((total, character) => total + character.charCodeAt(0), 0);
    return `${-(seed % 47) / 10}s`;
  };
  return (
    <span
      class="agent-avatar"
      classList={{ alive: props.animated !== false }}
      style={{
        width: `${size()}px`,
        height: `${size()}px`,
        color: props.config.color,
        "--agent-animation-offset": animationOffset(),
      }}
      role="img"
      aria-label={`${props.name} avatar`}
    >
      <svg viewBox="0 0 80 80" aria-hidden="true">
        <g class="agent-avatar-body">
          <path class="agent-antenna" d="M40 17V10m0 0 5-4m-5 4-5-4" />
          <Switch>
            <Match when={props.config.shape === "square"}>
              <rect class="agent-avatar-shell" x="13" y="18" width="54" height="52" rx="8" />
            </Match>
            <Match when={props.config.shape === "triangle"}>
              <path class="agent-avatar-shell" d="M36 12Q40 5 44 12l27 50q4 8-5 8H14q-9 0-5-8Z" />
            </Match>
            <Match when={props.config.shape === "cloud"}>
              <path class="agent-avatar-shell" d="M20 69Q7 69 7 56q0-12 11-15 0-21 21-23 16-1 22 14 12 1 12 16 0 21-21 21Z" />
            </Match>
            <Match when={props.config.shape === "hex"}>
              <path class="agent-avatar-shell" d="m40 10 27 15v30L40 70 13 55V25Z" />
            </Match>
            <Match when={props.config.shape === "round"}>
              <rect class="agent-avatar-shell" x="12" y="16" width="56" height="56" rx="24" />
            </Match>
          </Switch>
          <rect class="agent-avatar-face" x="22" y={faceY()} width="36" height={faceHeight()} rx="10" />
          <g class="agent-avatar-gaze">
            <g class="agent-avatar-eyes">
              <Switch>
                <Match when={props.config.eyes === "visor"}>
                  <rect x="28" y={eyeY() - 4} width="24" height="7" rx="3.5" />
                  <path class="agent-eye-glint" d={`m33 ${eyeY() - 3} 6 5`} />
                </Match>
                <Match when={props.config.eyes === "happy"}>
                  <path class="agent-happy-eye" d={`M28 ${eyeY() + 2}c1-5 7-5 8 0M44 ${eyeY() + 2}c1-5 7-5 8 0`} />
                </Match>
                <Match when={props.config.eyes === "dots"}>
                  <circle cx="32" cy={eyeY()} r="3" />
                  <circle cx="48" cy={eyeY()} r="3" />
                </Match>
              </Switch>
            </g>
          </g>
        </g>
      </svg>
    </span>
  );
}
