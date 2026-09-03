import { Match, Show, Switch } from "solid-js";
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
              <path class="agent-avatar-shell" d="M40 10c7 0 11 6 12 12 6-4 14-2 17 5 3 6 1 13-5 17 7 3 9 11 5 18-4 8-14 10-21 4-3 8-13 10-20 4-6-4-7-10-5-15-8 4-17 0-19-8-2-7 3-14 9-16-5-5-5-13 0-18 4-6 12-7 18-3 0-8 4-14 11-14Z" />
            </Match>
            <Match when={props.config.shape === "hex"}>
              <path class="agent-avatar-shell" d="m40 10 27 15v30L40 70 13 55V25Z" />
            </Match>
            <Match when={props.config.shape === "circle"}>
              <circle class="agent-avatar-shell" cx="40" cy="43" r="29" />
            </Match>
          </Switch>
          <g class="agent-avatar-gaze">
            <Show when={props.config.face !== "none"}>
              <rect
                class="agent-avatar-face"
                classList={{ outline: props.config.face === "outline" }}
                x="22"
                y={faceY()}
                width="36"
                height={faceHeight()}
                rx="10"
              />
            </Show>
            <g class="agent-avatar-eyes">
              <Switch>
                <Match when={props.config.eyes === "visor"}>
                  <rect x="28" y={eyeY() - 4} width="24" height="7" rx="3.5" />
                  <path class="agent-eye-glint" d={`m33 ${eyeY() - 3} 6 5`} />
                </Match>
                <Match when={props.config.eyes === "pill"}>
                  <rect x="29" y={eyeY() - 5} width="6" height="10" rx="3" />
                  <rect x="45" y={eyeY() - 5} width="6" height="10" rx="3" />
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
