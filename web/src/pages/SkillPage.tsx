import { canDeleteSkill } from "../skills/eligibility";
import { useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { deleteSkill, getAvailableSkill } from "../api/client";
import type { SkillSource, SkillVersion } from "../api/types";
import { Button } from "../components/Button";
import { DeleteConfirmationDialog } from "../components/DeleteConfirmationDialog";
import { LoadingState } from "../components/LoadingState";
import { Markdown } from "../components/Markdown";
import { Separator } from "../components/Separator";
import { useSkillDirectory } from "../skills/SkillDirectory";
import { Icon } from "../shell/icons";

interface SkillPageProps {
  skillSlug: string;
}

function label(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function sourceLabel(source: SkillSource): string {
  if (source === "portable") return ".agents Skill";
  if (source === "builtin") return "Built-in Skill";
  return "Hames-created Skill";
}

function ChipList(props: { values: string[]; empty: string }) {
  return (
    <Show when={props.values.length > 0} fallback={<p class="skill-empty-detail">{props.empty}</p>}>
      <ul class="skill-chip-list">
        <For each={props.values}>{(value) => <li>{value}</li>}</For>
      </ul>
    </Show>
  );
}

export function SkillPage(props: SkillPageProps) {
  const directory = useSkillDirectory();
  const navigate = useNavigate();
  const [detail, setDetail] = createSignal<SkillVersion>();
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [confirmingDelete, setConfirmingDelete] = createSignal(false);
  const summary = () => directory.skills().find((skill) => skill.slug === props.skillSlug);
  let requestId = 0;

  const load = () => {
    const sessionId = directory.sessionId();
    if (!sessionId) return;
    const currentRequest = ++requestId;
    setLoading(true);
    setError("");
    setDetail(undefined);
    void getAvailableSkill(sessionId, props.skillSlug)
      .then((skill) => { if (currentRequest === requestId) setDetail(skill); })
      .catch((caught: unknown) => {
        if (currentRequest === requestId) {
          setError(caught instanceof Error ? caught.message : "Unable to load Skill");
        }
      })
      .finally(() => { if (currentRequest === requestId) setLoading(false); });
  };

  createEffect(load);
  onCleanup(() => { requestId += 1; });

  const remove = async () => {
    const sessionId = directory.sessionId();
    if (!sessionId) throw new Error("No active session can delete this Skill.");
    await deleteSkill(sessionId, props.skillSlug);
    directory.remove(props.skillSlug);
    setConfirmingDelete(false);
    navigate("/skills", { replace: true });
  };

  return (
    <>
    <section class="page skill-page" aria-labelledby="skill-title">
      <Show when={loading()}>
        <LoadingState variant="detail" label="Loading Skill" />
      </Show>
      <Show when={error()}>
        <div class="error-state">
          <div><span class="eyebrow">Skill error</span><h2>This Skill could not be loaded.</h2><p>{error()}</p></div>
          <Button onClick={load}>Try again</Button>
        </div>
      </Show>
      <Show when={detail()} keyed>{(skill) => (
        <>
          <header class="skill-heading">
            <div class="skill-heading-copy">
              <span class="eyebrow">{sourceLabel(summary()?.source ?? "managed")}</span>
              <h1 id="skill-title">{skill.name}</h1>
              <p>{skill.description}</p>
            </div>
            <Show when={canDeleteSkill(summary())}>
              <Button
                variant="icon"
                class="detail-delete-action"
                aria-label={`Delete ${skill.name}`}
                title="Delete Skill"
                onClick={() => setConfirmingDelete(true)}
              >
                <Icon name="action.delete" size={17} />
              </Button>
            </Show>
          </header>

          <div class="skill-stat-strip" aria-label="Skill status">
            <div><span>Status</span><strong>{summary()?.archived ? "Archived" : label(skill.status)}</strong></div>
            <div><span>Scope</span><strong>{label(skill.scope)}</strong></div>
            <div><span>Version</span><strong>{skill.version}</strong></div>
            <div><span>Invocation</span><strong>{label(skill.metadata.invocation)}</strong></div>
          </div>

          <Separator label="Procedure" />
          <div class="skill-procedure"><Markdown content={skill.instructions} /></div>

          <div class="skill-detail-grid">
            <section>
              <Separator label="Triggers" />
              <ChipList values={skill.metadata.triggers} empty="No explicit triggers." />
            </section>
            <section>
              <Separator label="Tools" />
              <ChipList values={skill.metadata.tools} empty="No declared tools." />
            </section>
            <section>
              <Separator label="Requirements" />
              <ChipList values={skill.metadata.requires} empty="No declared requirements." />
            </section>
            <section>
              <Separator label="Scripts" />
              <Show when={skill.metadata.scripts.length > 0} fallback={<p class="skill-empty-detail">No packaged scripts.</p>}>
                <ul class="skill-script-list">
                  <For each={skill.metadata.scripts}>{(script) => (
                    <li><strong>{script.id}</strong><code>{script.path}</code><span>{script.description}</span></li>
                  )}</For>
                </ul>
              </Show>
            </section>
          </div>

          <Separator label="Source" />
          <dl class="skill-metadata">
            <div><dt>Package</dt><dd><code>{skill.package_path}</code></dd></div>
            <div><dt>Created by</dt><dd>{label(skill.created_by)}</dd></div>
            <div><dt>Slug</dt><dd>{skill.slug}</dd></div>
            <div><dt>Content hash</dt><dd><code>{skill.content_hash}</code></dd></div>
          </dl>
        </>
      )}</Show>
      <Show when={!loading() && !error() && directory.loaded() && !summary()}>
        <div class="error-state">
          <div><span class="eyebrow">Skills</span><h2>This Skill is not available.</h2><p>It may have been removed or overridden in this workspace.</p></div>
        </div>
      </Show>
    </section>
    <Show when={confirmingDelete() && detail()} keyed>
      {(skill) => (
        <DeleteConfirmationDialog
          eyebrow="Delete Skill"
          title={`Delete ${skill.name}?`}
          confirmLabel="Delete Skill"
          onClose={() => setConfirmingDelete(false)}
          onConfirm={remove}
        >
          <p>This removes the Hames-created Skill from the active catalog.</p>
          <p>Its immutable versions and audit evidence remain stored locally.</p>
        </DeleteConfirmationDialog>
      )}
    </Show>
    </>
  );
}
