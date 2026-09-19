import { canDeleteSkill } from "./eligibility";
import { deleteSkill } from "../api/client";
import { DeletableSidebarRow } from "../components/DeletableSidebarRow";
import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal } from "solid-js";
import type { SkillSource } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { LoadingState } from "../components/LoadingState";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { useWorkspace } from "../shell/workspace";
import { SkillCreateDialog } from "./SkillCreateDialog";
import { useSkillDirectory } from "./SkillDirectory";

const groups: readonly { source: SkillSource; label: string }[] = [
  { source: "managed", label: "Hames-created" },
  { source: "portable", label: "Global (~/.agents)" },
  { source: "builtin", label: "Built in" },
];

export function SkillSidebar() {
  const directory = useSkillDirectory();
  const workspace = useWorkspace();
  const search = useSidebarSearch();
  const navigate = useNavigate();
  const location = useLocation();

  const matchingSkills = () => {
    const query = search().trim().toLocaleLowerCase();
    if (!query) return directory.skills();
    return directory.skills().filter((skill) =>
      `${skill.name}\n${skill.slug}\n${skill.description}\n${skill.source}\n${skill.status}`
        .toLocaleLowerCase()
        .includes(query)
    );
  };

  const visibleGroups = () => {
    if (!search().trim()) return groups;
    return groups.filter((group) =>
      matchingSkills().some((skill) => skill.source === group.source)
    );
  };

  const authoringLabel = () => {
    const status = directory.authoringJob()?.status;
    if (status === "completed") return "Skill created";
    if (status === "failed" || status === "cancelled") return "Skill authoring stopped";
    if (status === "budget_wait") return "Skill authoring is waiting";
    return "Skill authoring started";
  };

  createEffect(() => {
    workspace.workingDirectory();
    void directory.ensureLoaded();
  });

  return (
    <div class="skill-sidebar-content">
      <Show when={directory.authoringJob()} keyed>{(job) => (
        <div class="skill-authoring-status" data-status={job.status} role="status">
          <strong>{authoringLabel()}</strong>
          <span>{job.error_message || job.goal}</span>
        </div>
      )}</Show>
      <Show when={directory.loading() && !directory.loaded()}>
        <LoadingState variant="sidebar" label="Loading Skills" />
      </Show>
      <Show when={directory.error()}>
        <div class="skill-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="skill-sidebar-list" aria-label="Skills">
          <For
            each={visibleGroups()}
            fallback={<p class="context-empty">No matching Skills.</p>}
          >{(group) => {
            const skills = () => matchingSkills().filter((skill) => skill.source === group.source);
            return (
              <CollapsibleSidebarGroup
                label={group.label}
                meta={<span>{skills().length}</span>}
                class="skill-sidebar-group"
              >
                <For each={skills()} fallback={<p class="skill-group-empty">No Skills found</p>}>
                  {(skill) => (
                    <DeletableSidebarRow name={skill.name} kind="Skill" eligible={canDeleteSkill(skill)}
                      description={<p>This removes the Hames-created Skill from the active catalog. Its immutable versions and audit evidence remain stored locally.</p>}
                      onDelete={async () => {
                        const sessionId = directory.sessionId();
                        if (!sessionId) throw new Error("No active session can delete this Skill.");
                        await deleteSkill(sessionId, skill.slug);
                        if (directory.sessionId() !== sessionId) return;
                        directory.remove(skill.slug);
                        if (location.pathname === `/skills/${encodeURIComponent(skill.slug)}`) navigate("/skills", { replace: true });
                      }}>
                      <A
                        href={`/skills/${encodeURIComponent(skill.slug)}`}
                        class="skill-sidebar-item"
                        activeClass="active"
                        end
                      >
                        <strong>{skill.name}</strong>
                        <span>{skill.slug} · {skill.archived ? "archived" : skill.status}</span>
                      </A>
                    </DeletableSidebarRow>
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

export function SkillSidebarAction() {
  const directory = useSkillDirectory();
  const workspace = useWorkspace();
  const [creating, setCreating] = createSignal(false);

  return (
    <>
      <Button
        variant="bare"
        size="small"
        class="sidebar-context-action sidebar-create-action sidebar-icon-action"
        aria-label="Create Skill"
        title="Create Skill"
        disabled={workspace.connection() !== "connected"}
        onClick={() => setCreating(true)}
      >
        <Icon name="action.add" size={15} />
      </Button>
      <Show when={creating()}>
        <SkillCreateDialog
          onClose={() => setCreating(false)}
          onCreate={directory.authorSkill}
          onQueued={() => setCreating(false)}
        />
      </Show>
    </>
  );
}
