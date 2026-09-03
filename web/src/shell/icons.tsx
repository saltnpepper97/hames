import { createContext, useContext } from "solid-js";
import type { Component, ParentProps } from "solid-js";
import { Dynamic } from "solid-js/web";

export const semanticIconNames = [
  "brand.mark",
  "nav.chat",
  "nav.runs",
  "nav.agents",
  "nav.memory",
  "nav.skills",
  "nav.scars",
  "nav.plugins",
  "nav.settings",
  "action.newChat",
  "action.add",
  "action.edit",
  "action.attach",
  "action.send",
  "action.stop",
  "action.expand",
  "action.back",
  "action.next",
  "action.selected",
  "action.collapseSidebar",
  "action.expandSidebar",
  "message.user",
  "conversation.tool",
  "conversation.reasoning",
  "mode.auto",
  "mode.plan",
  "mode.manual",
  "thinking.level",
  "state.empty",
] as const;

export type SemanticIconName = (typeof semanticIconNames)[number];

export interface IconGlyphProps {
  size?: string | number;
  class?: string;
  "aria-hidden"?: boolean;
}

export type IconGlyph = Component<IconGlyphProps>;

export interface IconPackPlugin {
  readonly id: string;
  readonly label: string;
  readonly icons: Readonly<Record<SemanticIconName, IconGlyph>>;
}

interface IconProviderProps extends ParentProps {
  pack: IconPackPlugin;
}

const IconPackContext = createContext<IconPackPlugin>();

export function IconProvider(props: IconProviderProps) {
  return (
    <IconPackContext.Provider value={props.pack}>
      {props.children}
    </IconPackContext.Provider>
  );
}

interface IconProps {
  name: SemanticIconName;
  label?: string;
  size?: number;
  class?: string;
}

export function Icon(props: IconProps) {
  const pack = useContext(IconPackContext);
  if (!pack) throw new Error("Icon rendered without an icon pack plugin");
  const size = () => `${props.size ?? 18}px`;
  return (
    <span
      class={`hames-icon ${props.class ?? ""}`}
      style={{ width: size(), height: size() }}
      role={props.label ? "img" : undefined}
      aria-label={props.label}
      aria-hidden={props.label ? undefined : true}
      data-icon={props.name}
      data-icon-pack={pack.id}
    >
      <Dynamic component={pack.icons[props.name]} size="100%" aria-hidden={true} />
    </span>
  );
}
