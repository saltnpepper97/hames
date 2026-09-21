import { cleanup, fireEvent, render, screen, waitFor, within } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { AgentDetail, AgentPublic, MemoryRecord, PluginInspectView, PluginView, Scar, ScarInspection, SessionUsage, SkillCatalogEntry, SkillVersion } from "./api/types";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly listeners = new Map<string, EventListener[]>();
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  close(): void {}
}

const bootstrap = {
  protocol_version: 1,
  gateway_protocol_version: 39,
  working_directory: "/work/hames",
  csrf_token: "csrf",
};

const workspaces = [{
  id: "workspace-hames",
  path: "/work/hames",
  title: "hames",
  created_at: "2026-09-01T12:00:00Z",
  updated_at: "2026-09-04T12:00:00Z",
  available: true,
}, {
  id: "workspace-elsewhere",
  path: "/work/elsewhere",
  title: "elsewhere",
  created_at: "2026-09-01T11:00:00Z",
  updated_at: "2026-09-03T12:00:00Z",
  available: true,
}];

const health = {
  status: "ok",
  version: "0.0.0",
  protocol_version: 39,
  database_ready: true,
  provider_profiles: ["codex"],
  default_provider: "codex",
  active_runs: 1,
  active_terminals: 2,
  mcp_servers: 1,
  mcp_ready: 1,
  mcp_degraded: 0,
};

const sessionUsage: SessionUsage = {
  estimated_input_tokens: 30_000,
  input_tokens: 24_000,
  output_tokens: 6_000,
  cached_input_tokens: 12_000,
  reasoning_tokens: 2_500,
  provider_reported_cost: 0.12,
  model_requests: 8,
  latest_context: {
    provider: "codex",
    model: "gpt-5.6-sol",
    agent_id: "default",
    estimated_input_tokens: 30_000,
    context_window_tokens: 120_000,
    input_budget_tokens: 112_000,
    output_reserve_tokens: 8_000,
    context_window_source: "catalog",
  },
  account_rate_limits: {
    plan_type: "plus",
    sliding_window_5h: {
      used: 25,
      remaining: 75,
      reset_at: "2099-01-01T01:00:00Z",
      window_minutes: 300,
    },
    weekly_window: {
      used: 60,
      remaining: 40,
      reset_at: "2099-01-07T00:00:00Z",
      window_minutes: 10_080,
    },
  },
  account_rate_limits_error: "",
  daily_activity: [{
    date: "2026-09-02",
    input_tokens: 24_000,
    output_tokens: 6_000,
    cached_input_tokens: 12_000,
    reasoning_tokens: 2_500,
    provider_reported_cost: 0.12,
    model_requests: 8,
  }],
};

const pooledUsage: SessionUsage = {
  ...sessionUsage,
  grok_account_configured: true,
  grok_account_usage: {
    label: "Weekly limit",
    observed_at: 1,
    window: { used: 88, remaining: 12, reset_at: "2026-09-20T21:00:00Z", window_minutes: null },
  },
  estimated_input_tokens: 48_000,
  input_tokens: 36_000,
  output_tokens: 12_000,
  latest_context: null,
  daily_activity: [{
    date: "2026-09-02",
    input_tokens: 36_000,
    output_tokens: 12_000,
    cached_input_tokens: 18_000,
    reasoning_tokens: 4_000,
    provider_reported_cost: 0.2,
    model_requests: 13,
  }],
};

const contextInspection = {
  event_id: "event-3",
  session_id: "session-current",
  run_id: "run-one",
  manifest: {
    provider: "codex",
    model: "gpt-5.6-sol",
    agent_id: "default",
    estimated_input_tokens: 30_000,
    context_window_tokens: 120_000,
    input_budget_tokens: 112_000,
    output_reserve_tokens: 8_000,
    context_window_source: "catalog",
    compiler_version: 1,
    estimator_version: "fixture",
    reasoning_effort: "high",
    selected_sources: [],
    omitted_sources: [],
    source_order: ["agent:default"],
    contributing_event_ids: [],
    request_hash: "request-context",
    request_snapshot_blob_hash: "snapshot-context",
    agent_capsule_hash: "agent-context",
    agent_capsule_path: "/home/.hames/agents/default/AGENT.md",
    agent_origin: "user",
  },
  request_snapshot: {
    system: "You are Hames.\n\nInjected project guidance.",
    tools: [{ name: "shell" }],
    messages: [{ role: "user", content: "Hello" }],
  },
};

const sessions = [
  {
    id: "session-current",
    created_at: "2026-09-02T18:00:00Z",
    status: "open",
    title: "Build the web foundation",
    working_directory: "/work/hames",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
    pinned: false,
  },
  {
    id: "session-other",
    created_at: "2026-09-01T18:00:00Z",
    status: "open",
    title: "Another project",
    working_directory: "/work/elsewhere",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
    pinned: false,
  },
  {
    id: "session-closed",
    created_at: "2026-08-31T18:00:00Z",
    status: "closed",
    title: "Closed workspace chat",
    working_directory: "/work/hames",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
    pinned: false,
  },
];

const createdSession = {
  ...sessions[0],
  id: "session-new",
  created_at: "2026-09-02T19:00:00Z",
  title: null,
};

const agents: AgentPublic[] = [
  {
    id: "default",
    name: "Hames",
    authority: "standard",
    path: "/home/.hames/agents/default/AGENT.md",
    content_hash: "agent-hash-one",
    avatar: null,
  },
  {
    id: "reviewer",
    name: "Reviewer",
    authority: "read_only",
    path: "/home/.hames/agents/reviewer/AGENT.md",
    content_hash: "agent-hash-two",
    avatar: { shape: "cloud", eyes: "visor", face: "solid", color: "#0d9488" },
  },
];

const defaultAgentDetail: AgentDetail = {
  ...agents[0]!,
  source: "---\nid: default\nname: Hames\n---\nDefault agent.",
  instructions: "Default agent.",
  tools_allow: [],
  tools_deny: [],
  skills_allow: [],
  skills_deny: [],
  skills_pin: [],
  delegation_allowed: false,
  delegation_targets: [],
  deprecated_fields: [],
};

const reviewerAgentDetail: AgentDetail = {
  ...defaultAgentDetail,
  ...agents[1]!,
  source: "---\nid: reviewer\nname: Reviewer\n---\nReview work carefully.",
  instructions: "Review work carefully.",
};

const memories: MemoryRecord[] = [
  {
    id: "memory-relationship",
    layer: "relationship",
    status: "active",
    visibility: "global",
    subject: "user:local",
    predicate: "prefers_documentation_style",
    value: "Concise but complete",
    summary: "The user prefers concise, complete documentation.",
    confidence: 0.95,
    importance: 0.9,
    owner_agent_id: null,
    workspace_path: null,
    lineage_root_session_id: null,
    source_session_id: "session-current",
    source_run_id: "run-one",
    origin_kind: "explicit",
    valid_from: null,
    valid_until: null,
    superseded_by_id: null,
    created_at: "2026-09-01T18:00:00Z",
    updated_at: "2026-09-01T18:00:00Z",
    anchors: [{ kind: "workspace", value: "/work/hames" }],
    provenance_event_ids: ["event-one"],
  },
  {
    id: "memory-semantic",
    layer: "semantic",
    status: "active",
    visibility: "workspace",
    subject: "project:hames",
    predicate: "uses_web_runtime",
    value: { framework: "SolidJS", host: "gateway" },
    summary: "Hames Web is served by the persistent gateway.",
    confidence: 1,
    importance: 0.8,
    owner_agent_id: null,
    workspace_path: "/work/hames",
    lineage_root_session_id: null,
    source_session_id: "session-current",
    source_run_id: "run-one",
    origin_kind: "automatic",
    valid_from: null,
    valid_until: null,
    superseded_by_id: null,
    created_at: "2026-09-02T18:00:00Z",
    updated_at: "2026-09-02T18:00:00Z",
    anchors: [],
    provenance_event_ids: ["event-two"],
  },
  {
    id: "memory-episode",
    layer: "episodic",
    status: "active",
    visibility: "workspace",
    subject: "run:one",
    predicate: "completed",
    value: "Built the web foundation",
    summary: "The first web foundation was completed.",
    confidence: 1,
    importance: 0.7,
    owner_agent_id: null,
    workspace_path: "/work/hames",
    lineage_root_session_id: null,
    source_session_id: "session-current",
    source_run_id: "run-one",
    origin_kind: "episode",
    valid_from: null,
    valid_until: null,
    superseded_by_id: null,
    created_at: "2026-09-02T19:00:00Z",
    updated_at: "2026-09-02T19:00:00Z",
    anchors: [],
    provenance_event_ids: [],
  },
];

const createdMemory: MemoryRecord = {
  ...memories[0]!,
  id: "memory-created",
  layer: "episodic",
  visibility: "session_team",
  subject: "release:tonight",
  predicate: "completed_step",
  value: "Added memory management",
  summary: "Memory management was added from the web UI.",
};

const scars: Scar[] = [
  {
    id: "scar-one",
    title: "Read the milestone source before reporting status",
    scope: "workspace",
    status: "guarded",
    severity: "high",
    failure_signature: "correction:milestone-source",
    description: "The assistant reported a milestone from memory instead of reading the project source.",
    trigger: {
      workspace_paths: ["/work/hames"],
      agent_ids: ["default"],
      intent_labels: ["project status"],
      entity_ids: [],
      tool_error_signatures: [],
      skill_ids: [],
      context_signatures: [],
    },
    expected_behavior: "Read the milestone document before stating the current project status.",
    detection: "explicit_correction",
    owner_agent_id: "default",
    workspace_path: "/work/hames",
    source_session_id: "session-current",
    source_run_id: "run-one",
    repair_layer: "semantic_memory",
    repair_reference: "memory-one",
    last_triggered_at: "2026-09-02T18:05:00Z",
    successful_guard_count: 2,
    regression_count: 0,
    dismissed_reason: null,
    created_at: "2026-09-02T18:05:00Z",
    updated_at: "2026-09-02T18:06:00Z",
    evidence_event_ids: ["event-correction"],
  },
];

const createdScar: Scar = {
  ...scars[0]!,
  id: "scar-created",
  title: "Verify the rendered result before reporting success",
  scope: "agent",
  status: "open",
  severity: "medium",
  failure_signature: "reports success without visual verification",
  description: "The assistant reported that the interface was fixed without checking it.",
  expected_behavior: "Inspect the rendered interface before reporting completion.",
  detection: "manual",
  repair_layer: null,
  repair_reference: null,
  successful_guard_count: 0,
  evidence_event_ids: [],
};

const scarInspection: ScarInspection = {
  scar_id: "scar-one",
  session_id: "session-current",
  title: scars[0]!.title,
  scope: "workspace",
  status: "guarded",
  severity: "high",
  detection: "explicit_correction",
  failure_signature: scars[0]!.failure_signature,
  description: scars[0]!.description,
  expected_behavior: scars[0]!.expected_behavior,
  trigger: scars[0]!.trigger,
  repair_layer: "semantic_memory",
  repair_reference: "memory-one",
  successful_guard_count: 2,
  regression_count: 0,
  created_at: "2026-09-02T18:05:00Z",
  updated_at: "2026-09-02T18:06:00Z",
  evidence_timeline: [{
    sequence: 31,
    event_id: "event-correction",
    session_id: "session-current",
    run_id: "run-one",
    created_at: "2026-09-02T18:05:00Z",
    event_type: "user.message",
    channel: "user",
    summary: "The milestone source was corrected",
    payload: { content: "Read docs/plan.md before answering." },
  }],
  transitions: [
    {
      event_id: "event-scar-recorded",
      event_type: "scar.recorded",
      previous_status: null,
      status: "candidate",
      reason: "",
      created_at: "2026-09-02T18:05:00Z",
    },
    {
      event_id: "event-scar-guarded",
      event_type: "scar.guarded",
      previous_status: "repair_proposed",
      status: "guarded",
      reason: "Memory repair promoted",
      created_at: "2026-09-02T18:06:00Z",
    },
  ],
  repairs: [{
    id: "repair-one",
    version: 1,
    repair_layer: "semantic_memory",
    risk: "low",
    required_authority: "memory_write",
    status: "promoted",
    previous_scar_status: "open",
    rationale: "The correction identifies a stable project fact.",
    proposal: { kind: "memory_record", summary: "Read the milestone source" },
    created_by: "automatic",
    created_at: "2026-09-02T18:05:30Z",
    decided_at: "2026-09-02T18:06:00Z",
  }],
  evaluations: [{
    event_id: "event-evaluation",
    repair_id: "repair-one",
    kind: "deterministic",
    status: "passed",
    score: 1,
    report: { evidence_available: true },
    created_at: "2026-09-02T18:05:45Z",
  }],
  explanation: "The user explicitly corrected Hames; the user's statement is the authoritative diagnosis.",
};

const createdScarInspection: ScarInspection = {
  ...scarInspection,
  scar_id: createdScar.id,
  title: createdScar.title,
  scope: createdScar.scope,
  status: createdScar.status,
  severity: createdScar.severity,
  detection: createdScar.detection,
  failure_signature: createdScar.failure_signature,
  description: createdScar.description,
  expected_behavior: createdScar.expected_behavior,
  trigger: createdScar.trigger,
  repair_layer: null,
  repair_reference: null,
  successful_guard_count: 0,
  evidence_timeline: [],
  transitions: [],
  repairs: [],
  evaluations: [],
  explanation: "Recorded manually from the Hames web interface.",
};

const skills: SkillCatalogEntry[] = [
  {
    id: "skill-managed",
    slug: "review-patterns",
    version_id: "skill-managed-v2",
    version: 2,
    name: "Review Patterns",
    description: "Review recurring changes using evidence from completed Hames runs.",
    scope: "workspace",
    scope_key: "/work/hames",
    status: "active",
    content_hash: "managed-hash",
    triggers: ["review a change"],
    tools: ["read_file"],
    scripts: [],
    score: 0,
    pinned: false,
    invocation: "model",
    argument_hint: "",
    source: "managed",
    archived: false,
  },
  {
    id: "external:workspace:project-checks:path-hash",
    slug: "project-checks",
    version_id: "external-project-checks-v1",
    version: 1,
    name: "Project Checks",
    description: "Run this repository's required checks.",
    scope: "workspace",
    scope_key: "/work/hames",
    status: "active",
    content_hash: "portable-hash",
    triggers: ["verify the project"],
    tools: ["shell"],
    scripts: [],
    score: 0,
    pinned: false,
    invocation: "both",
    argument_hint: "[target]",
    source: "portable",
    archived: false,
  },
  {
    id: "builtin:visual-verification",
    slug: "visual-verification",
    version_id: "builtin-visual-verification-v1",
    version: 1,
    name: "Visual Verification",
    description: "Verify rendered behavior instead of relying only on code inspection.",
    scope: "global",
    scope_key: null,
    status: "active",
    content_hash: "builtin-hash",
    triggers: ["visual change"],
    tools: [],
    scripts: [],
    score: 0,
    pinned: false,
    invocation: "model",
    argument_hint: "",
    source: "builtin",
    archived: false,
  },
];

