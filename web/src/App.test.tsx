import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { AgentDetail, AgentPublic, MemoryRecord, PluginInspectView, PluginView, Scar, ScarInspection, SkillCatalogEntry, SkillVersion } from "./api/types";

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
  gateway_protocol_version: 37,
  working_directory: "/work/hames",
  csrf_token: "csrf",
};

const health = {
  status: "ok",
  version: "0.0.0",
  protocol_version: 37,
  database_ready: true,
  provider_profiles: ["codex"],
  default_provider: "codex",
  active_runs: 1,
  active_terminals: 2,
  mcp_servers: 1,
  mcp_ready: 1,
  mcp_degraded: 0,
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

function successfulFetch() {
  let currentSession = { ...sessions[0] };
  let currentAgent = { ...defaultAgentDetail };
  let createdSessionCount = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/_hames/v1/bootstrap") return jsonResponse(bootstrap);
    if (path === "/v1/health") return jsonResponse(health);
    if (path === "/v1/sessions?has_messages=true") {
      return jsonResponse([currentSession, ...sessions.slice(1)]);
    }
    if (path === "/v1/agents" && !init?.method) return jsonResponse(agents);
    if (path === "/v1/plugins" && !init?.method) return jsonResponse([installedPlugin]);
    if (path === "/v1/plugins/inspect" && init?.method === "POST") {
      return jsonResponse(inspectedPlugin);
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
    if (path === "/v1/sessions/session-current/scars?limit=200") {
      return jsonResponse(scars);
    }
    if (path === "/v1/sessions/session-current/scars/scar-one/inspection") {
      return jsonResponse(scarInspection);
    }
    if (path === "/v1/sessions/session-current/skills/available") {
      return jsonResponse(skills);
    }
    if (path.startsWith("/v1/sessions/session-current/skills/available/")) {
      const slug = decodeURIComponent(path.split("/").at(-1) ?? "");
      return skillDetails[slug]
        ? jsonResponse(skillDetails[slug])
        : jsonResponse({ error: { message: "not found" } }, 404);
    }
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
          reasoning_supported: true,
          reasoning_efforts: ["low", "medium", "high"],
        }],
        error: null,
      });
    }
    if (path === "/v1/sessions" && init?.method === "POST") {
      createdSessionCount += 1;
      return jsonResponse({
        ...createdSession,
        id: createdSessionCount === 1 ? "session-new" : `session-new-${createdSessionCount}`,
      }, 201);
    }
    if (path === "/v1/sessions/session-current/messages" && init?.method === "POST") {
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
    if (path === "/v1/sessions/session-current/mode" && init?.method === "PUT") {
      currentSession = { ...currentSession, interaction_mode: "plan" };
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
    if (path === "/v1/questions/question-one" && init?.method === "POST") {
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
) {
  return {
    durable: true,
    event: {
      id: `event-${sequence}`,
      sequence,
      session_id: "session-current",
      run_id: runId,
      agent_id: "default",
      type,
      schema_version: 1,
      created_at: "2026-09-02T18:00:00Z",
      causation_id: null,
      correlation_id: null,
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
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders real workspace chats in the contextual sidebar", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    expect(await screen.findByRole("link", { name: /Build the web foundation/ })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Build the web foundation", level: 1 })).toBeInTheDocument();
    const chatSidebar = screen.getByRole("complementary", { name: "Chat sidebar" });
    expect(chatSidebar.querySelector(".context-header .eyebrow")).not.toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.queryByText("Another project")).not.toBeInTheDocument();
    expect(screen.queryByText("Closed workspace chat")).not.toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-icon^="nav."]')).toHaveLength(7);
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

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(document.querySelector(".app-frame")).toHaveClass("sidebar-collapsed");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(document.querySelector(".app-frame")).not.toHaveClass("sidebar-collapsed");

    fireEvent.click(screen.getByRole("link", { name: /Build the web foundation/ }));
    expect(
      await screen.findByRole("heading", { name: "Build the web foundation", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Build the web foundation" }).parentElement)
      .toHaveTextContent("gpt-5.6-sol");
    const chatScroll = document.querySelector("[data-conversation-scroll]");
    expect(chatScroll).toHaveClass("transcript-scroll");
    const chatFrame = chatScroll?.closest(".session-chat");
    expect(chatFrame).toBeInTheDocument();
    expect(chatFrame?.querySelector(".composer-dock")).toBeInTheDocument();
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
    expect(screen.queryByRole("link", { name: /Read the milestone source/ })).not.toBeInTheDocument();
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
    source.emit("user.message", durableEvent("user.message", 1, { content: "Hello", purpose: "turn" }));
    source.emit("run.started", durableEvent("run.started", 2, {}));
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
    source.emit("tool.requested", durableEvent("tool.requested", 3, {
      tool_call_id: "tool-one",
      name: "shell",
      status: "started",
      arguments: { command: "cargo test" },
    }));
    source.emit("tool.completed", durableEvent("tool.completed", 4, {
      tool_call_id: "tool-one",
      name: "shell",
      status: "completed",
      summary: "Tests passed",
      content: "28 tests passed",
    }));

    expect(await screen.findByText("Hello")).toBeInTheDocument();
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
    const toolNode = screen.getByText("shell").closest(".tool-node");
    expect(toolNode).toHaveAttribute("data-state", "success");
    expect(toolNode).toHaveTextContent("Tests passed");
    fireEvent.click(toolNode!.querySelector("summary")!);
    expect(toolNode).toHaveTextContent("Input");
    expect(toolNode).toHaveTextContent("cargo test");
    expect(toolNode).toHaveTextContent("Output");
    expect(toolNode).toHaveTextContent("28 tests passed");
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), {
      target: { value: "Follow up" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
    expect(await screen.findByText("Message sent")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/messages",
      expect.objectContaining({ method: "POST" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) => String(input) === "/v1/sessions?has_messages=true"),
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
        "tabler-icon-list-check",
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
    expect(screen.getByRole("button", { name: "Add attachment" })).toBeDisabled();
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
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    await waitFor(() => expect(window.location.pathname).toBe("/chat/session-new"));
    expect(await screen.findByRole("heading", { name: "What should we work on?" })).toBeInTheDocument();
    expect(document.querySelector(".session-chat")).toHaveClass("fresh");
    expect(screen.queryByRole("heading", { name: "New chat" })).not.toBeInTheDocument();
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
      expect(createCalls).toHaveLength(2);
      expect(window.location.pathname).toBe("/chat/session-new-2");
    });
    expect(await screen.findByRole("textbox", { name: "Message Hames" })).not.toBeDisabled();
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

    fireEvent.click(await screen.findByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/approvals/approval-one",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Proceed/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/questions/question-one",
        expect.objectContaining({ method: "POST" }),
      ),
    );
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

    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    expect(screen.getByRole("dialog", { name: "Add a plugin" })).toBeInTheDocument();
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

  it("switches and persists the dark appearance from Settings", async () => {
    window.history.replaceState({}, "", "/settings");
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    const darkMode = await screen.findByRole("switch", { name: "Dark mode" });
    expect(darkMode).not.toBeChecked();
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
    expect(agentSidebar.querySelectorAll(".agent-sidebar-item .agent-avatar")).toHaveLength(2);
    expect(agentSidebar).toHaveTextContent("Reviewer");
    expect(agentSidebar).toHaveTextContent("Read only");
    expect(screen.getByRole("heading", { name: "Agents", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Customize avatar" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "AGENT.md instructions" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Agent slug/ })).toBeDisabled();
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
