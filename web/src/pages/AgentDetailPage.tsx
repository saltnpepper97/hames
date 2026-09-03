import { A } from "@solidjs/router";
import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { AgentAvatar } from "../agents/AgentAvatar";
import { AgentAvatarEditor } from "../agents/AgentAvatarEditor";
import { fallbackAvatar } from "../agents/color";
import {
  getAgent,
  getAgentCapabilities,
  updateAgent,
  updateAgentAvatar,
} from "../api/client";
import type {
  AgentAvatarConfig,
  AgentDetail,
  SkillSummary,
} from "../api/types";
import { Button } from "../components/Button";
import { TextAreaField, TextField } from "../components/FormField";
import { SelectionRow } from "../components/SelectionRow";
import { SettingsSection } from "../components/SettingsSection";
import { Icon } from "../shell/icons";

interface AgentDetailPageProps {
  agentId: string;
  workingDirectory: string;
}

function effectiveSelection(all: string[], allow: string[], deny: string[]): Set<string> {
  const denied = new Set(deny);
  if (allow.length > 0) return new Set(allow.filter((id) => !denied.has(id)));
  return new Set(all.filter((id) => !denied.has(id)));
}

function withConfigured(values: string[], ...configured: string[][]): string[] {
  return [...new Set([...values, ...configured.flat()])].sort();
}

function accessUpdate(all: string[], selected: Set<string>, pins?: Set<string>) {
  const deny = all.filter((id) => !selected.has(id));
  return {
    allow: deny.length === 0 ? [] : all.filter((id) => selected.has(id)),
    deny,
    ...(pins ? { pin: [...pins].filter((id) => selected.has(id)).sort() } : {}),
  };
}

