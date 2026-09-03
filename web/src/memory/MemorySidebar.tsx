import { A } from "@solidjs/router";
import { For, Show, createEffect } from "solid-js";
import type { MemoryLayer } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { useWorkspace } from "../shell/workspace";
import { useMemoryDirectory } from "./MemoryDirectory";

const groups: readonly { layer: MemoryLayer; label: string }[] = [
  { layer: "relationship", label: "Relationships" },
  { layer: "semantic", label: "Semantic" },
  { layer: "episodic", label: "Episodes" },
];

function visibilityLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function MemorySidebar() {
  const directory = useMemoryDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.snapshot()?.bootstrap.working_directory;
    void directory.ensureLoaded();
  });

  return (
    <div class="memory-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <div class="memory-sidebar-loading" aria-label="Loading memory"><span /><span /><span /></div>
      </Show>
      <Show when={directory.error()}>
        <div class="memory-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="memory-sidebar-list" aria-label="Memories">
          <For each={groups}>{(group) => {
            const records = () => directory.records().filter((record) => record.layer === group.layer);
            return (
              <CollapsibleSidebarGroup
                  label={group.label}
                  meta={<span>{records().length}</span>}
                  class="memory-sidebar-group"
              >
                <For each={records()} fallback={<p class="memory-group-empty">No active records</p>}>
                  {(record) => (
                    <A
                      href={`/memory/${encodeURIComponent(record.id)}`}
                      class="memory-sidebar-item"
                      activeClass="active"
                      end
                    >
                      <strong>{record.summary}</strong>
                      <span>{visibilityLabel(record.visibility)}</span>
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
