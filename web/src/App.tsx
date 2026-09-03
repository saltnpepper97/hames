import { Navigate, Route, Router } from "@solidjs/router";
import type { ParentProps } from "solid-js";
import { AppShell } from "./components/AppShell";
import { coreWebPlugin } from "./plugins/core";
import { phosphorIconPack } from "./plugins/icons/phosphor";
import { IconProvider } from "./shell/icons";
import { composeWebPlugins } from "./shell/plugins";
import { WorkspaceProvider } from "./shell/workspace";

const registry = composeWebPlugins([coreWebPlugin]);

function WorkspaceRoot(props: ParentProps) {
  return (
    <WorkspaceProvider>
      <AppShell registry={registry}>{props.children}</AppShell>
    </WorkspaceProvider>
  );
}

export function App() {
  return (
    <IconProvider pack={phosphorIconPack}>
      <Router root={WorkspaceRoot}>
        {registry.surfaces.map((surface) => (
          <Route path={surface.route} component={surface.component} />
        ))}
        <Route path="/" component={() => <Navigate href="/chat" />} />
        <Route path="*404" component={() => <Navigate href="/chat" />} />
      </Router>
    </IconProvider>
  );
}
