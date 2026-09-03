import { Navigate, Route, Router } from "@solidjs/router";
import type { ParentProps } from "solid-js";
import { AgentDirectoryProvider } from "./agents/AgentDirectory";
import { MemoryDirectoryProvider } from "./memory/MemoryDirectory";
import { SkillDirectoryProvider } from "./skills/SkillDirectory";
import { ScarDirectoryProvider } from "./scars/ScarDirectory";
import { ThemeProvider } from "./theme/Theme";
import { AppShell } from "./components/AppShell";
import { coreWebPlugin } from "./plugins/core";
import { hamesIconPack } from "./plugins/icons/hames";
import { IconProvider } from "./shell/icons";
import { WebPluginProvider } from "./shell/pluginContext";
import { composeWebPlugins } from "./shell/plugins";
import { WorkspaceProvider } from "./shell/workspace";

const registry = composeWebPlugins([coreWebPlugin]);

function WorkspaceRoot(props: ParentProps) {
  return (
    <WebPluginProvider registry={registry}>
      <WorkspaceProvider>
        <AgentDirectoryProvider>
          <MemoryDirectoryProvider>
            <SkillDirectoryProvider>
              <ScarDirectoryProvider>
                <AppShell registry={registry}>{props.children}</AppShell>
              </ScarDirectoryProvider>
            </SkillDirectoryProvider>
          </MemoryDirectoryProvider>
        </AgentDirectoryProvider>
      </WorkspaceProvider>
    </WebPluginProvider>
  );
}

export function App() {
  return (
    <IconProvider pack={hamesIconPack}>
      <ThemeProvider>
        <Router root={WorkspaceRoot}>
          {registry.surfaces.map((surface) => (
            <Route path={surface.route} component={surface.component} />
          ))}
          <Route path="/" component={() => <Navigate href="/chat" />} />
          <Route path="*404" component={() => <Navigate href="/chat" />} />
        </Router>
      </ThemeProvider>
    </IconProvider>
  );
}
