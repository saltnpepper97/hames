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
  return (
    <span
      class="agent-avatar"
      classList={{ alive: props.animated !== false }}
      style={{ width: `${size()}px`, height: `${size()}px`, color: props.config.color }}
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
            <Match when={props.config.shape === "arch"}>
              <path class="agent-avatar-shell" d="M13 70V43a27 27 0 0 1 54 0v27Z" />
            </Match>
            <Match when={props.config.shape === "capsule"}>
              <rect class="agent-avatar-shell" x="18" y="12" width="44" height="62" rx="22" />
            </Match>
            <Match when={props.config.shape === "hex"}>
              <path class="agent-avatar-shell" d="m40 10 27 15v30L40 70 13 55V25Z" />
            </Match>
            <Match when={props.config.shape === "round"}>
              <rect class="agent-avatar-shell" x="12" y="16" width="56" height="56" rx="24" />
            </Match>
          </Switch>
          <rect class="agent-avatar-face" x="22" y="31" width="36" height="24" rx="10" />
          <g class="agent-avatar-eyes">
            <Switch>
              <Match when={props.config.eyes === "visor"}>
                <rect x="28" y="39" width="24" height="7" rx="3.5" />
                <path class="agent-eye-glint" d="m33 40 6 5" />
              </Match>
              <Match when={props.config.eyes === "happy"}>
                <path class="agent-happy-eye" d="M28 45c1-5 7-5 8 0M44 45c1-5 7-5 8 0" />
              </Match>
              <Match when={props.config.eyes === "dots"}>
                <circle cx="32" cy="43" r="3" />
                <circle cx="48" cy="43" r="3" />
              </Match>
            </Switch>
          </g>
        </g>
      </svg>
    </span>
  );
}
