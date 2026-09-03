import brain from "@phosphor-icons/core/regular/brain.svg";
import horse from "@phosphor-icons/core/regular/horse.svg";
import robot from "@phosphor-icons/core/regular/robot.svg";
import {
  IconArrowUp,
  IconArrowLeft,
  IconBandage,
  IconChevronDown,
  IconChevronRight,
  IconCheck,
  IconGitBranch,
  IconHandStop,
  IconListCheck,
  IconMessageCircle,
  IconMessages,
  IconPencil,
  IconPlug,
  IconPlus,
  IconSettings,
  IconSparkles,
  IconSquare,
  IconTool,
} from "@tabler/icons-solidjs";
import type { Component, JSX } from "solid-js";
import type { IconGlyph, IconGlyphProps, IconPackPlugin } from "../../shell/icons";

function phosphorIcon(source: string): IconGlyph {
  return (props: IconGlyphProps) => {
    const style = (): JSX.CSSProperties => ({
      "--hames-icon-source": `url("${source}")`,
      width: typeof props.size === "number" ? `${props.size}px` : props.size,
      height: typeof props.size === "number" ? `${props.size}px` : props.size,
    });

    return (
      <span
        class={`phosphor-icon ${props.class ?? ""}`}
        style={style()}
        aria-hidden={props["aria-hidden"]}
      />
    );
  };
}

function tablerIcon(Glyph: Component<IconGlyphProps & { strokeWidth?: number }>): IconGlyph {
  return (props: IconGlyphProps) => (
    <Glyph {...props} strokeWidth={1.7} />
  );
}

export const hamesIconPack = {
  id: "hames-default",
  label: "Hames Default (Phosphor + Tabler)",
  icons: {
    "brand.mark": phosphorIcon(horse),
    "nav.chat": tablerIcon(IconMessageCircle),
    "nav.runs": tablerIcon(IconGitBranch),
    "nav.agents": phosphorIcon(robot),
    "nav.memory": phosphorIcon(brain),
    "nav.skills": tablerIcon(IconTool),
    "nav.scars": tablerIcon(IconBandage),
    "nav.plugins": tablerIcon(IconPlug),
    "nav.settings": tablerIcon(IconSettings),
    "action.newChat": tablerIcon(IconPlus),
    "action.edit": tablerIcon(IconPencil),
    "action.attach": tablerIcon(IconPlus),
    "action.send": tablerIcon(IconArrowUp),
    "action.stop": tablerIcon(IconSquare),
    "action.expand": tablerIcon(IconChevronDown),
    "action.back": tablerIcon(IconArrowLeft),
    "action.next": tablerIcon(IconChevronRight),
    "action.selected": tablerIcon(IconCheck),
    "mode.auto": tablerIcon(IconSparkles),
    "mode.plan": tablerIcon(IconListCheck),
    "mode.manual": tablerIcon(IconHandStop),
    "thinking.level": phosphorIcon(brain),
    "state.empty": tablerIcon(IconMessages),
  },
} satisfies IconPackPlugin;
