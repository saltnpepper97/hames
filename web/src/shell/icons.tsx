import { createContext, useContext } from "solid-js";
import type { JSX, ParentProps } from "solid-js";

export const semanticIconNames = [
  "nav.chat",
  "nav.runs",
  "nav.agents",
  "nav.memory",
  "nav.skills",
  "nav.scars",
  "nav.plugins",
  "nav.settings",
  "state.empty",
] as const;

export type SemanticIconName = (typeof semanticIconNames)[number];

export interface IconPackPlugin {
  readonly id: string;
  readonly label: string;
  readonly icons: Readonly<Record<SemanticIconName, string>>;
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
  const style = (): JSX.CSSProperties => ({
    "--hames-icon-source": `url("${pack.icons[props.name]}")`,
    width: size(),
    height: size(),
  });

  return (
    <span
      class={`hames-icon ${props.class ?? ""}`}
      style={style()}
      role={props.label ? "img" : undefined}
      aria-label={props.label}
      aria-hidden={props.label ? undefined : true}
      data-icon={props.name}
      data-icon-pack={pack.id}
    />
  );
}
