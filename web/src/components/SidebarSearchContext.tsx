import { createContext, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";

const SidebarSearchContext = createContext<Accessor<string>>(() => "");

interface SidebarSearchProviderProps extends ParentProps {
  query: Accessor<string>;
}

export function SidebarSearchProvider(props: SidebarSearchProviderProps) {
  return (
    <SidebarSearchContext.Provider value={props.query}>
      {props.children}
    </SidebarSearchContext.Provider>
  );
}

export function useSidebarSearch(): Accessor<string> {
  return useContext(SidebarSearchContext);
}
