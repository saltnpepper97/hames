import type { AgentAvatarConfig } from "../api/types";

export interface HsvColor {
  h: number;
  s: number;
  v: number;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function hsvToHex({ h, s, v }: HsvColor): string {
  const hue = ((h % 360) + 360) % 360;
  const saturation = clamp(s, 0, 1);
  const value = clamp(v, 0, 1);
  const chroma = value * saturation;
  const section = hue / 60;
  const intermediate = chroma * (1 - Math.abs((section % 2) - 1));
  const [red, green, blue] =
    section < 1 ? [chroma, intermediate, 0]
      : section < 2 ? [intermediate, chroma, 0]
        : section < 3 ? [0, chroma, intermediate]
          : section < 4 ? [0, intermediate, chroma]
            : section < 5 ? [intermediate, 0, chroma]
              : [chroma, 0, intermediate];
  const match = value - chroma;
  const channel = (item: number) => Math.round((item + match) * 255)
    .toString(16)
    .padStart(2, "0");
  return `#${channel(red)}${channel(green)}${channel(blue)}`;
}

export function hexToHsv(hex: string): HsvColor {
  const normalized = /^#[0-9a-f]{6}$/i.test(hex) ? hex.slice(1) : "64748b";
  const red = Number.parseInt(normalized.slice(0, 2), 16) / 255;
  const green = Number.parseInt(normalized.slice(2, 4), 16) / 255;
  const blue = Number.parseInt(normalized.slice(4, 6), 16) / 255;
  const maximum = Math.max(red, green, blue);
  const minimum = Math.min(red, green, blue);
  const delta = maximum - minimum;
  let hue = 0;
  if (delta !== 0) {
    if (maximum === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (maximum === green) hue = 60 * ((blue - red) / delta + 2);
    else hue = 60 * ((red - green) / delta + 4);
  }
  return {
    h: (hue + 360) % 360,
    s: maximum === 0 ? 0 : delta / maximum,
    v: maximum,
  };
}

const fallbackColors = [
  "#2563eb", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#ca8a04",
  "#16a34a", "#0d9488", "#0891b2", "#4f46e5", "#475569", "#9333ea",
] as const;

export function fallbackAvatar(agentId: string): AgentAvatarConfig {
  let hash = 2166136261;
  for (const character of agentId) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  const index = Math.abs(hash) % fallbackColors.length;
  return {
    shape: "circle",
    eyes: "dots",
    face: "solid",
    color: fallbackColors[index] ?? "#64748b",
  };
}

export const avatarPalette = fallbackColors;
