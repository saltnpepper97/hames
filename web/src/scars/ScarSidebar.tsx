import { A } from "@solidjs/router";
import { For, Show, createEffect } from "solid-js";
import type { Scar, ScarStatus } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { useWorkspace } from "../shell/workspace";
import { useScarDirectory } from "./ScarDirectory";

const groups: readonly { label: string; statuses: readonly ScarStatus[] }[] = [
  { label: "Needs attention", statuses: ["candidate", "open", "repair_proposed", "regressed"] },
  { label: "Guarded", statuses: ["guarded"] },
  { label: "History", statuses: ["healed", "dismissed"] },
];

function groupScars(scars: Scar[], statuses: readonly ScarStatus[]): Scar[] {
  return scars.filter((scar) => statuses.includes(scar.status));
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function ScarSidebar() {
  const directory = useScarDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.snapshot()?.bootstrap.working_directory;
    void directory.ensureLoaded();
  });

  return (
    <div class="scar-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <div class="scar-sidebar-loading" aria-label="Loading Scars"><span /><span /><span /></div>
      </Show>
      <Show when={directory.error()}>
        <div class="scar-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="scar-sidebar-list" aria-label="Scars">
          <For each={groups}>{(group) => {
            const scars = () => groupScars(directory.scars(), group.statuses);
            return (
              <CollapsibleSidebarGroup
                label={group.label}
                meta={<span>{scars().length}</span>}
                class="scar-sidebar-group"
              >
                <For each={scars()} fallback={<p class="scar-group-empty">No Scars</p>}>
                  {(scar) => (
                    <A
                      href={`/scars/${encodeURIComponent(scar.id)}`}
                      class="scar-sidebar-item"
                      activeClass="active"
                      end
                    >
                      <span class="scar-sidebar-marker" data-severity={scar.severity} aria-hidden="true" />
                      <span class="scar-sidebar-copy">
                        <strong>{scar.title}</strong>
                        <span>{label(scar.status)} · {scar.severity}</span>
                      </span>
                    </A>
                  )}
                </For>
              </CollapsibleSidebarGroup>
            );
          }}</For>
        </nav>
      </Show>
    </div>
  );
}