export function AgentDetailPage(props: AgentDetailPageProps) {
  const [agent, setAgent] = createSignal<AgentDetail>();
  const [name, setName] = createSignal("");
  const [instructions, setInstructions] = createSignal("");
  const [toolIds, setToolIds] = createSignal<string[]>([]);
  const [skills, setSkills] = createSignal<SkillSummary[]>([]);
  const [selectedTools, setSelectedTools] = createSignal(new Set<string>());
  const [selectedSkills, setSelectedSkills] = createSignal(new Set<string>());
  const [pinnedSkills, setPinnedSkills] = createSignal(new Set<string>());
  const [toolQuery, setToolQuery] = createSignal("");
  const [skillQuery, setSkillQuery] = createSignal("");
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [saveError, setSaveError] = createSignal("");
  const [saved, setSaved] = createSignal(false);
  const [editingAvatar, setEditingAvatar] = createSignal(false);
  let requestGeneration = 0;

  const load = async (agentId: string) => {
    const generation = ++requestGeneration;
    setLoading(true);
    setError("");
    try {
      const [nextAgent, nextCapabilities] = await Promise.all([
        getAgent(agentId),
        getAgentCapabilities(agentId, props.workingDirectory),
      ]);
      if (generation !== requestGeneration) return;
      const allTools = withConfigured(
        nextCapabilities.tools,
        nextAgent.tools_allow,
        nextAgent.tools_deny,
      );
      const knownSkillSlugs = nextCapabilities.skills.map((skill) => skill.slug);
      const allSkillSlugs = withConfigured(
        knownSkillSlugs,
        nextAgent.skills_allow,
        nextAgent.skills_deny,
        nextAgent.skills_pin,
      );
      const bySlug = new Map(nextCapabilities.skills.map((skill) => [skill.slug, skill]));
      setAgent(nextAgent);
      setName(nextAgent.name);
      setInstructions(nextAgent.instructions);
      setToolIds(allTools);
      setSkills(allSkillSlugs.map((slug) => bySlug.get(slug) ?? {
        slug,
        name: slug,
        description: "Not currently available in this workspace",
        scope: "agent",
      }));
      setSelectedTools(effectiveSelection(allTools, nextAgent.tools_allow, nextAgent.tools_deny));
      setSelectedSkills(
        effectiveSelection(allSkillSlugs, nextAgent.skills_allow, nextAgent.skills_deny),
      );
      setPinnedSkills(new Set(nextAgent.skills_pin));
    } catch (caught) {
      if (generation === requestGeneration) {
        setError(caught instanceof Error ? caught.message : "Unable to load this agent");
      }
    } finally {
      if (generation === requestGeneration) setLoading(false);
    }
  };

  createEffect(() => {
    const agentId = props.agentId;
    if (!props.workingDirectory) return;
    void load(agentId);
  });

  const setMembership = (
    setter: typeof setSelectedTools,
    id: string,
    selected: boolean,
  ) => setter((current) => {
    const next = new Set(current);
    if (selected) next.add(id);
    else next.delete(id);
    return next;
  });

  const visibleTools = createMemo(() => {
    const query = toolQuery().trim().toLocaleLowerCase();
    return query ? toolIds().filter((id) => id.toLocaleLowerCase().includes(query)) : toolIds();
  });
  const visibleSkills = createMemo(() => {
    const query = skillQuery().trim().toLocaleLowerCase();
    return query
      ? skills().filter((skill) => `${skill.slug} ${skill.name} ${skill.description}`.toLocaleLowerCase().includes(query))
      : skills();
  });
  const nameError = createMemo(() => {
    const length = name().trim().length;
    return length === 0 ? "Name is required" : length > 80 ? "Use 80 characters or fewer" : "";
  });
  const instructionsError = createMemo(() =>
    instructions().trim() ? "" : "AGENT.md instructions cannot be empty"
  );

  const save = async () => {
    if (saving() || nameError() || instructionsError()) return;
    setSaving(true);
    setSaveError("");
    setSaved(false);
    try {
      const updated = await updateAgent(props.agentId, {
        name: name().trim(),
        instructions: instructions().trim(),
        tools: accessUpdate(toolIds(), selectedTools()),
        skills: accessUpdate(skills().map((skill) => skill.slug), selectedSkills(), pinnedSkills()),
      });
      setAgent(updated);
      setName(updated.name);
      setInstructions(updated.instructions);
      setSaved(true);
    } catch (caught) {
      setSaveError(caught instanceof Error ? caught.message : "Unable to save this agent");
    } finally {
      setSaving(false);
    }
  };

  const saveAvatar = async (avatar: AgentAvatarConfig) => {
    if (saving()) return;
    setSaving(true);
    setSaveError("");
    try {
      const updated = await updateAgentAvatar(props.agentId, avatar);
      setAgent(updated);
      setEditingAvatar(false);
      setSaved(true);
    } catch (caught) {
      setSaveError(caught instanceof Error ? caught.message : "Unable to save this avatar");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section class="page agent-detail-page" aria-labelledby="agent-detail-title">
      <Show when={loading()}>
        <div class="agent-detail-loading"><span /><span /><span /></div>
      </Show>
      <Show when={error()}>
        <div class="error-state">
          <div><span class="eyebrow">Agent error</span><h2>This agent could not be loaded.</h2><p>{error()}</p></div>
          <Button onClick={() => void load(props.agentId)}>Try again</Button>
        </div>
      </Show>
      <Show when={!loading() && !error() && agent()} keyed>{(current) => {
        const avatar = () => current.avatar ?? fallbackAvatar(current.id);
        return <>
          <header class="agent-detail-header">
            <A class="agent-back" href="/agents"><Icon name="action.back" size={16} />Agents</A>
            <div class="agent-detail-identity">
              <Button variant="bare" class="agent-detail-avatar" aria-label={`Edit ${current.name} appearance`} onClick={() => setEditingAvatar(true)}>
                <AgentAvatar config={avatar()} name={current.name} size={112} />
              </Button>
              <div>
                <span class="eyebrow">Agent</span>
                <h1 id="agent-detail-title">{current.name}</h1>
                <p>{current.id} · {current.authority === "read_only" ? "Read only" : "Standard authority"}</p>
              </div>
            </div>
            <div class="agent-save-bar">
              <span class="agent-save-state" classList={{ error: Boolean(saveError()) }} role="status">
                {saveError() || (saved() ? "Changes saved" : "Changes write directly to AGENT.md")}
              </span>
              <Button variant="primary" loading={saving()} disabled={Boolean(nameError() || instructionsError())} onClick={() => void save()}>
                Save changes
              </Button>
            </div>
          </header>

          <div class="agent-settings-stack">
            <SettingsSection title="Identity" description="The display name can change. The agent slug is permanent once created.">
              <div class="agent-field-grid">
                <TextField label="Display name" value={name()} maxlength={80} error={nameError()} onInput={(event) => { setName(event.currentTarget.value); setSaved(false); }} />
                <TextField label="Agent slug" value={current.id} disabled helper="Permanent ID used by sessions and history" />
              </div>
            </SettingsSection>

            <SettingsSection title="AGENT.md instructions" description="Markdown instructions added to this agent's context on every turn.">
              <TextAreaField
                label="Instructions"
                value={instructions()}
                rows={14}
                code
                error={instructionsError()}
                onInput={(event) => { setInstructions(event.currentTarget.value); setSaved(false); }}
              />
            </SettingsSection>

            <SettingsSection title="Tools" description="Tools can only reduce the authority already granted by Hames and workspace policy.">
              <TextField label="Filter tools" value={toolQuery()} placeholder="Find a tool" onInput={(event) => setToolQuery(event.currentTarget.value)} />
              <div class="selection-list">
                <For each={visibleTools()} fallback={<p class="selection-empty">No matching tools.</p>}>{(id) => (
                  <SelectionRow
                    label={id}
                    selected={selectedTools().has(id)}
                    onSelected={(selected) => { setMembership(setSelectedTools, id, selected); setSaved(false); }}
                  />
                )}</For>
              </div>
            </SettingsSection>

            <SettingsSection title="Skills" description="Choose discoverable procedures and pin the ones this agent should see first.">
              <TextField label="Filter skills" value={skillQuery()} placeholder="Find a skill" onInput={(event) => setSkillQuery(event.currentTarget.value)} />
              <div class="selection-list">
                <For each={visibleSkills()} fallback={<p class="selection-empty">No matching skills.</p>}>{(skill) => (
                  <SelectionRow
                    label={skill.name}
                    description={`${skill.slug} · ${skill.description}`}
                    selected={selectedSkills().has(skill.slug)}
                    pinned={pinnedSkills().has(skill.slug)}
                    pinAvailable
                    onSelected={(selected) => { setMembership(setSelectedSkills, skill.slug, selected); setSaved(false); }}
                    onPinned={(pinned) => { setMembership(setPinnedSkills, skill.slug, pinned); setSaved(false); }}
                  />
                )}</For>
              </div>
            </SettingsSection>

            <SettingsSection
              title="Appearance"
              description="Shape, face plate, eyes, and color are portable with the agent capsule."
              action={<Button onClick={() => setEditingAvatar(true)}>Edit appearance</Button>}
            >
              <div class="agent-appearance-summary">
                <AgentAvatar config={avatar()} name={current.name} size={76} />
                <div><strong>{avatar().shape}</strong><span>{avatar().eyes} eyes · {avatar().face} face · {avatar().color.toUpperCase()}</span></div>
              </div>
            </SettingsSection>
          </div>

          <Show when={editingAvatar()}>
            <AgentAvatarEditor
              agentName={current.name}
              initial={avatar()}
              saving={saving()}
              error={saveError()}
              onSave={(next) => void saveAvatar(next)}
              onClose={() => { if (!saving()) { setEditingAvatar(false); setSaveError(""); } }}
            />
          </Show>
        </>;
      }}</Show>
    </section>
  );
}
