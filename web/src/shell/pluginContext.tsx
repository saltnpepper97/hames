import { createContext, useContext } from "solid-js";
import type { ParentProps } from "solid-js";
import type { WebPluginRegistry } from "./plugins";

interface WebPluginProviderProps extends ParentProps {
  registry: WebPluginRegistry;
}

const WebPluginContext = createContext<WebPluginRegistry>();

export function WebPluginProvider(props: WebPluginProviderProps) {
  return (
    <WebPluginContext.Provider value={props.registry}>
      {props.children}
    </WebPluginContext.Provider>
  );
}

export function useWebPlugins(): WebPluginRegistry {
  const registry = useContext(WebPluginContext);
  if (!registry) throw new Error("Web plugin registry is unavailable");
  return registry;
}