const skillDetails: Record<string, SkillVersion> = Object.fromEntries(skills.map((skill) => [
  skill.slug,
  {
    id: skill.version_id,
    skill_id: skill.id,
    slug: skill.slug,
    version: skill.version,
    content_hash: skill.content_hash,
    status: skill.status,
    scope: skill.scope,
    scope_key: skill.scope_key,
    name: skill.name,
    description: skill.description,
    instructions: skill.slug === "project-checks"
      ? "## Run checks\n\nUse the repository scripts and report exact failures."
      : "Read the relevant evidence, perform the procedure, and report the verified result.",
    metadata: {
      id: skill.slug,
      name: skill.name,
      description: skill.description,
      version: skill.version,
      scope: skill.scope,
      tools: skill.tools,
      triggers: skill.triggers,
      requires: skill.slug === "project-checks" ? ["repository checkout"] : [],
      scripts: skill.scripts,
      invocation: skill.invocation,
      argument_hint: skill.argument_hint,
    },
    package_path: skill.source === "portable"
      ? `/work/hames/.agents/skills/${skill.slug}`
      : skill.source === "managed"
      ? `/home/.hames/skills/packages/${skill.slug}`
      : `/app/builtin_skills/${skill.slug}`,
    base_version_id: null,
    created_by: skill.source === "portable" ? "external" : skill.source,
    source_session_id: skill.source === "managed" ? "session-current" : skill.source,
    source_run_id: skill.source === "managed" ? "run-one" : null,
    created_at: "2026-09-01T18:00:00Z",
    activated_at: "2026-09-01T19:00:00Z",
    last_used_at: null,
    pinned: skill.pinned,
  },
]));

const installedPlugin: PluginView = {
  id: "event-relay",
  name: "Event Relay",
  enabled: false,
  running: false,
  version: "0.3.0",
  fingerprint: "84a0c50f469744252766aadd35bccd65c0cd1f5956acc530f511818df89634de",
  capabilities: ["event"],
  permissions: ["broker:network_request"],
  entrypoint: "worker.py",
  package_path: "/home/.hames/plugins/installed/event-relay/0.3.0-84a0c50f4697",
  tools: [],
  warning: "",
};

const inspectedPlugin: PluginInspectView = {
  id: "project-stats",
  name: "Project Stats",
  version: "0.1.0",
  fingerprint: "f4750d31e193ec6a8f5dbc0289172903b947cc590d0a29d8768316e97d452ef7",
  permissions: ["broker:project_read"],
  capabilities: ["tool", "context"],
  entrypoint: "worker.py",
  files: ["README.md", "plugin.toml", "worker.py"],
};

const addedPlugin: PluginView = {
  ...inspectedPlugin,
  enabled: false,
  running: false,
  package_path: "/home/.hames/plugins/installed/project-stats/0.1.0-f4750d31e193",
  tools: [],
  warning: "",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function successfulFetch(options: { plugins?: PluginView[]; workspaces?: typeof workspaces; emptyWorkspace?: boolean } = {}) {
  let currentSession = { ...sessions[0] };
  let forkedSession: typeof currentSession | undefined;
  let currentAgent = { ...defaultAgentDetail };
  let currentAgents = agents.map((agent) => ({ ...agent }));
  let currentWorkspaces = (options.workspaces ?? workspaces).map((workspace) => ({ ...workspace }));
  let createdSessionCount = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/_hames/v1/bootstrap") return jsonResponse(bootstrap);
    if (path === "/v1/health") return jsonResponse(health);
    if (path === "/v1/workspaces" && !init?.method) return jsonResponse(currentWorkspaces);
    if (path === "/v1/workspaces" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as { path: string; title: string | null };
      const created = {
        id: "workspace-added",
        path: request.path,
        title: request.title ?? request.path.split("/").at(-1)!,
        created_at: "2026-09-07T14:00:00Z",
        updated_at: "2026-09-07T14:00:00Z",
        available: true,
      };
      currentWorkspaces = [created, ...currentWorkspaces];
      return jsonResponse(created, 201);
    }
    if (path.startsWith("/v1/workspaces/") && init?.method === "PATCH") {
      const id = decodeURIComponent(path.split("/").at(-1) ?? "");
      const update = JSON.parse(String(init.body)) as { title: string };
      const current = currentWorkspaces.find((workspace) => workspace.id === id);
      if (!current) return jsonResponse({ error: { message: "not found" } }, 404);
      const renamed = { ...current, title: update.title };
      currentWorkspaces = currentWorkspaces.map((workspace) =>
        workspace.id === id ? renamed : workspace
      );
      return jsonResponse(renamed);
    }
    if (path.startsWith("/v1/workspaces/") && init?.method === "DELETE") {
      const id = decodeURIComponent(path.split("/").at(-1) ?? "");
      currentWorkspaces = currentWorkspaces.filter((workspace) => workspace.id !== id);
      return jsonResponse({ deleted: true });
    }
    if (path.startsWith("/v1/directories?") && !init?.method) {
      const requestedPath = new URLSearchParams(path.split("?")[1]).get("path");
      return jsonResponse({
        path: requestedPath ?? "/work/hames",
        parent: "/work",
        directories: [{ name: "src", path: `${requestedPath ?? "/work/hames"}/src` }],
      });
    }
    if (path === "/v1/directories/select" && init?.method === "POST") {
      return jsonResponse({ path: "/work/hames/scratch" });
    }
    if (path === "/v1/directories/pick" && init?.method === "POST") {
      const created = {
        id: "workspace-scratch",
        path: "/work/hames/scratch",
        title: "scratch",
        created_at: "2026-09-04T14:00:00Z",
        updated_at: "2026-09-04T14:00:00Z",
        available: true,
      };
      currentWorkspaces = [created, ...currentWorkspaces];
      return jsonResponse(created);
    }
    if (path === "/v1/directories" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as { parent: string; name: string };
      const created = {
        id: `workspace-${request.name}`,
        path: `${request.parent}/${request.name}`,
        title: request.name,
        created_at: "2026-09-04T14:00:00Z",
        updated_at: "2026-09-04T14:00:00Z",
        available: true,
      };
      currentWorkspaces = [created, ...currentWorkspaces];
      return jsonResponse(created, 201);
    }
    if (path === "/v1/sessions?has_messages=true&include_titled=true&registered_workspaces_only=true&include_delegated=true") {
      return jsonResponse([
        ...(!options.emptyWorkspace ? [currentSession] : []),
        ...(forkedSession ? [forkedSession] : []),
        ...sessions.slice(1),
      ]);
    }
    if (path === "/v1/sessions?has_messages=true&working_directory=%2Fwork%2Fhames") {
      return jsonResponse([currentSession, ...(forkedSession ? [forkedSession] : []), ...sessions.slice(1)]);
    }
    if (path === "/v1/sessions?has_messages=true&working_directory=%2Fwork%2Felsewhere") {
      return jsonResponse([sessions[1]]);
    }
    if (path === "/v1/sessions?has_messages=true&working_directory=%2Fwork%2Fhames%2Fscratch") {
      return jsonResponse([]);
    }
    if (path === "/v1/sessions/session-current/usage") return jsonResponse(sessionUsage);
    if (path === "/v1/usage") return jsonResponse(pooledUsage);
    if (path === "/v1/contexts/event-3") return jsonResponse(contextInspection);
    if (path === "/v1/agents" && !init?.method) return jsonResponse(currentAgents);
    if (path === "/v1/agents" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as {
        name: string;
        authority: "standard" | "read_only";
        source: string;
      };
      const id = /\"id\":\s*\"([^\"]+)\"/.exec(request.source)?.[1] ?? "agent-1234567890abcdef1234567890abcdef";
      const created: AgentDetail = {
        id,
        name: request.name,
        authority: request.authority,
        path: `/home/.hames/agents/${id}/AGENT.md`,
        content_hash: `agent-hash-${id}`,
        avatar: null,
        source: request.source,
        instructions: request.source.split("---").at(-1)?.trim() ?? "",
        tools_allow: [],
        tools_deny: [],
        skills_allow: [],
        skills_deny: [],
        skills_pin: [],
        delegation_allowed: false,
        delegation_targets: [],
        deprecated_fields: [],
      };
      currentAgents = [...currentAgents, created];
      return jsonResponse(created, 201);
    }
    if (path === "/v1/plugins" && !init?.method) return jsonResponse(options.plugins ?? [installedPlugin]);
    if (path === "/v1/plugins/inspect" && init?.method === "POST") {
      return jsonResponse(inspectedPlugin);
    }
    if (path === "/v1/plugins/uploads" && init?.method === "POST") {
      return jsonResponse({ upload_id: "upload-one", plugin: inspectedPlugin }, 201);
    }
    if (path === "/v1/plugins/uploads/upload-one/install" && init?.method === "POST") {
      return jsonResponse(addedPlugin, 201);
    }
    if (path === "/v1/plugins/uploads/upload-one" && init?.method === "DELETE") {
      return jsonResponse({ discarded: true });
    }
    if (path === "/v1/plugins/install" && init?.method === "POST") {
      return jsonResponse(addedPlugin, 201);
    }
    if (path === "/v1/plugins/event-relay/enable" && init?.method === "POST") {
      return jsonResponse({ ...installedPlugin, enabled: true, running: true });
    }
    if (path === "/v1/plugins/event-relay/disable" && init?.method === "POST") {
      return jsonResponse({ ...installedPlugin, enabled: false, running: false });
    }
    if (path === "/v1/plugins/event-relay" && init?.method === "DELETE") {
      return jsonResponse({ removed: true });
    }
    if (path === "/v1/agents/default" && !init?.method) return jsonResponse(currentAgent);
    if (path === "/v1/agents/reviewer" && !init?.method) return jsonResponse(reviewerAgentDetail);
    if (path === "/v1/agents/reviewer" && init?.method === "DELETE") {
      currentAgents = currentAgents.filter((agent) => agent.id !== "reviewer");
      return jsonResponse({ retired_to: "/home/.hames/agents/retired/reviewer" });
    }
    if (path === "/v1/agents/default/capabilities?working_directory=%2Fwork%2Fhames") {
      return jsonResponse({
        tools: ["read_file", "shell", "write_file"],
        skills: [{
          slug: "testing",
          name: "Testing",
          description: "Run the project checks",
          scope: "global",
        }],
      });
    }
    if (path === "/v1/agents/reviewer/capabilities?working_directory=%2Fwork%2Fhames") {
      return jsonResponse({ tools: ["read_file"], skills: [] });
    }
    if (path === "/v1/agents/default" && init?.method === "PATCH") {
      const update = JSON.parse(String(init.body));
      currentAgent = {
        ...currentAgent,
        name: update.name ?? currentAgent.name,
        instructions: update.instructions ?? currentAgent.instructions,
        avatar: update.avatar ?? currentAgent.avatar,
        tools_allow: update.tools?.allow ?? currentAgent.tools_allow,
        tools_deny: update.tools?.deny ?? currentAgent.tools_deny,
        skills_allow: update.skills?.allow ?? currentAgent.skills_allow,
        skills_deny: update.skills?.deny ?? currentAgent.skills_deny,
        skills_pin: update.skills?.pin ?? currentAgent.skills_pin,
      };
      return jsonResponse(currentAgent);
    }
    if (path.startsWith("/v1/sessions/session-current/memories?")) {
      const parameters = new URLSearchParams(path.split("?")[1]);
      const layer = parameters.get("layer");
      const offset = Number(parameters.get("offset") ?? 0);
      const limit = Number(parameters.get("limit") ?? 200);
      return jsonResponse(memories.filter((memory) => memory.layer === layer).slice(offset, offset + limit));
    }
    if (path === "/v1/sessions/session-current/memories" && init?.method === "POST") {
      return jsonResponse(createdMemory, 201);
    }
    if (path === "/v1/sessions/session-current/memories/memory-relationship" && init?.method === "DELETE") {
      return jsonResponse({ memory_id: "memory-relationship", deleted: true });
    }
    if (path === "/v1/sessions/session-current/scars?limit=200") {
      return jsonResponse(scars);
    }
    if (path === "/v1/sessions/session-current/scars" && init?.method === "POST") {
      return jsonResponse(createdScar, 201);
    }
    if (path === "/v1/sessions/session-current/scars/scar-one/inspection") {
      return jsonResponse(scarInspection);
    }
    if (path === "/v1/sessions/session-current/scars/scar-created/inspection") {
      return jsonResponse(createdScarInspection);
    }
    if (path === "/v1/sessions/session-current/scars/scar-one" && init?.method === "DELETE") {
      return jsonResponse({ scar_id: "scar-one", deleted: true });
    }
    if (/^\/v1\/sessions\/[^/]+\/queue$/.test(path)) {
      return jsonResponse({ session_id: path.split("/")[3], paused: false, items: [] });
    }
    if (path === "/v1/sessions/session-current/skills/available") {
      return jsonResponse(skills);
    }
    if (path === "/v1/sessions/session-current/skills/review-patterns" && init?.method === "DELETE") {
      return jsonResponse({ skill_id: "skill-managed", slug: "review-patterns", deleted: true });
    }
    if (path === "/v1/sessions/session-current/skills/author" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as { goal: string; scope: "workspace" | "agent" };
      return jsonResponse({
        id: "skill-job-one",
        kind: "author",
        status: "pending",
        session_id: "session-current",
        run_id: null,
        source_event_id: "event-skill-authoring",
        target_skill_id: null,
        goal: request.goal,
        scope: request.scope,
        attempts: 0,
        error_code: null,
        error_message: null,
        created_at: "2026-09-03T16:00:00Z",
        updated_at: "2026-09-03T16:00:00Z",
      }, 202);
    }
    if (path.startsWith("/v1/sessions/session-current/skills/available/")) {
      const slug = decodeURIComponent(path.split("/").at(-1) ?? "");
      return skillDetails[slug]
        ? jsonResponse(skillDetails[slug])
        : jsonResponse({ error: { message: "not found" } }, 404);
    }
    if (path === "/v1/connections") return jsonResponse([]);
    if (path === "/v1/providers") {
      return jsonResponse([
        {
          id: "codex",
          adapter: "codex",
          endpoint: "local",
          configured_model: "gpt-5.6-sol",
          default_reasoning_effort: "high",
          supported_reasoning_efforts: ["low", "medium", "high", "xhigh"],
        },
        {
          id: "ollama",
          adapter: "ollama",
          endpoint: "http://127.0.0.1:11434",
          configured_model: "qwen3:8b",
          default_reasoning_effort: "medium",
          supported_reasoning_efforts: ["low", "medium", "high"],
        },
        {
          id: "ollama-idle",
          adapter: "ollama",
          endpoint: "http://127.0.0.1:11435",
          configured_model: "",
          default_reasoning_effort: "",
          supported_reasoning_efforts: [],
        },
      ]);
    }
    if (path === "/v1/providers/codex/probe" && init?.method === "POST") {
      return jsonResponse({
        id: "codex",
        adapter: "codex",
        reachable: true,
        models: [{
          id: "gpt-5.6-sol",
          status: "available",
          context_length: 272000,
          parameter_size: null,
          quantization: null,
          input_modalities: ["text", "image"],
          output_modalities: ["text"],
          reasoning_supported: true,
          reasoning_efforts: ["low", "medium", "high", "xhigh"],
        }],
        error: null,
      });
    }
    if (path === "/v1/providers/ollama/probe" && init?.method === "POST") {
      return jsonResponse({
        id: "ollama",
        adapter: "ollama",
        reachable: true,
        models: [{
          id: "qwen3:8b",
          status: "available",
          context_length: 32768,
          parameter_size: "8B",
          quantization: "Q4_K_M",
          input_modalities: ["text"],
          output_modalities: ["text"],
          reasoning_supported: true,
          reasoning_efforts: ["low", "medium", "high"],
        }],
        error: null,
      });
    }
    if (path === "/v1/sessions" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as { working_directory: string };
      createdSessionCount += 1;
      return jsonResponse({
        ...createdSession,
        id: createdSessionCount === 1 ? "session-new" : `session-new-${createdSessionCount}`,
        working_directory: request.working_directory,
      }, 201);
    }
    if (path === "/v1/sessions/session-new" && !init?.method) {
      return jsonResponse(createdSession);
    }
    if (/^\/v1\/sessions\/(?:session-current|session-new(?:-\d+)?)\/messages$/.test(path) && init?.method === "POST") {
      return jsonResponse(
        {
          submission_id: "submission-one",
          replayed: false,
          disposition: "started",
          run_id: "run-one",
          queued: null,
        },
        202,
      );
    }
    if (/^\/v1\/sessions\/(?:session-current|session-new(?:-\d+)?)\/title$/.test(path) && init?.method === "PUT") {
      const request = JSON.parse(String(init.body)) as { title: string };
      const id = path.split("/").at(-2) ?? "session-new";
      if (id === "session-current") currentSession = { ...currentSession, title: request.title };
      return jsonResponse({
        ...(id === "session-current" ? currentSession : createdSession),
        id,
        title: request.title,
      });
    }
    if (path === "/v1/sessions/session-current/attachments/notes-digest" && !init?.method) {
      return new Response("# Attached notes\n\nRendered from the durable attachment.", {
        status: 200,
        headers: { "Content-Type": "text/markdown" },
      });
    }
    if (path === "/v1/sessions/session-current/dream" && init?.method === "POST") {
      return jsonResponse({ dream_id: "dream-requested" }, 202);
    }
    if (path === "/v1/sessions/session-current/compact" && init?.method === "POST") {
      return jsonResponse({ run_id: "compact-one", trigger: "manual" }, 202);
    }
    if (path === "/v1/sessions/session-current/fork" && init?.method === "POST") {
      forkedSession = {
        ...currentSession,
        id: "session-fork",
        created_at: "2026-09-04T20:00:00Z",
        title: "Forked conversation",
      };
      return jsonResponse(forkedSession, 201);
    }
    if (path === "/v1/sessions/session-fork" && !init?.method && forkedSession) {
      return jsonResponse(forkedSession);
    }
    if (path === "/v1/sessions/session-current/goals/current" && !init?.method) {
      return jsonResponse(null);
    }
    if (path === "/v1/sessions/session-current/goals" && init?.method === "POST") {
      const request = JSON.parse(String(init.body)) as { objective: string };
      return jsonResponse({
        id: "goal-one",
        session_id: "session-current",
        objective: request.objective,
        status: "running",
        step_count: 0,
        current_run_id: null,
        latest_summary: "",
        latest_evidence: [],
        latest_signature: "",
        repeated_no_progress: 0,
        active_seconds: 0,
        active_since: null,
        created_at: "2026-09-04T20:00:00Z",
        updated_at: "2026-09-04T20:00:00Z",
      }, 202);
    }
    if (path === "/v1/sessions/session-current/terminals" && init?.method === "DELETE") {
      return jsonResponse({ closed: 2 });
    }
    if (path === "/v1/sessions/session-current/mode" && init?.method === "PUT") {
      currentSession = { ...currentSession, interaction_mode: "plan" };
      return jsonResponse(currentSession);
    }
    if (path.endsWith("/agent") && init?.method === "PUT") {
      const selection = JSON.parse(String(init.body)) as { agent_id: string };
      const sessionId = decodeURIComponent(path.split("/")[3] ?? "");
      if (sessionId === createdSession.id) {
        return jsonResponse({ ...createdSession, agent_id: selection.agent_id });
      }
      currentSession = { ...currentSession, agent_id: selection.agent_id };
      return jsonResponse(currentSession);
    }
    if (path === "/v1/sessions/session-current/pinned" && init?.method === "PUT") {
      const update = JSON.parse(String(init.body)) as { pinned: boolean };
      currentSession = { ...currentSession, pinned: update.pinned };
      return jsonResponse(currentSession);
    }
    if (path === "/v1/sessions/session-current" && init?.method === "DELETE") {
      currentSession = { ...currentSession, status: "closed" };
      return jsonResponse(currentSession);
    }
    if (path === "/v1/sessions/session-current" && init?.method === "PATCH") {
      const selection = JSON.parse(String(init.body)) as {
        provider: string;
        model: string;
        reasoning_effort: string;
      };
      currentSession = { ...currentSession, ...selection };
      return jsonResponse(currentSession);
    }
    if (path === "/v1/runs/run-one/cancel" && init?.method === "POST") {
      return jsonResponse({ cancelled: true });
    }
    if (path === "/v1/approvals/approval-one" && init?.method === "POST") {
      return jsonResponse({ status: "approved" });
    }
    if (path.startsWith("/v1/questions/") && init?.method === "POST") {
      return jsonResponse({ answer: "Proceed" });
    }
    return jsonResponse({ error: { message: "not found" } }, 404);
  });
}

