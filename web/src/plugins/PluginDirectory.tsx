import { createContext, createSignal, useContext } from "solid-js";
import type { Accessor, ParentProps } from "solid-js";
import { listPlugins } from "../api/client";
import type { PluginView } from "../api/types";

interface PluginDirectoryContextValue {
  plugins: Accessor<PluginView[]>;
  loading: Accessor<boolean>;
  loaded: Accessor<boolean>;
  error: Accessor<string>;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  upsert: (plugin: PluginView) => void;
  remove: (pluginId: string) => void;
}

const PluginDirectoryContext = createContext<PluginDirectoryContextValue>();

export function usePluginDirectory(): PluginDirectoryContextValue {
  const context = useContext(PluginDirectoryContext);
  if (!context) throw new Error("Plugin directory rendered outside its provider");
  return context;
}

export function PluginDirectoryProvider(props: ParentProps) {
  const [plugins, setPlugins] = createSignal<PluginView[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [loaded, setLoaded] = createSignal(false);
  const [error, setError] = createSignal("");
  let pending: Promise<void> | undefined;

  const refresh = (): Promise<void> => {
    if (pending) return pending;
    setLoading(true);
    setError("");
    pending = listPlugins()
      .then((items) => {
        setPlugins([...items].sort((left, right) => left.name.localeCompare(right.name)));
        setLoaded(true);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Unable to load plugins");
      })
      .finally(() => {
        setLoading(false);
        pending = undefined;
      });
    return pending;
  };

  const upsert = (plugin: PluginView) => {
    setPlugins((current) => {
      const next = current.filter((item) => item.id !== plugin.id);
      next.push(plugin);
      return next.sort((left, right) => left.name.localeCompare(right.name));
    });
    setLoaded(true);
  };

  return (
    <PluginDirectoryContext.Provider value={{
      plugins,
      loading,
      loaded,
      error,
      ensureLoaded: () => loaded() ? Promise.resolve() : refresh(),
      refresh,
      upsert,
      remove: (pluginId) => setPlugins((current) => current.filter((item) => item.id !== pluginId)),
    }}>
      {props.children}
    </PluginDirectoryContext.Provider>
  );
}
