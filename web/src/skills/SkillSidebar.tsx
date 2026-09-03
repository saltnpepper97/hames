import { A } from "@solidjs/router";
import { For, Show, createEffect } from "solid-js";
import type { SkillSource } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { useWorkspace } from "../shell/workspace";
import { useSkillDirectory } from "./SkillDirectory";

const groups: readonly { source: SkillSource; label: string }[] = [
  { source: "managed", label: "Hames-created" },
  { source: "portable", label: ".agents" },
  { source: "builtin", label: "Built in" },
];

export function SkillSidebar() {
  const directory = useSkillDirectory();
  const workspace = useWorkspace();

  createEffect(() => {
    workspace.snapshot()?.bootstrap.working_directory;
    void directory.ensureLoaded();
  });

  return (
    <div class="skill-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <div class="skill-sidebar-loading" aria-label="Loading Skills"><span /><span /><span /></div>
      </Show>
      <Show when={directory.error()}>
        <div class="skill-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="skill-sidebar-list" aria-label="Skills">
          <For each={groups}>{(group) => {
            const skills = () => directory.skills().filter((skill) => skill.source === group.source);
            return (
              <CollapsibleSidebarGroup
                label={group.label}
                meta={<span>{skills().length}</span>}
                class="skill-sidebar-group"
              >
                <For each={skills()} fallback={<p class="skill-group-empty">No Skills found</p>}>
                  {(skill) => (
                    <A
                      href={`/skills/${encodeURIComponent(skill.slug)}`}
                      class="skill-sidebar-item"
                      activeClass="active"
                      end
                    >
                      <strong>{skill.name}</strong>
                      <span>{skill.slug} · {skill.scope}</span>
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