function durableEvent(
  type: string,
  sequence: number,
  payload: Record<string, unknown>,
  runId: string | null = "run-one",
  correlationId: string | null = null,
  sessionId = "session-current",
) {
  return {
    durable: true,
    event: {
      id: `event-${sequence}`,
      sequence,
      session_id: sessionId,
      run_id: runId,
      agent_id: "default",
      type,
      schema_version: 1,
      created_at: "2026-09-02T18:00:00Z",
      causation_id: null,
      correlation_id: correlationId,
      payload,
      blob_hash: null,
      payload_hash: `hash-${sequence}`,
      redaction_state: "clear",
    },
  };
}

describe("Hames web shell", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/chat/session-current");
    window.sessionStorage.clear();
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders real workspace chats in the contextual sidebar", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    expect(await screen.findByRole("link", { name: /Build the web foundation/ })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Build the web foundation", level: 1 })).toBeInTheDocument();
    expect(document.querySelector(".chat-session-directory")).not.toBeInTheDocument();
    const chatSidebar = screen.getByRole("complementary", { name: "Chat sidebar" });
    expect(chatSidebar.querySelector(".context-header .eyebrow")).not.toBeInTheDocument();
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("Another project")).not.toBeInTheDocument();
    expect(screen.queryByText("Closed workspace chat")).not.toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
    const mobileHeader = document.querySelector(".mobile-header");
    expect(mobileHeader).not.toBeNull();
    expect(within(mobileHeader as HTMLElement).getByText("Connected")).toBeInTheDocument();
    expect(mobileHeader?.querySelector('[data-icon="action.menu"] svg')).toHaveClass(
      "tabler-icon-menu-2",
    );
    expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Events" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Agent: Hames" })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-icon^="nav."]')).toHaveLength(7);
    expect(document.querySelector('[data-icon="nav.flows"] svg')).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Automations" })).not.toBeInTheDocument();
    expect(document.querySelector('[data-icon="nav.scars"]')).toHaveAttribute(
      "data-icon-pack",
      "hames-default",
    );
    expect(document.querySelector('[data-icon="nav.scars"] svg')).toHaveClass(
      "tabler-icon-bandage",
    );
    expect(document.querySelector('[data-icon="nav.plugins"] svg')).toHaveClass(
      "tabler-icon-plug",
    );
    expect(document.querySelector('[data-icon="nav.agents"] .phosphor-icon')).toHaveClass(
      "phosphor-icon",
    );
    expect(document.querySelector('[data-icon="nav.memory"] .phosphor-icon')).toBeInTheDocument();
    expect(document.querySelector('[data-icon="brand.mark"] .brand-image-icon')).toBeInTheDocument();
    expect(document.querySelector(".activity-rail")).not.toBeInTheDocument();
    expect(document.querySelector(".context-sidebar")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search chats" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add workspace" })).toBeInTheDocument();
    const newChatButton = screen.getByRole("button", { name: "New chat in hames" });
    expect(newChatButton.querySelector(".tabler-icon-message-circle-plus")).toBeInTheDocument();
    expect(newChatButton.closest(".workspace-chat-group-header")).toBeInTheDocument();
    const currentChatLink = await screen.findByRole("link", { name: "Build the web foundation" });
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    expect(currentChatLink).not.toHaveTextContent("default");
    expect(currentChatLink.querySelector("time")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".conversation-date-group").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Collapse hames" }));
    expect(screen.getByRole("button", { name: "Expand hames" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("link", { name: "Build the web foundation" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand hames" }));
    expect(await screen.findByRole("link", { name: "Build the web foundation" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-collapsed");
    const revealSidebar = screen.getByRole("button", { name: "Expand sidebar" });
    expect(revealSidebar).toHaveClass("sidebar-reveal-button");
    expect(newChatButton.querySelector(".tabler-icon-message-circle-plus")).toBeInTheDocument();

    fireEvent.pointerEnter(revealSidebar);
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-collapsed");
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-peeking");
    fireEvent.pointerLeave(document.querySelector(".navigation-stack") as HTMLElement);
    expect(document.querySelector(".app-frame")).not.toHaveClass("sidebar-peeking");

    fireEvent.click(revealSidebar);
    expect(document.querySelector(".app-frame")).not.toHaveClass("sidebar-collapsed");

    fireEvent.click(screen.getByRole("link", { name: /Build the web foundation/ }));
    expect(
      await screen.findByRole("heading", { name: "Build the web foundation", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Build the web foundation" }).parentElement)
      .not.toHaveTextContent("gpt-5.6-sol");
    expect(screen.queryByRole("combobox", { name: "Current workspace" }))
      .not.toBeInTheDocument();
    const chatScroll = document.querySelector("[data-conversation-scroll]");
    expect(chatScroll).toHaveClass("transcript-scroll");
    const chatFrame = chatScroll?.closest(".session-chat");
    expect(chatFrame).toBeInTheDocument();
    expect(chatFrame?.querySelector(".composer-dock")).toBeInTheDocument();
  });

  it("starts without inheriting a launch directory when no workspace is authorized", async () => {
    const fetchMock = successfulFetch({ workspaces: [] });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
    expect(await screen.findByText("No workspaces yet.")).toBeInTheDocument();
    const freshHeading = await screen.findByRole("heading", { name: "What should we work on?" });
    expect(freshHeading.closest(".chat-view-panel")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add a workspace to begin" }))
      .not.toBeInTheDocument();
    const chooseWorkspace = screen.getByRole("button", { name: "Choose a workspace" });
    expect(chooseWorkspace).toHaveTextContent("Choose workspace");
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input) === "/v1/sessions" && init?.method === "POST"
    )).toBe(false);

    fireEvent.click(chooseWorkspace);
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    const name = within(dialog).getByRole("textbox", { name: "Workspace name" });
    expect(name).toBeRequired();
    expect(within(dialog).getByRole("button", { name: "Add workspace" })).toBeDisabled();
    fireEvent.input(name, { target: { value: "My workspace" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose folder" }));
    await within(dialog).findByText("/work/hames/scratch");
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/v1/workspaces" && init?.method === "POST")).toBe(false);
    fireEvent.submit(name.closest("form")!);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/directories/select",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ initial_path: null }),
      }),
    );
  });

  it("switches workspace context without changing the browser launch", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const newChat = await screen.findByRole("button", { name: "New chat in hames" });
    fireEvent.click(newChat);
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    await screen.findByRole("heading", { name: "What should we work on?", level: 2 });
    const picker = await screen.findByRole("combobox", { name: "Current workspace" });
    await waitFor(() => expect(picker).toHaveTextContent("hames"));
    expect(picker.querySelector('[data-icon="action.folder"]')).toBeInTheDocument();
    expect(picker.closest(".composer-shell")).not.toBeInTheDocument();
    expect(picker.closest(".composer-workspace-control")?.nextElementSibling)
      .toHaveClass("composer-trays");
    const workspaceControl = picker.closest(".composer-workspace-control") as HTMLElement;
    expect(within(workspaceControl).queryByRole("button", { name: "Add workspace" }))
      .not.toBeInTheDocument();
    expect(within(workspaceControl).queryByRole("button", { name: /Manage/ }))
      .not.toBeInTheDocument();
    fireEvent.click(picker);
    fireEvent.click(screen.getByRole("option", { name: /elsewhere/ }));

    expect(await screen.findByRole("link", { name: "Another project" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Build the web foundation" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("hames.selectedWorkspace"))
      .toBe("workspace-elsewhere");
    expect(fetchMock).toHaveBeenCalledWith("/_hames/v1/bootstrap", expect.anything());
  });

  it("adds a workspace from the composer picker when only one is registered", async () => {
    const fetchMock = successfulFetch({ workspaces: workspaces.slice(0, 1) });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("button", { name: "New chat in hames" }));
    const picker = await screen.findByRole("combobox", { name: "Current workspace" });
    await waitFor(() => expect(picker).toHaveTextContent("hames"));
    fireEvent.click(picker);
    expect(screen.getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("option", { name: "Add workspace" })
      .querySelector('[data-icon="action.add"] svg')).toHaveClass("tabler-icon-plus");
    fireEvent.click(screen.getByRole("option", { name: "Add workspace" }));
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    const name = within(dialog).getByRole("textbox", { name: "Workspace name" });
    expect(name).toBeRequired();
    expect(within(dialog).getByRole("button", { name: "Add workspace" })).toBeDisabled();
    fireEvent.input(name, { target: { value: "My workspace" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose folder" }));
    await within(dialog).findByText("/work/hames/scratch");
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/v1/workspaces" && init?.method === "POST")).toBe(false);
    fireEvent.submit(name.closest("form")!);

    await waitFor(() => expect(screen.getByRole("combobox", { name: "Current workspace" })).toHaveTextContent("My workspace"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("hames.selectedWorkspace")).toBe("workspace-added");
    expect(fetchMock).toHaveBeenCalledWith("/v1/workspaces", expect.objectContaining({
      method: "POST", body: JSON.stringify({ path: "/work/hames/scratch", title: "My workspace" }),
    }));
    expect(fetchMock).toHaveBeenCalledWith("/v1/directories/select", expect.objectContaining({
      method: "POST", body: JSON.stringify({ initial_path: "/work/hames" }),
    }));
    fireEvent.click(screen.getByRole("combobox", { name: "Current workspace" }));
    expect(screen.getByRole("option", { name: "Add workspace" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it.each(["cancel", "error"])("handles native workspace picker %s without saving a workspace", async (outcome) => {
    const base = successfulFetch({ workspaces: workspaces.slice(0, 1) });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/v1/directories/select") {
        return outcome === "cancel" ? jsonResponse(null) : new Response(JSON.stringify({
          error: { code: "native_directory_picker_failed", message: "System folder picker unavailable" },
        }), { status: 500, headers: { "Content-Type": "application/json" } });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("button", { name: "New chat in hames" }));
    const picker = await screen.findByRole("combobox", { name: "Current workspace" });
    await waitFor(() => expect(picker).toHaveTextContent("hames"));
    fireEvent.click(picker);
    fireEvent.click(screen.getByRole("option", { name: "Add workspace" }));
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose folder" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/v1/directories/select", expect.anything()));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Choose folder" })).toBeEnabled());
    expect(picker).toHaveTextContent("hames");
    expect(within(dialog).getByRole("button", { name: "Add workspace" })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/v1/workspaces" && init?.method === "POST")).toBe(false);
    if (outcome === "error") expect(await screen.findByRole("alert")).toHaveTextContent("System folder picker unavailable");
  });

  it.each(["sidebar", "dropdown"])("cancels the %s workspace form without registration", async (entry) => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("button", { name: "New chat in hames" }));
    if (entry === "sidebar") fireEvent.click(screen.getByRole("button", { name: "Add workspace" }));
    else {
      fireEvent.click(await screen.findByRole("combobox", { name: "Current workspace" }));
      fireEvent.click(screen.getByRole("option", { name: "Add workspace" }));
    }
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    fireEvent.input(within(dialog).getByRole("textbox", { name: "Workspace name" }), { target: { value: "Cancelled" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose folder" }));
    await within(dialog).findByText("/work/hames/scratch");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/v1/workspaces" && init?.method === "POST")).toBe(false);
  });

  it("searches workspace names and their chats from the sidebar header", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    fireEvent.click(await screen.findByRole("button", { name: "Search chats" }));
    const search = screen.getByRole("searchbox", { name: "Search chats" });
    expect(search.closest('[data-component="search-input"]')).toBeInTheDocument();
    expect(search.closest(".workspace-directory-header")).toHaveClass("searching");
    fireEvent.input(search, { target: { value: "another project" } });

    expect(await screen.findByRole("link", { name: "Another project" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Collapse hames/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collapse elsewhere" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    expect(screen.getByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
    expect(document.querySelector(".workspace-directory-header")).not.toHaveClass("searching");
  });

  it("uses the shared animated search header across searchable sidebars", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Agents" }));
    let sidebar = await screen.findByRole("complementary", { name: "Agents sidebar" });
    await within(sidebar).findByRole("link", { name: /Reviewer/ });
    const createAgent = screen.getByRole("button", { name: "Create Agent" });
    expect(createAgent).toHaveClass("sidebar-icon-action");
    expect(createAgent).toHaveTextContent("");
    fireEvent.click(screen.getByRole("button", { name: "Search Agents" }));
    let search = screen.getByRole("searchbox", { name: "Search Agents" });
    expect(search.closest('[data-component="search-input"]')).toBeInTheDocument();
    fireEvent.input(search, { target: { value: "reviewer" } });
    expect(within(sidebar).getByRole("link", { name: /Reviewer/ })).toBeInTheDocument();
    expect(within(sidebar).queryByRole("link", { name: /Hames/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Memory" }));
    sidebar = await screen.findByRole("complementary", { name: "Memory sidebar" });
    await within(sidebar).findByRole("link", { name: /Hames Web is served/ });
    const addMemory = await screen.findByRole("button", { name: "Add Memory" });
    expect(addMemory).toHaveClass("sidebar-icon-action");
    fireEvent.click(screen.getByRole("button", { name: "Search Memory" }));
    search = screen.getByRole("searchbox", { name: "Search Memory" });
    fireEvent.input(search, { target: { value: "persistent gateway" } });
    expect(sidebar.querySelector('a[href="/memory/memory-semantic"]')).toBeInTheDocument();
    expect(sidebar.querySelector('a[href="/memory/memory-relationship"]')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Skills" }));
    sidebar = await screen.findByRole("complementary", { name: "Skills sidebar" });
    await within(sidebar).findByRole("link", { name: /Project Checks/ });
    expect(await screen.findByRole("button", { name: "Create Skill" })).toHaveClass(
      "sidebar-icon-action",
    );
    fireEvent.click(screen.getByRole("button", { name: "Search Skills" }));
    search = screen.getByRole("searchbox", { name: "Search Skills" });
    fireEvent.input(search, { target: { value: "project checks" } });
    expect(sidebar.querySelector('a[href="/skills/project-checks"]')).toBeInTheDocument();
    expect(sidebar.querySelector('a[href="/skills/review-patterns"]')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Scars" }));
    sidebar = await screen.findByRole("complementary", { name: "Scars sidebar" });
    await within(sidebar).findByRole("link", { name: /Read the milestone source/ });
    expect(await screen.findByRole("button", { name: "Create Scar" })).toHaveClass(
      "sidebar-icon-action",
    );
    fireEvent.click(screen.getByRole("button", { name: "Search Scars" }));
    search = screen.getByRole("searchbox", { name: "Search Scars" });
    fireEvent.input(search, { target: { value: "nothing matches this" } });
    expect(within(sidebar).getByText("No matching Scars.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Plugins" }));
    sidebar = await screen.findByRole("complementary", { name: "Plugins sidebar" });
    await within(sidebar).findByRole("link", { name: /Event Relay/ });
    expect(await screen.findByRole("button", { name: "Add Plugin" })).toHaveClass(
      "sidebar-icon-action",
    );
    fireEvent.click(screen.getByRole("button", { name: "Search Plugins" }));
    search = screen.getByRole("searchbox", { name: "Search Plugins" });
    fireEvent.input(search, { target: { value: "nothing matches this" } });
    expect(within(sidebar).getByText("No matching plugins.")).toBeInTheDocument();
  });

  it("renames and unregisters a workspace without presenting filesystem deletion", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const manage = await screen.findByRole("button", { name: "Workspace actions for hames" });
    await waitFor(() => expect(manage).toBeEnabled());
    fireEvent.click(manage);
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const name = screen.getByRole("textbox", { name: "Display name" });
    fireEvent.input(name, { target: { value: "Hames core" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Collapse Hames core" }))
        .toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Hames core" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(screen.getByText("The folder, its files, and every chat transcript remain on disk."))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove workspace" }));

    expect(await screen.findByRole("link", { name: "Another project" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/workspaces/workspace-hames",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("opens the native picker and selects its folder", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const add = await screen.findByRole("button", { name: "Add workspace" });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    const name = within(dialog).getByRole("textbox", { name: "Workspace name" });
    expect(name).toBeRequired();
    expect(within(dialog).getByRole("button", { name: "Add workspace" })).toBeDisabled();
    fireEvent.input(name, { target: { value: "My workspace" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose folder" }));
    await within(dialog).findByText("/work/hames/scratch");
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/v1/workspaces" && init?.method === "POST")).toBe(false);
    fireEvent.submit(name.closest("form")!);


    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(screen.getByRole("combobox", { name: "Current workspace" }))
      .toHaveTextContent("My workspace");
    expect(await screen.findByRole("button", { name: /^(Collapse|Expand) My workspace$/ }))
      .toBeInTheDocument();
    expect(screen.getByText("No sessions yet")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/directories/select",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ initial_path: "/work/hames" }),
      }),
    );
  });

  it.each([false, true])("recovers after active-chat deletion without unnecessary creation (last visible: %s)", async (lastVisible) => {
    const fallback = successfulFetch();
    let deleted = false;
    window.sessionStorage.setItem("hames.new-chat-consumed:/work/hames", "true");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/v1/sessions/session-current" && init?.method === "DELETE") deleted = true;
      if (path.startsWith("/v1/sessions?")) {
        return Promise.resolve(jsonResponse([
          ...(!deleted ? [sessions[0]] : []),
          ...(!lastVisible ? [{ ...sessions[1], working_directory: "/work/hames" }] : []),
        ]));
      }
      return fallback(input, init);
    }));
    render(() => <App />);
    await screen.findByRole("button", { name: "Delete Build the web foundation" });
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete Build the web foundation" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(window.location.pathname).toBe(lastVisible ? "/chat/session-new" : "/chat/session-other"));
    if (lastVisible) {
      expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/chat/session-new");
    } else {
      expect(await screen.findByRole("heading", { name: "Another project" })).toBeInTheDocument();
    }
    expect(fallback.mock.calls.filter(([path, init]) => path === "/v1/sessions" && init?.method === "POST")).toHaveLength(lastVisible ? 1 : 0);
    expect(screen.queryByRole("link", { name: /Build the web foundation/ })).not.toBeInTheDocument();
  });

  it("pins and deletes chats from the conversation directory", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(screen.queryByRole("heading", { name: "Pinned" })).not.toBeInTheDocument();
    const pin = await screen.findByRole("button", { name: "Pin Build the web foundation" });
    fireEvent.click(pin);
    const unpin = await screen.findByRole("button", { name: "Unpin Build the web foundation" });
    expect(unpin).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "Pinned" })).toBeInTheDocument();
    expect(unpin.closest(".conversation-section"))
      .toHaveAccessibleName("Pinned chats in hames");
    expect(unpin.querySelector('[data-icon="action.unpin"] .tabler-icon-pinned-off'))
      .toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/pinned",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ pinned: true }) }),
    );

    fireEvent.click(unpin);
    await screen.findByRole("button", { name: "Pin Build the web foundation" });
    expect(screen.queryByRole("heading", { name: "Pinned" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete Build the web foundation" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Its local audit history is retained",
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete chat" }));

    await waitFor(() => {
      expect(screen.queryByRole("link", { name: /Build the web foundation/ })).not.toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("keeps the sidebar expanded at compact desktop widths until the user collapses it", async () => {
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
      matches: query === "(min-width: 821px) and (max-width: 1100px)",
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => true),
    })));
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });
    expect(document.querySelector(".app-frame")).not.toHaveClass("sidebar-collapsed");
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-collapsed");
  });

  it("keeps a hover-revealed sidebar open while navigating within it", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    const reveal = screen.getByRole("button", { name: "Expand sidebar" });
    fireEvent.pointerEnter(reveal);
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-peeking");

    fireEvent.click(screen.getByRole("link", { name: "Memory" }));
    await screen.findByRole("complementary", { name: "Memory sidebar" });
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-collapsed");
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-peeking");

    fireEvent.pointerLeave(document.querySelector(".navigation-stack") as HTMLElement);
    expect(document.querySelector(".app-frame")).not.toHaveClass("sidebar-peeking");
  });

  it("browses real memories grouped by layer", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Memory" }));
    const sidebar = await screen.findByRole("complementary", { name: "Memory sidebar" });
    expect(await screen.findByRole("separator", { name: "Relationships" })).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Semantic"]')).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Episodes"]')).toBeInTheDocument();
    expect(await screen.findByRole("heading", {
      name: "Prefers documentation style",
      level: 1,
    })).toBeInTheDocument();
    expect(document.querySelector(".memory-heading .detail-heading-summary"))
      .toHaveTextContent("The user prefers concise, complete documentation.");
    expect(screen.queryByText("This surface is intentionally quiet for now.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Relationships" }));
    expect(screen.queryByRole("link", { name: /The user prefers concise/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand Relationships" }));
    expect(screen.getByRole("link", { name: /The user prefers concise/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Hames Web is served by/ }));
    expect(await screen.findByRole("heading", {
      name: "Uses web runtime",
      level: 1,
    })).toBeInTheDocument();
    expect(document.querySelector(".memory-heading .detail-heading-summary"))
      .toHaveTextContent("Hames Web is served by the persistent gateway.");
    expect(document.querySelector(".memory-json-value")).toHaveTextContent('"framework": "SolidJS"');
    expect(screen.getByText("Workspace", { selector: "dt" })).toBeInTheDocument();
  });

  it("creates every memory layer from the Memory sidebar form", async () => {
    window.history.replaceState({}, "", "/memory/memory-relationship");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const addMemory = await screen.findByRole("button", { name: "Add Memory" });
    await waitFor(() => expect(addMemory).toBeEnabled());
    fireEvent.click(addMemory);
    expect(await screen.findByRole("dialog", { name: "Add something Hames should remember" }))
      .toBeInTheDocument();
    const type = screen.getByRole("combobox", { name: "Memory type" });
    fireEvent.click(type);
    const typeOptions = screen.getByRole("listbox", { name: "Memory type" });
    expect(within(typeOptions).getAllByRole("option").map((option) => option.dataset.value)).toEqual([
      "relationship", "semantic", "episodic",
    ]);
    fireEvent.click(within(typeOptions).getByRole("option", { name: /^Episode/ }));
    fireEvent.click(screen.getByRole("combobox", { name: "Visible to" }));
    fireEvent.click(within(screen.getByRole("listbox", { name: "Visible to" }))
      .getByRole("option", { name: /^This session team/ }));
    fireEvent.input(screen.getByRole("textbox", { name: "Subject" }), {
      target: { value: "release:tonight" },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "Relationship or fact" }), {
      target: { value: "completed_step" },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "What should Hames remember?" }), {
      target: { value: "Added memory management" },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "Short summary" }), {
      target: { value: "Memory management was added from the web UI." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add memory" }));

    expect(await screen.findByRole("heading", { name: "Completed step", level: 1 }))
      .toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/memories",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          layer: "episodic",
          visibility: "session_team",
          subject: "release:tonight",
          predicate: "completed_step",
          value: "Added memory management",
          summary: "Memory management was added from the web UI.",
        }),
      }),
    );
  });

  it.each([
    ["/agents/default", "Available agents", "Delete Reviewer", "Delete agent", "/v1/agents/reviewer"],
    ["/memory/memory-relationship", "Memories", "Delete The user prefers concise, complete documentation.", "Delete memory", "/v1/sessions/session-current/memories/memory-relationship"],
    ["/skills/project-checks", "Skills", "Delete Review Patterns", "Delete Skill", "/v1/sessions/session-current/skills/review-patterns"],
    ["/scars/scar-one", "Scars", "Delete Read the milestone source before reporting status", "Delete Scar", "/v1/sessions/session-current/scars/scar-one"],
    ["/plugins/event-relay", "Installed plugins", "Remove Event Relay", "Remove plugin", "/v1/plugins/event-relay"],
  ])("removes a sidebar row through confirmation: %s", async (route, list, action, confirm, endpoint) => {
    window.history.replaceState({}, "", route);
    const fetchMock = successfulFetch({ plugins: [installedPlugin] });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    const nav = await screen.findByRole("navigation", { name: list });
    const button = await within(nav).findByRole("button", { name: action });
    expect(button.closest("a")).toBeNull();
    const before = window.location.pathname;
    fireEvent.click(button);
    expect(window.location.pathname).toBe(before);
    expect(fetchMock.mock.calls.some(([path, init]) => path === endpoint && init?.method === "DELETE")).toBe(false);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: confirm }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(endpoint, expect.objectContaining({ method: "DELETE" })));
    await waitFor(() => expect(within(nav).queryByRole("button", { name: action })).not.toBeInTheDocument());
    if (route === "/agents/default" || route === "/skills/project-checks") expect(window.location.pathname).toBe(before);
  });

  it("confirms before deleting a memory", async () => {
    window.history.replaceState({}, "", "/memory/memory-relationship");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    fireEvent.click(await within(screen.getByRole("main")).findByRole("button", {
      name: "Delete The user prefers concise, complete documentation.",
    }));
    expect(screen.getByRole("dialog")).toHaveTextContent("permanently removes the memory");
    fireEvent.click(screen.getByRole("button", { name: "Delete memory" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/memories/memory-relationship",
      expect.objectContaining({ method: "DELETE" }),
    ));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: /The user prefers concise/ }),
    ).not.toBeInTheDocument());
  });

  it("browses Hames and portable Skills grouped by source", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Skills" }));
    const sidebar = await screen.findByRole("complementary", { name: "Skills sidebar" });
    expect(await screen.findByRole("separator", { name: "Hames-created" })).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Global (~/.agents)"]')).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Built in"]')).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Project Checks", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Run checks", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("/work/hames/.agents/skills/project-checks")).toBeInTheDocument();
    expect(screen.queryByText("Promotion and rollback will call gateway controls rather than writing files in-browser.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Global (~/.agents)" }));
    expect(screen.queryByRole("link", { name: /Project Checks/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /Review Patterns/ }));
    expect(await screen.findByRole("heading", { name: "Review Patterns", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("/home/.hames/skills/packages/review-patterns")).toBeInTheDocument();
  });

  it("deletes only Hames-created Skills after confirmation", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Skills" }));
    expect(await screen.findByRole("heading", { name: "Project Checks", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Project Checks" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Visual Verification/ }));
    expect(await screen.findByRole("heading", { name: "Visual Verification", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Visual Verification" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Review Patterns/ }));
    fireEvent.click(await within(screen.getByRole("main")).findByRole("button", { name: "Delete Review Patterns" }));
    expect(screen.getByRole("dialog", { name: "Delete Review Patterns?" })).toHaveTextContent(
      "immutable versions and audit evidence remain stored locally",
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete Skill" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/skills/review-patterns",
      expect.objectContaining({ method: "DELETE" }),
    ));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: /Review Patterns/ }),
    ).not.toBeInTheDocument());
  });

  it("starts real Skill authoring from the Skills sidebar", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Skills" }));
    const createSkill = await screen.findByRole("button", { name: "Create Skill" });
    await waitFor(() => expect(createSkill).toBeEnabled());
    expect(createSkill.closest(".sidebar-context-header")).toBeInTheDocument();
    fireEvent.click(createSkill);
    expect(screen.getByRole("dialog", { name: "Create a Skill" })).toBeInTheDocument();

    fireEvent.input(screen.getByRole("textbox", { name: /What should this Skill do/ }), {
      target: { value: "Review accessibility regressions and verify the rendered result." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Current agent/ }));
    fireEvent.click(screen.getByRole("button", { name: "Start authoring" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/skills/author",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          goal: "Review accessibility regressions and verify the rendered result.",
          scope: "agent",
          target_skill_id: null,
        }),
      }),
    ));
    await waitFor(() => expect(
      screen.queryByRole("dialog", { name: "Create a Skill" }),
    ).not.toBeInTheDocument());
    expect(screen.getByText("Skill authoring started")).toBeInTheDocument();
  });

  it("explains a Scar through its real repair and evidence lineage", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    fireEvent.click(await screen.findByRole("link", { name: "Scars" }));
    const sidebar = await screen.findByRole("complementary", { name: "Scars sidebar" });
    expect(await screen.findByRole("separator", { name: "Needs attention" })).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Guarded"]')).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="History"]')).toBeInTheDocument();

    expect(await screen.findByRole("heading", {
      name: "Explicit correction",
      level: 1,
    })).toBeInTheDocument();
    expect(document.querySelector(".scar-heading .detail-heading-summary"))
      .toHaveTextContent("Read the milestone source before reporting status");
    expect(screen.getByRole("heading", { name: "Why it triggered" })).toBeInTheDocument();
    expect(screen.getByText(/authoritative diagnosis/)).toBeInTheDocument();
    expect(screen.getByText("correction:milestone-source")).toBeInTheDocument();
    expect(screen.getByRole("separator", { name: "Repair history" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Semantic memory" })).toBeInTheDocument();
    expect(screen.getByText("The milestone source was corrected")).toBeInTheDocument();
    expect(screen.queryByText("This surface is intentionally quiet for now.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Guarded" }));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: /Read the milestone source/ }),
    ).not.toBeInTheDocument());
  });

  it("creates a Scar from the Scars sidebar form", async () => {
    window.history.replaceState({}, "", "/scars/scar-one");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const createScarButton = await screen.findByRole("button", { name: "Create Scar" });
    await waitFor(() => expect(createScarButton).toBeEnabled());
    expect(createScarButton.closest(".sidebar-context-header")).toBeInTheDocument();
    fireEvent.click(createScarButton);
    expect(await screen.findByRole("dialog", { name: "Create a Scar" })).toBeInTheDocument();

    fireEvent.input(screen.getByRole("textbox", { name: "What went wrong?" }), {
      target: { value: createdScar.title },
    });
    fireEvent.click(screen.getByRole("combobox", { name: "Severity" }));
    fireEvent.click(within(screen.getByRole("listbox", { name: "Severity" }))
      .getByRole("option", { name: /^Medium/ }));
    fireEvent.click(screen.getByRole("combobox", { name: "Applies to" }));
    fireEvent.click(within(screen.getByRole("listbox", { name: "Applies to" }))
      .getByRole("option", { name: /^Current agent/ }));
    fireEvent.input(screen.getByRole("textbox", { name: "Recognition cue" }), {
      target: { value: createdScar.failure_signature },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "What happened?" }), {
      target: { value: createdScar.description },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "What should happen instead?" }), {
      target: { value: createdScar.expected_behavior },
    });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Create a Scar" }))
      .getByRole("button", { name: "Create Scar" }));

    expect(await screen.findByRole("heading", { name: "Manual", level: 1 })).toBeInTheDocument();
    expect(screen.getAllByText(createdScar.title).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/scars",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          title: createdScar.title,
          severity: "medium",
          scope: "agent",
          failure_signature: createdScar.failure_signature,
          description: createdScar.description,
          expected_behavior: createdScar.expected_behavior,
        }),
      }),
    );
  });

  it("confirms before deleting a Scar", async () => {
    window.history.replaceState({}, "", "/scars/scar-one");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    fireEvent.click(await within(screen.getByRole("main")).findByRole("button", {
      name: "Delete Read the milestone source before reporting status",
    }));
    expect(screen.getByRole("dialog")).toHaveTextContent("permanently removes the Scar");
    fireEvent.click(screen.getByRole("button", { name: "Delete Scar" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/scars/scar-one",
      expect.objectContaining({ method: "DELETE" }),
    ));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: /Read the milestone source/ }),
    ).not.toBeInTheDocument());
  });

  it("keeps transcript elements mounted when the workspace refreshes", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    MockEventSource.instances[0]!.emit("user.message", durableEvent("user.message", 1, {
      content: "Keep this reading position",
    }));
    const message = await screen.findByText("Keep this reading position");
    // Online uses the same refresh path as the ten-second dashboard poll.
    fireEvent(window, new Event("online"));
    await new Promise(resolve => setTimeout(resolve, 100));
    expect(screen.getByText("Keep this reading position")).toBe(message);
  });

  it("replays live gateway events and submits messages", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;
    expect(source.url).toBe("/v1/events?session_id=session-current");
    source.open();
    source.emit("user.message", durableEvent("user.message", 1, {
      content: "Hello",
      purpose: "turn",
      attachments: [{
        digest: "notes-digest",
        name: "notes.md",
        media_type: "text/markdown",
        kind: "text",
        size: 52,
      }],
    }));
    source.emit("run.started", durableEvent("run.started", 2, {}));
    source.emit("context.compiled", durableEvent("context.compiled", 3, {
      provider: "codex",
      model: "gpt-5.6-sol",
      estimated_input_tokens: 30_000,
      selected_sources: [{
        source_id: "agent:default",
        source_type: "agent",
        selected_tokens: 1_200,
        estimated_tokens: 1_200,
        source_path: "/home/.hames/agents/default/AGENT.md",
      }],
      omitted_sources: [{
        source_id: "memory:old",
        source_type: "memory",
        estimated_tokens: 400,
        reason: "budget",
      }],
    }));
    source.emit("response.text_delta", {
      durable: false,
      session_id: "session-current",
      run_id: "run-one",
      type: "response.text_delta",
      payload: { text: "Hi there" },
    });
    source.emit("response.reasoning_delta", {
      durable: false,
      session_id: "session-current",
      run_id: "run-one",
      type: "response.reasoning_delta",
      payload: { text: "**Checking transcript Markdown**" },
    });
    const thinking = (await screen.findByText("Thinking")).closest("details")!;
    expect(thinking).not.toHaveAttribute("open");
    thinking.open = true;
    fireEvent(thinking, new Event("toggle"));
    thinking.open = false;
    fireEvent(thinking, new Event("toggle"));
    source.emit("response.reasoning_delta", {
      durable: false, session_id: "session-current", run_id: "run-one",
      type: "response.reasoning_delta", payload: { text: " and keeping the manual collapse" },
    });
    await waitFor(() => expect(document.querySelector(".reasoning-node .markdown")).toHaveTextContent("keeping the manual collapse"));
    expect(document.querySelector(".reasoning-node")).toBe(thinking);
    const updatedThinking = screen.getByText("Thinking").closest("details")!;
    expect(updatedThinking).not.toHaveAttribute("open");
    updatedThinking.open = true;
    fireEvent(updatedThinking, new Event("toggle"));
    source.emit("response.reasoning_delta", {
      durable: false, session_id: "session-current", run_id: "run-one",
      type: "response.reasoning_delta", payload: { text: " after reopening" },
    });
    await waitFor(() => expect(document.querySelector(".reasoning-node .markdown")).toHaveTextContent("after reopening"));
    expect(screen.getByText("Thinking").closest("details")).toHaveAttribute("open");
    source.emit("tool.requested", durableEvent("tool.requested", 4, {
      tool_call_id: "tool-one",
      name: "shell",
      status: "started",
      arguments: { command: "cargo test" },
    }));
    source.emit("tool.completed", durableEvent("tool.completed", 5, {
      tool_call_id: "tool-one",
      name: "shell",
      status: "completed",
      summary: "Tests passed",
      content: "stdout:\n28 tests passed\nstderr:\n",
      structured_data: { stdout: "28 tests passed\n", stderr: "", exit_code: 0 },
      duration_seconds: 1.25,
    }));
    source.emit("tasks.replaced", durableEvent("tasks.replaced", 6, {
      title: "Transcript polish",
      revision: 1,
      items: [
        { id: "task-one", text: "Build task strip", status: "completed", position: 0 },
        { id: "task-two", text: "Inspect the result", status: "in_progress", position: 1 },
      ],
    }));
    source.emit("tool.requested", durableEvent("tool.requested", 7, {
      tool_call_id: "task-tool",
      name: "task_update",
      status: "started",
      arguments: { action: "update", task_id: "task-two", status: "in_progress" },
    }));
    source.emit("tool.completed", durableEvent("tool.completed", 8, {
      tool_call_id: "task-tool",
      name: "task_update",
      status: "completed",
      summary: "marked Inspect the result in progress",
      structured_data: {
        task_list: {
          title: "Transcript polish",
          revision: 1,
          items: [
            { id: "task-one", text: "Build task strip", status: "completed", position: 0 },
            { id: "task-two", text: "Inspect the result", status: "in_progress", position: 1 },
          ],
        },
      },
    }));
    source.emit("tool.completed", durableEvent("tool.completed", 9, {
      tool_call_id: "edit-tool",
      name: "edit_file",
      status: "completed",
      content: [
        "--- a/web/src/old.ts",
        "+++ b/web/src/old.ts",
        "@@ -1 +1 @@",
        "-old",
        "+new",
      ].join("\n"),
    }));

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview notes.md" }));
    const attachmentPreview = await screen.findByRole("dialog", { name: "notes.md" });
    expect(await within(attachmentPreview).findByText(/Rendered from the durable attachment/))
      .toBeInTheDocument();
    fireEvent.click(within(attachmentPreview).getByRole("button", { name: "Close preview" }));
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(screen.getByText("You").closest(".message-node")).toHaveClass("user");
    expect(document.querySelector(".message-node.assistant .message-role")).toHaveTextContent("Hames");
    expect(document.querySelector(".message-node.assistant .agent-avatar")).toHaveAttribute(
      "aria-label",
      "Hames avatar",
    );
    expect(document.querySelector(".message-node.assistant .agent-avatar")).toHaveClass("alive");
    const reasoningNode = document.querySelector(".reasoning-node");
    expect(reasoningNode?.querySelector(".disclosure-summary strong")).toHaveTextContent(
      "Checking transcript Markdown",
    );
    const promptNode = screen.getByText("Prompt context").closest(".prompt-injection-node");
    expect(promptNode).toHaveTextContent("1 source · 1.2k source tokens · 30k request");
    expect(promptNode).not.toHaveAttribute("open");
    fireEvent.click(promptNode!.querySelector("summary")!);
    expect(await screen.findByText("Model-facing system prompt")).toBeInTheDocument();
    expect(promptNode).toHaveTextContent("Injected project guidance.");
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/contexts/event-3",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    const toolNode = (await screen.findByText("Run")).closest(".tool-node");
    expect(toolNode).toHaveAttribute("data-state", "success");
    expect(toolNode).toHaveTextContent("cargo test");
    fireEvent.click(toolNode!.querySelector("summary")!);
    expect(toolNode?.querySelector(".terminal-card")).toHaveAttribute("data-state", "success");
    expect(toolNode).toHaveTextContent("cargo test");
    expect(toolNode).toHaveTextContent("28 tests passed");
    expect(toolNode).toHaveTextContent("Done");
    expect(toolNode).toHaveTextContent("1.3s");
    const taskPanel = screen.getByRole("region", { name: "Transcript polish" });
    expect(taskPanel).toHaveTextContent("1/2 completed");
    expect(taskPanel).toHaveTextContent("Inspect the result");
    const taskToggle = within(taskPanel).getByRole("button");
    expect(taskPanel.closest(".composer-dock")).not.toBeNull();
    expect(taskPanel.nextElementSibling).toHaveClass("composer-trays");
    fireEvent.click(taskToggle);
    expect(within(taskPanel).queryByRole("list")).not.toBeInTheDocument();
    fireEvent.click(taskToggle);
    expect(screen.getByRole("region", { name: "Transcript polish" }).querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("Updated tasks").closest(".task-tool-row"))
      .toHaveTextContent("1/2 completed · Inspect the result");
    const editNode = screen.getByText("Edit").closest(".tool-node");
    expect(editNode?.querySelector(".tool-diff-summary .diff-added")).toHaveTextContent("+1");
    expect(editNode?.querySelector(".tool-diff-summary .diff-removed")).toHaveTextContent("−1");
    expect(screen.getByRole("button", { name: "Stop" })).toHaveClass("stop");
    expect(screen.getByRole("button", { name: "Stop" })).toBe(screen.getByRole("button", { name: "Stop" }).closest(".composer-toolbar")?.lastElementChild);
    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Queue message" })).not.toBeInTheDocument();

    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), {
      target: { value: "Follow up" },
    });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message Hames" }), { key: "Enter" });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue(""));
    expect(screen.queryByText("Message sent")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/messages",
      expect.objectContaining({ method: "POST" }),
    );
    const submitted = fetchMock.mock.calls.find(([input, init]) =>
      String(input) === "/v1/sessions/session-current/messages" && init?.method === "POST"
    );
    expect(JSON.parse(String(submitted?.[1]?.body))).not.toHaveProperty("attachments");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input) === "/v1/sessions?has_messages=true&include_titled=true&registered_workspaces_only=true&include_delegated=true"
        ),
      ).toHaveLength(2),
    );
    expect(MockEventSource.instances).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/runs/run-one/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("selects and creates agents from the chat bar", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const agentTrigger = await screen.findByRole("button", { name: "Agent: Hames" });
    fireEvent.click(agentTrigger);
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /Reviewer/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/agent",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ agent_id: "reviewer" }),
      }),
    ));
    expect(await screen.findByRole("button", { name: "Agent: Reviewer" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Agent: Reviewer" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Create agent" }));
    expect(screen.getByRole("dialog", { name: "Create an agent" })).toBeInTheDocument();
    fireEvent.input(screen.getByRole("textbox", { name: "Display name" }), {
      target: { value: "Careful Reviewer" },
    });
    expect(screen.queryByRole("textbox", { name: /Agent slug/ })).not.toBeInTheDocument();
    fireEvent.input(screen.getByRole("textbox", { name: "AGENT.md instructions" }), {
      target: { value: "Review changes carefully." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("combobox", { name: "Default model" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create and use agent" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents",
      expect.objectContaining({ method: "POST" }),
    ));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/agent",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ agent_id: "agent-1234567890abcdef1234567890abcdef" }),
      }),
    ));
    expect(await screen.findByRole("button", { name: "Agent: Careful Reviewer" }))
      .toBeInTheDocument();
    expect(screen.queryByText("agent-1234567890abcdef1234567890abcdef")).not.toBeInTheDocument();
    const creation = fetchMock.mock.calls.find(([url, init]) => url === "/v1/agents" && init?.method === "POST");
    expect(JSON.parse(String(creation?.[1]?.body)).source).not.toContain('"id":');
    expect(screen.queryByRole("dialog", { name: "Create an agent" })).not.toBeInTheDocument();
  });

  it("maps durable activity into the Events view without losing the composer draft", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;
    source.open();
    source.emit("user.message", durableEvent("user.message", 1, { content: "Inspect the graph" }));
    source.emit("run.started", durableEvent("run.started", 2, { model: "gpt-5.6-sol" }));
    source.emit("tool.completed", durableEvent("tool.completed", 3, {
      name: "shell",
      status: "completed",
      summary: "Tests passed",
    }));

    const composer = screen.getByRole("textbox", { name: "Message Hames" });
    fireEvent.input(composer, { target: { value: "Keep this draft" } });
    fireEvent.click(screen.getByRole("tab", { name: "Events" }));

    const timeline = await screen.findByRole("region", { name: "Event timeline" });
    await waitFor(() => {
      expect(document.querySelector(".events-toolbar-summary")).toHaveTextContent("Events3");
    });
    expect(timeline).toHaveTextContent("InputModelTools");
    expect(timeline).not.toHaveTextContent("No durable events yet");
    expect(screen.queryByRole("textbox", { name: "Message Hames" })).not.toBeInTheDocument();
    const toolMarker = screen.getByRole("button", { name: "Event 3: Tool · Completed" });
    fireEvent.click(toolMarker);
    expect(screen.getByRole("complementary", { name: "Selected event details" }))
      .toHaveTextContent("Tests passed");

    fireEvent.input(screen.getByRole("searchbox", { name: "Filter events" }), {
      target: { value: "shell" },
    });
    expect(screen.getByRole("table", { name: "Durable session events" })).toHaveTextContent("Tests passed");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue("Keep this draft");
    fireEvent.click(screen.getByRole("tab", { name: "Events" }));
    expect(screen.getByRole("searchbox", { name: "Filter events" })).toHaveValue("shell");
    expect(screen.queryByRole("textbox", { name: "Message Hames" })).not.toBeInTheDocument();
  });

  it("restores a per-chat composer draft after the page is remounted and clears it after send", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    const firstView = render(() => <App />);
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    const composer = screen.getByRole("textbox", { name: "Message Hames" });
    fireEvent.input(composer, { target: { value: "Keep this after refresh" } });
    await waitFor(() => expect(window.localStorage.getItem("hames.composer-draft:session-current"))
      .toBe("Keep this after refresh"));

    firstView.unmount();
    render(() => <App />);
    const restored = await screen.findByRole("textbox", { name: "Message Hames" });
    expect(restored).toHaveValue("Keep this after refresh");

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/messages",
      expect.objectContaining({ method: "POST" }),
    ));
    await waitFor(() => expect(window.localStorage.getItem("hames.composer-draft:session-current"))
      .toBeNull());
  });

  it("reviews durable plans, keeps feedback in Plan, and requires explicit execution", async () => {
    const base = successfulFetch();
    let executeFails = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/plans/current")) return jsonResponse({ current: { id: "plan-one", status: "ready" } });
      if (String(input).endsWith("/plans/current/execute")) return executeFails
        ? jsonResponse({ error: { message: "Please try again" } }, 409)
        : jsonResponse({ run_id: "run-execution" }, 202);
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = render(() => <App />);
    await screen.findByRole("textbox", { name: "Message Hames" });
    fireEvent.click(screen.getByRole("button", { name: "Interaction mode" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Plan" }));
    await waitFor(() => expect(document.querySelector('[data-icon="mode.plan"]')).toBeInTheDocument());
    const proposal = durableEvent("plan.proposed", 1, { plan_id: "plan-one", title: "Fix the layout", revision: 1, markdown: "# Fix the layout" });
    MockEventSource.instances.at(-1)!.emit("plan.proposed", proposal);
    expect(await screen.findByText("Plan ready for review")).toBeInTheDocument();
    expect(document.querySelector('[data-chat-region="composer"] .plan-review')).toBeInTheDocument();
    first.unmount();
    render(() => <App />);
    await screen.findByRole("textbox", { name: "Message Hames" });
    const source = MockEventSource.instances.at(-1)!;
    source.emit("plan.proposed", proposal);
    expect(await screen.findByText("Plan ready for review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Interaction mode" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Auto" }));
    expect(await screen.findByText(/Use Execute plan above the composer/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/mode") && String(init?.body).includes('"auto"'))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Request changes" }));
    const input = screen.getByRole("textbox", { name: "Message Hames" });
    expect(input).toHaveFocus();
    fireEvent.input(input, { target: { value: "Keep the existing API" } });
    expect(screen.getByRole("button", { name: "Execute plan" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/messages") && JSON.parse(String(init?.body)).purpose === "plan_note")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Plan review" })).not.toBeInTheDocument());
    expect(screen.queryByText("Plan changes requested")).not.toBeInTheDocument();
    source.emit("plan.proposed", durableEvent("plan.proposed", 2, { plan_id: "plan-two", title: "Revised layout", revision: 2 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Execute plan" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Execute plan" }));
    expect(await screen.findByText("Please try again")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Execute plan" })).not.toBeDisabled();
    executeFails = false;
    fireEvent.click(screen.getByRole("button", { name: "Execute plan" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Plan review" })).not.toBeInTheDocument());
    expect(screen.queryByText("Starting plan execution…")).not.toBeInTheDocument();
    source.emit("plan.approved", durableEvent("plan.approved", 3, { plan_id: "plan-two" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Plan review" })).not.toBeInTheDocument());
    source.emit("plan.execution.started", durableEvent("plan.execution.started", 4, { plan_id: "plan-two", execution_run_id: "run-execution" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Plan review" })).not.toBeInTheDocument());
    expect(screen.queryByText("Plan execution started")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute plan" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/plans/current/execute"))).toHaveLength(2);
    source.emit("plan.execution.attention", durableEvent("plan.execution.attention", 5, { plan_id: "plan-two", message: "Execution could not start" }));
    expect(screen.queryByText("Plan execution needs attention")).not.toBeInTheDocument();
    expect(document.querySelector('[data-chat-region="composer"] .plan-review')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    expect(await screen.findByRole("menuitem", { name: "Resume plan execution" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: "Review the remaining blocker" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message Hames" }), { key: "Enter" });
    await waitFor(() => {
      const messages = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/messages") && init?.method === "POST");
      expect(JSON.parse(String(messages.at(-1)?.[1]?.body))).toMatchObject({ content: "Review the remaining blocker", purpose: "turn" });
    });
  });

  it("updates mode and thinking through plugin-contributed composer controls", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    expect(document.querySelector('[data-icon="mode.auto"] svg')).toHaveClass(
      "tabler-icon-sparkles",
    );
    const modeTrigger = screen.getByRole("button", { name: "Interaction mode" });
    fireEvent.click(modeTrigger);
    fireEvent.keyDown(modeTrigger, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Interaction mode" })).not.toBeInTheDocument();
    fireEvent.click(modeTrigger);
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Plan" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current/mode",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ mode: "plan" }) }),
      ),
    );

    await waitFor(() =>
      expect(document.querySelector('[data-icon="mode.plan"] svg')).toHaveClass(
        "tabler-icon-checklist",
      ),
    );

    const selectionTrigger = screen.getByRole("button", { name: /Model and thinking/ });
    fireEvent.click(selectionTrigger);
    fireEvent.click(screen.getByRole("menuitem", { name: /Thinking/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Medium" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            provider: "codex",
            model: "gpt-5.6-sol",
            reasoning_effort: "medium",
          }),
        }),
      ),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Interaction mode" })).toHaveTextContent("Plan");
      expect(screen.getByRole("button", { name: /Model and thinking/ })).toHaveTextContent("Medium");
    });
    expect(screen.queryByRole("button", { name: "Thinking level" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to message" })).toBeEnabled();
  });

  it("opens slash commands from the plus button and supports keyboard completion", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    const composer = screen.getByRole("textbox", { name: "Message Hames" });
    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Commands & Skills/ }));
    expect(screen.getByRole("listbox", { name: "Slash commands" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/compact/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("option", { name: /\/tasks/ })).not.toBeInTheDocument();
    for (const command of ["sessions", "resume", "model", "effort", "agent", "mode", "themes", "memory"]) {
      expect(screen.queryByRole("option", { name: new RegExp(`/${command}\\b`) })).not.toBeInTheDocument();
    }

    fireEvent.keyDown(composer, { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: /\/fork/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(composer).toHaveValue("/fork ");

    fireEvent.input(composer, { target: { value: "/project-c" } });
    expect(await screen.findByRole("option", { name: /\/project-checks/ })).toBeInTheDocument();
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(composer).toHaveValue("/project-checks ");
  });

  it("previews image and file attachments in the joined composer drawer", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    expect(screen.getByRole("menu", { name: "Add to message" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Upload files or images/ }));
    const file = new File(["image data"], "pixel.png", { type: "image/png" });
    const notes = new File(["hello"], "notes.md", { type: "text/markdown" });
    const fileInput = screen.getByLabelText("Upload files or images");
    expect(fileInput).toHaveAttribute("hidden");
    fireEvent.change(fileInput, {
      target: { files: [file, notes] },
    });
    const attachmentDrawer = await screen.findByLabelText("Message attachments");
    expect(attachmentDrawer).toHaveClass("composer-attachment-drawer");
    expect(attachmentDrawer.closest(".composer-stack")).not.toBeInTheDocument();
    expect(attachmentDrawer.closest(".composer-trays")?.nextElementSibling).toHaveClass("composer-stack");
    expect(attachmentDrawer.compareDocumentPosition(document.querySelector(".composer-shell")!))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    const drawerToggle = within(attachmentDrawer).getByRole("button", { name: "Collapse attachments" });
    expect(drawerToggle).toHaveAttribute("aria-expanded", "true");

    expect(await screen.findByText("pixel.png")).toBeInTheDocument();
    expect(screen.getByText("notes.md")).toBeInTheDocument();
    expect(screen.getByText("MD")).toBeInTheDocument();
    expect(attachmentDrawer.querySelector('[data-icon="action.file"]')).toBeInTheDocument();
    expect(attachmentDrawer.querySelector('img[src^="data:image/png;base64,"]')).toBeInTheDocument();

    fireEvent.click(within(attachmentDrawer).getByRole("button", { name: "Preview pixel.png" }));
    let preview = screen.getByRole("dialog", { name: "pixel.png" });
    expect(within(preview).getByRole("img", { name: "pixel.png" }))
      .toHaveAttribute("src", expect.stringMatching(/^data:image\/png;base64,/));
    fireEvent.click(within(preview).getByRole("button", { name: "Close preview" }));

    fireEvent.click(within(attachmentDrawer).getByRole("button", { name: "Preview notes.md" }));
    preview = await screen.findByRole("dialog", { name: "notes.md" });
    expect(within(preview).getByText("hello")).toBeInTheDocument();
    fireEvent.click(within(preview).getByRole("button", { name: "Close preview" }));

    fireEvent.click(drawerToggle);
    expect(drawerToggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(drawerToggle);
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), {
      target: { value: "Describe this" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => {
      if (String(input) !== "/v1/sessions/session-current/messages" || init?.method !== "POST") {
        return false;
      }
      const body = JSON.parse(String(init.body)) as { attachments?: { name: string }[] };
      return body.attachments?.map((attachment) => attachment.name).join(",") === "pixel.png,notes.md";
    })).toBe(true));
  });

  it("executes web commands directly and shows compaction lifecycle in the transcript", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;
    const composer = screen.getByRole("textbox", { name: "Message Hames" });

    fireEvent.input(composer, { target: { value: "/dream " } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(composer).toHaveValue(""));
    expect(screen.queryByText("Dream started")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/dream",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.input(composer, { target: { value: "/compact " } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(composer).toHaveValue(""));
    expect(screen.queryByText("Compaction started")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/compact",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls.filter(([input]) =>
      String(input) === "/v1/sessions/session-current/messages"
    )).toHaveLength(0);

    source.emit("context.compaction.started", durableEvent("context.compaction.started", 1, {
      compaction_id: "compact-one",
      trigger: "manual",
      preserve_recent_turns: 4,
    }, "compact-one"));
    expect(await screen.findByText("Compacting context")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

    source.emit("context.compaction.completed", durableEvent("context.compaction.completed", 2, {
      compaction_id: "compact-one",
      trigger: "manual",
      preserve_recent_turns: 4,
      summary: "Older decisions retained.",
      turns_compacted: 6,
      before_tokens: 30_000,
      after_tokens: 8_000,
      partial: false,
    }, "compact-one"));
    expect(await screen.findByText("Compacted context")).toBeInTheDocument();
    expect(screen.getByText("6 turns · 30k → 8.0k tokens")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toHaveClass("send");

    fireEvent.input(composer, { target: { value: "/goal Ship the command palette" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(composer).toHaveValue(""));
    expect(screen.queryByText("Running goal · Ship the command palette")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/goals",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows folded wrap-up and dream maintenance in the web transcript", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;

    source.emit("memory.job.queued", durableEvent("memory.job.queued", 1, {
      job_id: "memory-job",
      kind: "extraction",
      status: "pending",
      attempts: 0,
    }, "source-run"));
    source.emit("memory.job.started", durableEvent("memory.job.started", 2, {
      job_id: "memory-job",
      kind: "extraction",
      status: "running",
      attempts: 1,
    }, "source-run"));
    expect(await screen.findByLabelText("Wrap-up: Memory update waiting for the idle model"))
      .toBeInTheDocument();

    source.emit("model.response.started", durableEvent(
      "model.response.started",
      3,
      {},
      "memory-job",
      "memory-job",
    ));
    expect(await screen.findByLabelText("Wrap-up: Reviewing this turn for durable memory"))
      .toHaveAttribute("data-state", "running");

    source.emit("memory.job.completed", durableEvent("memory.job.completed", 4, {
      job_id: "memory-job",
      kind: "extraction",
      status: "completed",
      attempts: 1,
    }, "source-run"));
    expect(await screen.findByLabelText("Wrap-up: Turn memory reviewed"))
      .toHaveAttribute("data-state", "success");
    expect(document.querySelectorAll('.maintenance-node[data-kind="wrap-up"]')).toHaveLength(1);

    source.emit("dream.started", durableEvent("dream.started", 5, {
      dream_id: "dream-one",
      status: "running",
    }, null));
    expect(await screen.findByLabelText("Dream: Reviewing recent memory, Skills, and scars"))
      .toHaveAttribute("data-state", "running");

    source.emit("dream.completed", durableEvent("dream.completed", 6, {
      dream_id: "dream-one",
      status: "completed",
      memories_reconciled: 2,
      skills_reconciled: 1,
      scars_repaired: 1,
    }, null));
    expect(await screen.findByLabelText("Dream: Reconciled 2 memories · 1 Skill · 1 scar"))
      .toHaveAttribute("data-state", "success");
    expect(document.querySelectorAll('.maintenance-node[data-kind="dream"]')).toHaveLength(1);
  });

  it("opens a forked web conversation instead of sending the command as a prompt", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    const composer = screen.getByRole("textbox", { name: "Message Hames" });

    fireEvent.input(composer, { target: { value: "/fork event-12" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(await screen.findByRole("heading", { name: "Forked conversation", level: 1 }))
      .toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/fork",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ at: "event-12", title: null, agent_id: null }),
      }),
    );
  });

  it("shows conversation tokens by the composer and pooled account usage in Settings", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    const composer = screen.getByRole("textbox", { name: "Message Hames" });
    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Commands & Skills/ }));
    await screen.findByRole("listbox", { name: "Slash commands" });
    expect(screen.queryByRole("option", { name: /\/usage/ })).not.toBeInTheDocument();
    fireEvent.keyDown(composer, { key: "Escape" });

    const usageTrigger = await screen.findByRole("button", { name: /Open token breakdown/ });
    expect(usageTrigger).toHaveTextContent("8 requests");
    expect(usageTrigger).toHaveTextContent("Cache hit 50%");
    expect(usageTrigger).toHaveTextContent("Input 24k · Output 6k");
    expect(usageTrigger).toHaveTextContent("25% context");
    fireEvent.click(usageTrigger);

    const popup = await screen.findByRole("dialog", { name: "Token breakdown" });
    expect(document.querySelector(".dialog-backdrop")).not.toBeInTheDocument();
    expect(within(popup).getByText("112k input budget")).toBeInTheDocument();
    expect(within(popup).getByText("8k response reserve")).toBeInTheDocument();
    expect(within(popup).getByText("Input / prompt").parentElement).toHaveTextContent("24k");
    expect(within(popup).getByText("Output").parentElement).toHaveTextContent("6k");
    expect(within(popup).getByText("Cached input").parentElement).toHaveTextContent("12k · 50% hit");
    expect(within(popup).getByText("Reasoning").parentElement).toHaveTextContent("2.5k");
    expect(within(popup).getByText("Requests").parentElement).toHaveTextContent("8");
    expect(within(popup).getByRole("progressbar", { name: "Context window" }))
      .toHaveAttribute("aria-valuenow", "30000");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Token breakdown" })).not.toBeInTheDocument();
    expect(usageTrigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /Usage/ }));

    expect(await screen.findByRole("heading", { name: "Usage", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Appearance", level: 2 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/settings/usage"));
    expect(screen.queryByRole("combobox", { name: "Chat" })).not.toBeInTheDocument();
    expect(await screen.findByText("ChatGPT usage")).toBeInTheDocument();
    expect(screen.getByText("Plus")).toBeInTheDocument();
    expect(screen.queryByText("Session totals")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Context window" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "5-hour limit" }))
      .toHaveAttribute("aria-valuenow", "25");
    expect(within(screen.getByRole("region", { name: "ChatGPT usage" })).getByRole("progressbar", { name: "Weekly limit" }))
      .toHaveAttribute("aria-valuenow", "60");
    expect(within(screen.getByRole("region", { name: "Grok usage" })).getByRole("progressbar", { name: "Weekly limit" }))
      .toHaveAttribute("aria-valuenow", "88");
    expect(screen.getByLabelText("Sep 2, 2026: 48,000 tokens"))
      .toHaveAttribute("data-level", "4");
    expect(fetchMock.mock.calls.some(([url, init]) =>
      url === "/v1/sessions/session-current/messages" && init?.method === "POST"
    )).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/usage",
      expect.objectContaining({ credentials: "same-origin" }),
    );

    expect(screen.queryByRole("dialog", { name: "Usage" })).not.toBeInTheDocument();
  });

  it("selects provider, model, and explicit thinking in one model picker flow", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    const modelTrigger = screen.getByRole("button", { name: /Model and thinking/ });
    expect(modelTrigger).toHaveTextContent("gpt-5.6-solHigh");
    fireEvent.click(modelTrigger);
    expect(document.querySelector(".model-picker-popover")).toHaveAttribute("data-morph");
    expect(document.querySelector(".model-picker-popover")).toHaveAttribute("data-layout", "root");
    fireEvent.click(screen.getByRole("menuitem", { name: /Model/ }));
    expect(document.querySelector(".model-picker-popover")).toHaveAttribute("data-layout", "models");
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /qwen3:8b/ }));
    expect(fetchMock.mock.calls.some(([url]) => url === "/v1/providers/ollama-idle/probe"))
      .toBe(false);

    expect(screen.getByText("Choose thinking to finish")).toBeInTheDocument();
    expect(document.querySelector(".model-picker-popover")).toHaveAttribute("data-layout", "effort");
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH"),
    ).toHaveLength(0);
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Low" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            provider: "ollama",
            model: "qwen3:8b",
            reasoning_effort: "low",
          }),
        }),
      ),
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Model and thinking/ })).toHaveTextContent(
        "qwen3:8bLow",
      );
    });
  });

  it("creates a durable chat and opens its composer", async () => {
    window.history.replaceState({}, "", "/chat");
    const fetchMock = successfulFetch({ emptyWorkspace: true });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(await screen.findByRole("heading", { name: "What should we work on?" })).toBeInTheDocument();
    const freshChat = document.querySelector(".session-chat");
    expect(freshChat).toHaveClass("fresh");
    expect(freshChat?.querySelector(".fresh-chat-hero")).toBeInTheDocument();
    expect(freshChat?.querySelector(".fresh-chat-hero .agent-avatar")).not.toBeInTheDocument();
    expect(await screen.findByText("Hames is ready when you are.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Agent: Hames" }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /Reviewer/ }));
    expect(await screen.findByText("Reviewer is ready when you are.")).toBeInTheDocument();
    expect(screen.queryByText("Hames is ready when you are.")).not.toBeInTheDocument();
    expect(freshChat?.querySelector(".composer-dock")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "New chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New chat" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Starting a new chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Hames" })).not.toBeDisabled();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ working_directory: "/work/hames" }),
      }),
    );

    fireEvent.click(screen.getByRole("link", { name: "Chat" }));
    await waitFor(() => {
      const createCalls = fetchMock.mock.calls.filter(([url, init]) =>
        url === "/v1/sessions" && init?.method === "POST"
      );
      expect(createCalls).toHaveLength(1);
      expect(window.location.pathname).toBe("/chat/session-new");
    });

    fireEvent.click(screen.getByRole("link", { name: "Agents" }));
    await screen.findByRole("complementary", { name: "Agents sidebar" });
    fireEvent.click(screen.getByRole("link", { name: "Chat" }));
    await waitFor(() => {
      expect(window.location.pathname).toBe("/chat/session-new");
      expect(fetchMock.mock.calls.filter(([url, init]) =>
        url === "/v1/sessions" && init?.method === "POST"
      )).toHaveLength(1);
    });
    expect(await screen.findByRole("textbox", { name: "Message Hames" })).not.toBeDisabled();
  });

  it("keeps one New chat row without management actions until it has a title", async () => {
    window.history.replaceState({}, "", "/chat");
    const fetchMock = successfulFetch({ emptyWorkspace: true });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(await screen.findByRole("link", { name: "New chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pin New chat" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete New chat" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url, init]) =>
      url === "/v1/sessions" && init?.method === "POST"
    )).toHaveLength(1);
  });

  it("titles a first message immediately, applies later title events, and defers the next draft", async () => {
    window.history.replaceState({}, "", "/chat");
    const fetchMock = successfulFetch({ emptyWorkspace: true });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const composer = screen.getByRole("textbox", { name: "Message Hames" });
    fireEvent.input(composer, { target: { value: "Clean up the sidebar title lifecycle" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByRole("heading", { name: "Clean up the sidebar title lifecycle" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    await waitFor(() => expect(composer).toHaveValue(""));
    expect(fetchMock.mock.calls.some(([url]) => url === "/v1/sessions/session-new/title"))
      .toBe(false);

    MockEventSource.instances[0]!.emit(
      "session.title.changed",
      durableEvent("session.title.changed", 99, { title: "A cleaner sidebar lifecycle" }, null, null, "session-new"),
    );
    expect(await screen.findByRole("heading", { name: "A cleaner sidebar lifecycle" }))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: "A cleaner sidebar lifecycle" }))
      .toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Agents" }));
    await screen.findByRole("complementary", { name: "Agents sidebar" });
    fireEvent.click(screen.getByRole("link", { name: "Chat" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url, init]) =>
      url === "/v1/sessions" && init?.method === "POST"
    )).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "New chat in hames" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new-2"));
    expect(await screen.findByRole("link", { name: "New chat" })).toBeInTheDocument();
  });

  it.each([
    { input: "hi", endpoint: "messages", title: "hi" },
    { input: "/dream", endpoint: "dream", title: "Dream" },
    { input: "/heal", endpoint: "messages", title: "Heal scars" },
    { input: "/compact", endpoint: "compact", title: "Compact conversation" },
    { input: "/goal Inspect the workspace", endpoint: "goals", title: "Inspect the workspace" },
    { input: "/goal resume", endpoint: "goals/current/resume", title: "Resume goal" },
  ])("keeps $input titled through stale refreshes and a New click during submission", async ({ input: content, endpoint, title }) => {
    window.history.replaceState({}, "", "/chat");
    const baseFetch = successfulFetch({ emptyWorkspace: true });
    let release!: (value: Response) => void;
    let messageStarted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === `/v1/sessions/session-new/${endpoint}` && init?.method === "POST") {
        messageStarted = true;
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      if (messageStarted && path === "/v1/sessions?has_messages=true&include_titled=true&registered_workspaces_only=true&include_delegated=true") {
        return jsonResponse([sessions[0], { ...createdSession, id: "session-new", title: null },
          { ...createdSession, id: "old-untitled", title: null }]);
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: content } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByRole("heading", { name: title });
    await waitFor(() => expect(messageStarted).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "New chat in hames" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new-2"));
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: "second draft" } });
    release(jsonResponse({ disposition: "started", run_id: "run-one", queued: null, status: "running", objective: "Inspect the workspace" }, 202));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) =>
      String(input) === "/v1/sessions?has_messages=true&include_titled=true&registered_workspaces_only=true&include_delegated=true").length).toBeGreaterThan(1));
    expect(window.location.pathname).toBe("/chat/session-new-2");
    expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue("second draft");
    expect(screen.getByRole("link", { name: title })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "New chat" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/chat/session-new-2");
  });

  it("keeps the fourth pending message draft when the server queue is full", async () => {
    window.history.replaceState({}, "", "/chat/session-current");
    const baseFetch = successfulFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/v1/sessions/session-current/messages" && init?.method === "POST")
        return jsonResponse({ error: { code: "session_queue_full", message: "Queue is full (3 pending messages). Remove a message or wait for a slot." } }, 409);
      return baseFetch(input, init);
    }));
    render(() => <App />);
    const composer = await screen.findByRole("textbox", { name: "Message Hames" });
    fireEvent.input(composer, { target: { value: "Fourth pending message" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await screen.findByText("Queue is full (3 pending messages). Remove a message or wait for a slot.");
    expect(composer).toHaveValue("Fourth pending message");
    expect(window.localStorage.getItem("hames.composer-draft:session-current")).toBe("Fourth pending message");
  });

  it("offers explicit workspace trust and retries the preserved draft", async () => {
    window.history.replaceState({}, "", "/chat/session-current");
    const baseFetch = successfulFetch();
    let attempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/v1/sessions/session-current/messages" && init?.method === "POST") {
        attempts += 1;
        if (attempts === 1) {
          return jsonResponse({
            error: { code: "working_directory_untrusted", message: "working directory is not trusted" },
          }, 409);
        }
        return jsonResponse({ disposition: "started", run_id: "run-trusted", queued: null }, 202);
      }
      if (path === "/v1/sessions/session-current/trust" && init?.method === "PUT") {
        return jsonResponse({ path: "/work/hames", trusted: true, grant_id: "grant-1" });
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    const composer = await screen.findByRole("textbox", { name: "Message Hames" });
    fireEvent.input(composer, { target: { value: "Use this newly added workspace" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("working directory is not trusted")).toBeInTheDocument();
    expect(composer).toHaveValue("Use this newly added workspace");
    fireEvent.click(screen.getByRole("button", { name: "Trust workspace & retry" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/trust",
      expect.objectContaining({ method: "PUT" }),
    ));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue(""));
    expect(screen.queryByText("Message sent")).not.toBeInTheDocument();
    expect(composer).toHaveValue("");
  });

  it.each(["/dream", "/heal", "/compact", "/goal Check things", "/goal resume"])(
    "restores an untitled draft when %s is rejected", async content => {
      window.history.replaceState({}, "", "/chat");
      const baseFetch = successfulFetch({ emptyWorkspace: true });
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).startsWith("/v1/sessions/session-new/") && init?.method === "POST") {
          return jsonResponse({ error: { code: "rejected", message: "Work rejected" } }, 409);
        }
        return baseFetch(input, init);
      }));
      render(() => <App />);
      await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
      fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: content } });
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));
      await screen.findByText("Work rejected");
      expect(screen.getByRole("heading", { name: "New chat" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "New chat" })).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue(content);
    },
  );

  it.each([
    { input: "/goal", endpoint: "goals/current", body: null, note: "No active goal" },
    { input: "/stop", endpoint: "terminals", body: { closed: 0 }, note: "No background terminals are running" },
  ])("does not consume a draft for $input inspection or control", async command => {
    window.history.replaceState({}, "", "/chat");
    const baseFetch = successfulFetch({ emptyWorkspace: true });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === `/v1/sessions/session-new/${command.endpoint}`) return jsonResponse(command.body);
      return baseFetch(input, init);
    }));
    render(() => <App />);
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: command.input } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue(""));
    if (command.input === "/goal") await screen.findByText(command.note);
    else expect(screen.queryByText(command.note)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "New chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete New chat" })).not.toBeInTheDocument();
  });

  it("persists a dream-only chat through completion and reload before another New", async () => {
    window.history.replaceState({}, "", "/chat");
    const baseFetch = successfulFetch({ emptyWorkspace: true });
    let accepted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/v1/sessions/session-new/dream" && init?.method === "POST") {
        accepted = true;
        return jsonResponse({ dream_id: "dream-only" }, 202);
      }
      if (accepted && path === "/v1/sessions?has_messages=true&include_titled=true&registered_workspaces_only=true&include_delegated=true") {
        return jsonResponse([sessions[0], { ...createdSession, id: "session-new", title: "Dream" }]);
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const mounted = render(() => <App />);
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), { target: { value: "/dream" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue(""));
    expect(screen.queryByText("Dream started")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Dream" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    MockEventSource.instances[0]!.emit("dream.completed", durableEvent("dream.completed", 99,
      { dream_id: "dream-only", memories_reconciled: 0, skills_reconciled: 0, scars_repaired: 0 }, null, null, "session-new"));
    await waitFor(() => expect(document.querySelector('[data-kind="dream"]')).toHaveTextContent("already tidy"));
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/messages"))).toBe(false);
    mounted.unmount();
    render(() => <App />);
    await screen.findByRole("heading", { name: "Dream" });
    expect(screen.getByRole("link", { name: "Dream" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New chat in hames" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new-2"));
    expect(screen.getByRole("textbox", { name: "Message Hames" })).toHaveValue("");
    expect(screen.getByRole("link", { name: "Dream" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/chat/session-new-2");
  });

  it("restores an empty chat directly after a browser refresh", async () => {
    window.history.replaceState({}, "", "/chat/session-new");
    const fetchMock = successfulFetch({ emptyWorkspace: true });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("heading", { name: "What should we work on?" }))
      .toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Message Hames" })).not.toBeDisabled()
    );
    expect(document.querySelector(".session-chat")).toHaveClass("fresh");
    expect(screen.getByRole("combobox", { name: "Current workspace" })).toHaveTextContent("hames");
    expect(document.querySelector(".composer-stats-skeleton")).not.toBeInTheDocument();
    expect(document.querySelector(".composer-stats-line")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-new",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(fetchMock.mock.calls.filter(([url, init]) =>
      url === "/v1/sessions" && init?.method === "POST"
    )).toHaveLength(0);
  });

  it("recovers an invalid chat route to an existing workspace chat", async () => {
    window.history.replaceState({}, "", "/chat/missing-session");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-current"));
    expect(await screen.findByRole("heading", { name: "Build the web foundation" }))
      .toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Hames" })).not.toBeDisabled();
    expect(fetchMock.mock.calls.some(([path, init]) => path === "/v1/sessions" && init?.method === "POST")).toBe(false);
  });

  it("resolves approval and question events through gateway mutations", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;

    source.emit(
      "approval.requested",
      durableEvent("approval.requested", 1, {
        approval_id: "approval-one",
        tool_call_id: "tool-one",
        name: "shell",
        arguments: { command: "cargo test" },
        request_hash: "a".repeat(64),
        working_directory: "/work/hames",
        reason: "Run the test suite",
        allow_session: true,
      }),
    );
    source.emit(
      "question.requested",
      durableEvent("question.requested", 2, {
        question_id: "question-one",
        tool_call_id: "tool-two",
        question: "Continue with the change?",
        options: [{ label: "Proceed", description: "Continue implementation" }],
      }),
    );
    source.emit(
      "question.requested",
      durableEvent("question.requested", 3, {
        question_id: "question-many",
        tool_call_id: "tool-three",
        question: "Which checks should run?",
        answer_type: "multiple_choice",
        options: [
          { label: "Unit", description: "Fast component checks" },
          { label: "Integration", description: "Gateway checks" },
          { label: "Browser", description: "Rendered interaction checks" },
        ],
        min_selections: 2,
        max_selections: 3,
      }),
    );
    source.emit(
      "question.requested",
      durableEvent("question.requested", 4, {
        question_id: "question-text",
        tool_call_id: "tool-four",
        question: "What should the release be called?",
        answer_type: "text",
        options: [],
        placeholder: "Release name",
      }),
    );

    const approvalDialog = await screen.findByRole("dialog", { name: "Allow shell?" });
    expect(approvalDialog).toHaveAttribute("aria-modal", "true");
    expect(document.querySelector(".approval-dialog")).toBeInTheDocument();
    expect(within(approvalDialog).getByRole("button", { name: "Deny permission request" }))
      .toBeInTheDocument();
    expect(within(approvalDialog).getByRole("button", { name: "Deny" }))
      .toHaveAttribute("data-variant", "destructive");
    expect(within(approvalDialog).getByRole("button", { name: "Allow for session" }))
      .toHaveClass("approval-allow");
    expect(within(approvalDialog).getByRole("button", { name: "Allow once" }))
      .toHaveClass("approval-allow");
    fireEvent.click(within(approvalDialog).getByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/approvals/approval-one",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    source.emit(
      "approval.resolved",
      durableEvent("approval.resolved", 5, {
        approval_id: "approval-one",
        request_hash: "a".repeat(64),
        decision: "approved",
        approval_scope: "once",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Allow shell?" })).not.toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /Proceed/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/questions/question-one",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            selected_option: "Proceed",
            selected_options: [],
            note: "",
            custom_answer: "",
          }),
        }),
      ),
    );

    const unit = screen.getByRole("checkbox", { name: /Unit/ });
    const browser = screen.getByRole("checkbox", { name: /Browser/ });
    fireEvent.click(unit);
    fireEvent.click(browser);
    fireEvent.click(screen.getByRole("button", { name: "Submit choices" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/questions/question-many",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          selected_option: null,
          selected_options: ["Unit", "Browser"],
          note: "",
          custom_answer: "",
        }),
      }),
    ));

    const textAnswer = screen.getByRole("textbox", { name: "Your answer" });
    expect(textAnswer).toHaveAttribute("placeholder", "Release name");
    fireEvent.input(textAnswer, { target: { value: "Moonrise" } });
    fireEvent.keyDown(textAnswer, { key: "Enter" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/questions/question-text",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          selected_option: null,
          selected_options: [],
          note: "",
          custom_answer: "Moonrise",
        }),
      }),
    ));
  });

  it("treats closing a permission modal as an explicit denial", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    MockEventSource.instances[0]!.emit(
      "approval.requested",
      durableEvent("approval.requested", 1, {
        approval_id: "approval-one",
        tool_call_id: "tool-one",
        name: "shell",
        arguments: { command: "cargo test" },
        request_hash: "a".repeat(64),
        working_directory: "/work/hames",
        reason: "Run the test suite",
        allow_session: true,
      }),
    );

    const dialog = await screen.findByRole("dialog", { name: "Allow shell?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deny permission request" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/approvals/approval-one",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "denied", request_hash: "a".repeat(64) }),
      }),
    ));
  });

  it("provides every core plugin surface through the icon rail", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    for (const label of ["Chat", "Agents", "Memory", "Skills", "Scars", "Plugins", "Settings"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "Runs" })).not.toBeInTheDocument();
  });

  it("manages installed plugins and reviews a local package before installing", async () => {
    window.history.replaceState({}, "", "/plugins");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const sidebar = await screen.findByRole("complementary", { name: "Plugins sidebar" });
    expect(await screen.findByRole("separator", { name: "Enabled" })).toBeInTheDocument();
    expect(sidebar.querySelector('[role="separator"][aria-label="Disabled"]')).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Event Relay", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Make network requests")).toBeInTheDocument();
    const enableSwitch = screen.getByRole("switch", { name: "Disabled" });
    expect(enableSwitch).not.toBeChecked();
    fireEvent.click(enableSwitch);
    expect(await screen.findByRole("switch", { name: "Enabled" })).toBeChecked();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/plugins/event-relay/enable",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Add Plugin" }));
    expect(screen.getByRole("dialog", { name: "Add a plugin" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use a path on this gateway" }));
    fireEvent.input(screen.getByRole("textbox", { name: /^Package directory/ }), {
      target: { value: "/work/project-stats" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Inspect package" }));

    expect(await screen.findByRole("dialog", { name: "Review Project Stats" })).toBeInTheDocument();
    expect(screen.getByText("Adds callable tools")).toBeInTheDocument();
    expect(screen.getByText("Read project files")).toBeInTheDocument();
    const installButton = screen.getByRole("button", { name: "Install plugin" });
    expect(installButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /^I reviewed these permissions/ }));
    expect(installButton).toBeEnabled();
    fireEvent.click(installButton);

    expect(await screen.findByRole("heading", { name: "Project Stats", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/plugins/inspect",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ path: "/work/project-stats" }) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/plugins/install",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ path: "/work/project-stats" }) }),
    );
  });

  it("uploads a selected plugin folder before permission review", async () => {
    window.history.replaceState({}, "", "/plugins");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    fireEvent.click(await screen.findByRole("button", { name: "Add Plugin" }));
    expect(screen.getByRole("button", { name: "Choose plugin folder" })).toBeInTheDocument();
    expect(screen.getByLabelText("Choose plugin folder")).toHaveAttribute("hidden");
    const manifest = new File(["id = \"project-stats\""], "plugin.toml", { type: "text/plain" });
    const worker = new File(["print('worker')"], "worker.py", { type: "text/x-python" });
    Object.defineProperty(manifest, "webkitRelativePath", { value: "project-stats/plugin.toml" });
    Object.defineProperty(worker, "webkitRelativePath", { value: "project-stats/worker.py" });
    fireEvent.change(screen.getByLabelText("Choose plugin folder"), {
      target: { files: [manifest, worker] },
    });

    expect(await screen.findByRole("dialog", { name: "Review Project Stats" })).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([input]) => String(input) === "/v1/plugins/uploads");
    expect(call?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
    const body = JSON.parse(String(call?.[1]?.body)) as { files: { path: string }[] };
    expect(body.files.map((file) => file.path)).toEqual(["plugin.toml", "worker.py"]);
  });

  it("keeps plugin installation in the sidebar and centers the empty guidance", async () => {
    window.history.replaceState({}, "", "/plugins");
    vi.stubGlobal("fetch", successfulFetch({ plugins: [] }));
    render(() => <App />);

    const addPlugin = await screen.findByRole("button", { name: "Add Plugin" });
    expect(screen.getAllByRole("button", { name: "Add Plugin" })).toHaveLength(1);
    expect(addPlugin.closest(".sidebar-context-header")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".plugin-route-empty")).toHaveTextContent(
      "No plugins yet—use in the sidebar.",
    ));
    expect(screen.getByRole("img", { name: "Add Plugin" })
      .querySelector('[data-icon="action.add"] svg')).toHaveClass("tabler-icon-plus");
    fireEvent.click(addPlugin);
    expect(screen.getByRole("dialog", { name: "Add a plugin" })).toBeInTheDocument();
  });

  it("switches and persists the dark appearance from Settings", async () => {
    window.history.replaceState({}, "", "/settings");
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    const sidebar = await screen.findByRole("complementary", { name: "Settings sidebar" });
    expect(screen.getByRole("heading", { name: "Settings", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Appearance/ })).toHaveAttribute("href", "/settings/appearance");
    expect(screen.getByRole("link", { name: /Usage/ })).toHaveAttribute("href", "/settings/usage");
    expect(sidebar.querySelector('[role="switch"]')).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Appearance", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Usage", level: 2 })).toBeInTheDocument();

    const darkMode = await screen.findByRole("switch", { name: "Dark mode" });
    expect(darkMode).not.toBeChecked();
    expect(darkMode.closest(".settings-page")).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("data-theme", "light");

    fireEvent.click(darkMode);
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(window.localStorage.getItem("hames.theme")).toBe("dark");

    fireEvent.click(darkMode);
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "light"));
    expect(window.localStorage.getItem("hames.theme")).toBe("light");
  });

  it("opens a real agent breakdown and persists edits", async () => {
    window.history.replaceState({}, "", "/agents");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    const agentSidebar = screen.getByRole("complementary", { name: "Agents sidebar" });
    expect(await screen.findByRole("heading", { name: "Hames", level: 1 })).toBeInTheDocument();
    const createAgent = screen.getByRole("button", { name: "Create Agent" });
    expect(createAgent.closest(".sidebar-context-header")).toBeInTheDocument();
    fireEvent.click(createAgent);
    expect(screen.getByRole("dialog", { name: "Create an agent" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(agentSidebar.querySelectorAll(".agent-sidebar-item .agent-avatar")).toHaveLength(2);
    expect(agentSidebar).toHaveTextContent("Reviewer");
    expect(agentSidebar).toHaveTextContent("Read only");
    expect(screen.getByRole("heading", { name: "Agents", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Customize avatar" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "AGENT.md instructions" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Agent slug/ })).not.toBeInTheDocument();
    fireEvent.input(screen.getByRole("textbox", { name: "Display name" }), {
      target: { value: "Navigator" },
    });
    fireEvent.input(screen.getByRole("textbox", { name: "Instructions" }), {
      target: { value: "Navigate this codebase carefully." },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "shell" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pin" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/default",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          name: "Navigator",
          instructions: "Navigate this codebase carefully.",
          tools: { allow: ["read_file", "write_file"], deny: ["shell"] },
          skills: { allow: [], deny: [], pin: ["testing"] },
        }),
      }),
    ));
    await screen.findByRole("heading", { name: "Navigator", level: 1 });
    expect(agentSidebar).toHaveTextContent("Navigator");

    expect(screen.queryByRole("heading", { name: "Appearance" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit Navigator appearance" }));
    expect(screen.getByRole("dialog", { name: "Customize Navigator" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Hex/ }));
    fireEvent.click(screen.getByRole("button", { name: /Pill/ }));
    fireEvent.click(screen.getByRole("button", { name: "Use #db2777" }));
    fireEvent.click(screen.getByRole("button", { name: "Save avatar" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/default",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ avatar: { shape: "hex", eyes: "pill", face: "solid", color: "#db2777" } }),
      }),
    ));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("only allows non-default agents to be deleted and confirms retirement", async () => {
    window.history.replaceState({}, "", "/agents/reviewer");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("heading", { name: "Reviewer", level: 1 })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("main")).getByRole("button", { name: "Delete Reviewer" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("retires the agent capsule");
    fireEvent.click(screen.getByRole("button", { name: "Delete agent" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/reviewer",
      expect.objectContaining({ method: "DELETE" }),
    ));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: /Reviewer/ }),
    ).not.toBeInTheDocument());
    expect(await screen.findByRole("heading", { name: "Hames", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Hames" })).not.toBeInTheDocument();
  });

  it("surfaces a retryable offline state", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("connection refused"))
      .mockImplementation(successfulFetch());
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("connection refused");
    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    await waitFor(() => expect(screen.getAllByText("Connected").length).toBeGreaterThan(0));
  });

  it("distinguishes an expired browser session from an offline gateway", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { error: { code: "web_session_required", message: "session expired" } },
          401,
        ),
      ),
    );
    render(() => <App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Run hames web to reconnect securely",
    );
    expect(screen.queryByRole("button", { name: "Retry connection" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Reopen Hames Web").length).toBeGreaterThan(0);
  });
});
