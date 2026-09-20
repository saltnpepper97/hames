import brain from "@phosphor-icons/core/regular/brain.svg";
import robot from "@phosphor-icons/core/regular/robot.svg";
import {
  IconArrowUp,
  IconMaximize,
  IconMinimize,
  IconArrowLeft,
  IconBandage,
  IconChevronDown,
  IconChevronRight,
  IconCheck,
  IconFolder,
  IconFolderPlus,
  IconFileText,
  IconGitBranch,
  IconHandStop,
  IconListCheck,
  IconChecklist,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMessageCircle,
  IconMessageCirclePlus,
  IconMessages,
  IconMenu2,
  IconMoonStars,
  IconDots,
  IconPencil,
  IconPin,
  IconPinnedOff,
  IconPlug,
  IconPlus,
  IconPrompt,
  IconSearch,
  IconSettings,
  IconSparkles,
  IconSquare,
  IconTool,
  IconTrash,
  IconUser,
  IconX,
} from "@tabler/icons-solidjs";
import type { Component, JSX } from "solid-js";
import hamesMark from "../../assets/hames.png";
import type { IconGlyph, IconGlyphProps, IconPackPlugin } from "../../shell/icons";

function imageIcon(source: string): IconGlyph {
  return (props: IconGlyphProps) => (
    <img
      class={`brand-image-icon ${props.class ?? ""}`}
      src={source}
      alt=""
      aria-hidden={props["aria-hidden"]}
    />
  );
}

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
    "brand.mark": imageIcon(hamesMark),
    "nav.chat": tablerIcon(IconMessageCircle),
    "nav.runs": tablerIcon(IconGitBranch),
    "nav.agents": phosphorIcon(robot),
    "nav.memory": phosphorIcon(brain),
    "nav.skills": tablerIcon(IconTool),
    "nav.scars": tablerIcon(IconBandage),
    "nav.plugins": tablerIcon(IconPlug),
    "nav.settings": tablerIcon(IconSettings),
    "action.newChat": tablerIcon(IconMessageCirclePlus),
    "action.add": tablerIcon(IconPlus),
    "action.folder": tablerIcon(IconFolder),
    "action.folderAdd": tablerIcon(IconFolderPlus),
    "action.file": tablerIcon(IconFileText),
    "action.search": tablerIcon(IconSearch),
    "action.close": tablerIcon(IconX),
    "action.more": tablerIcon(IconDots),
    "action.edit": tablerIcon(IconPencil),
    "action.pin": tablerIcon(IconPin),
    "action.unpin": tablerIcon(IconPinnedOff),
    "action.delete": tablerIcon(IconTrash),
    "action.menu": tablerIcon(IconMenu2),
    "action.attach": tablerIcon(IconPlus),
    "action.send": tablerIcon(IconArrowUp),
    "action.stop": tablerIcon(IconSquare),
    "action.expand": tablerIcon(IconChevronDown),
    "action.maximize": tablerIcon(IconMaximize),
    "action.restore": tablerIcon(IconMinimize),
    "action.back": tablerIcon(IconArrowLeft),
    "action.next": tablerIcon(IconChevronRight),
    "action.selected": tablerIcon(IconCheck),
    "action.collapseSidebar": tablerIcon(IconLayoutSidebarLeftCollapse),
    "action.expandSidebar": tablerIcon(IconLayoutSidebarLeftExpand),
    "message.user": tablerIcon(IconUser),
    "conversation.tool": tablerIcon(IconTool),
    "conversation.tasks": tablerIcon(IconListCheck),
    "conversation.reasoning": phosphorIcon(brain),
    "conversation.context": tablerIcon(IconPrompt),
    "conversation.wrapUp": tablerIcon(IconSparkles),
    "conversation.dream": tablerIcon(IconMoonStars),
    "mode.auto": tablerIcon(IconSparkles),
    "mode.plan": tablerIcon(IconChecklist),
    "mode.manual": tablerIcon(IconHandStop),
    "thinking.level": phosphorIcon(brain),
    "state.empty": tablerIcon(IconMessages),
  },
} satisfies IconPackPlugin;
