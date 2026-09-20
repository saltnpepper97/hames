import type {
  AgentAvatarConfig,
  AgentCapabilities,
  AgentCreate,
  AgentDetail,
  AgentPublic,
  AgentUpdate,
  ApiErrorBody,
  ContextInspection,
  CompactionAccepted,
  DashboardSnapshot,
  GatewayHealth,
  Goal,
  MessageAccepted,
  MessageQueueState,
  MessageAttachmentUpload,
  MemoryLayer,
  MemoryCreate,
  MemoryRecord,
  PluginInspectView,
  PluginUploadFile,
  PluginUploadInspection,
  PluginView,
  ProviderProbe,
  ProviderProfile,
  Scar,
  ScarCreate,
  ScarInspection,
  Session,
  SessionMode,
  SessionUsage,
  SkillCatalogEntry,
  SkillJob,
  SkillVersion,
  WebBootstrap,
  Workspace,
} from "./types";

export class HamesApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;

  constructor(message: string, status = 0, code = "network_error", retryable = true) {
    super(message);
    this.name = "HamesApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

let csrfToken = "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-Hames-CSRF", csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unable to reach Hames";
    throw new HamesApiError(detail);
  }

  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // The status text remains the safe fallback for malformed upstream errors.
    }
    throw new HamesApiError(
      body.error?.message ?? response.statusText ?? "Hames request failed",
      response.status,
      body.error?.code ?? "request_failed",
      body.error?.retryable ?? response.status >= 500,
    );
  }

  return (await response.json()) as T;
}

export function sessionEventStreamUrl(sessionId: string): string {
  const parameters = new URLSearchParams({ session_id: sessionId });
  return `/v1/events?${parameters.toString()}`;
}

export function createSession(workingDirectory: string): Promise<Session> {
  return request<Session>("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ working_directory: workingDirectory }),
  });
}

export function listWorkspaces(): Promise<Workspace[]> {
  return request<Workspace[]>("/v1/workspaces");
}

export function createWorkspace(path: string, title?: string): Promise<Workspace> {
  return request<Workspace>("/v1/workspaces", {
    method: "POST",
    body: JSON.stringify({ path, title: title?.trim() || null }),
  });
}

export function renameWorkspace(workspaceId: string, title: string): Promise<Workspace> {
  return request<Workspace>(`/v1/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteWorkspace(workspaceId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/v1/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "DELETE",
  });
}

export function selectDirectory(initialPath?: string): Promise<{ path: string } | null> {
  return request<{ path: string } | null>("/v1/directories/select", {
    method: "POST",
    body: JSON.stringify({ initial_path: initialPath || null }),
  });
}

export function pickDirectory(initialPath?: string): Promise<Workspace | null> {
  return request<Workspace | null>("/v1/directories/pick", {
    method: "POST",
    body: JSON.stringify({ initial_path: initialPath || null }),
  });
}

export function getSession(sessionId: string): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}`);
}

export function getSessionUsage(sessionId: string): Promise<SessionUsage> {
  return request<SessionUsage>(`/v1/sessions/${encodeURIComponent(sessionId)}/usage`);
}

export function getPooledUsage(): Promise<SessionUsage> {
  return request<SessionUsage>("/v1/usage");
}

export function inspectContext(eventId: string): Promise<ContextInspection> {
  return request<ContextInspection>(`/v1/contexts/${encodeURIComponent(eventId)}`);
}

export function closeSession(sessionId: string): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export function updateSessionPinned(sessionId: string, pinned: boolean): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}/pinned`, {
    method: "PUT",
    body: JSON.stringify({ pinned }),
  });
}

function submitMessage(
  sessionId: string,
  content: string,
  purpose: "turn" | "heal" | "plan_note" = "turn",
  attachments: MessageAttachmentUpload[] = [],
): Promise<MessageAccepted> {
  const attachmentPayload = attachments.length > 0 ? { attachments } : {};
  return request<MessageAccepted>(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({
      submission_id: crypto.randomUUID(),
      content,
      remember: false,
      send_now: false,
      purpose,
      paste_spans: [],
      ...attachmentPayload,
    }),
  });
}

export function sendMessage(
  sessionId: string,
  content: string,
  attachments: MessageAttachmentUpload[] = [],
): Promise<MessageAccepted> {
  return submitMessage(sessionId, content, "turn", attachments);
}

export function trustSession(
  sessionId: string,
): Promise<{ path: string; trusted: boolean; grant_id?: string | null }> {
  return request(`/v1/sessions/${encodeURIComponent(sessionId)}/trust`, { method: "PUT" });
}

export function updateSessionTitle(sessionId: string, title: string): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: "PUT",
    body: JSON.stringify({ title }),
  });
}

export function healScars(sessionId: string): Promise<MessageAccepted> {
  return submitMessage(sessionId, "Heal behavioral scars now.", "heal");
}

export function dreamSession(sessionId: string): Promise<{ dream_id: string }> {
  return request(`/v1/sessions/${encodeURIComponent(sessionId)}/dream`, { method: "POST" });
}

export function compactSession(sessionId: string): Promise<CompactionAccepted> {
  return request<CompactionAccepted>(`/v1/sessions/${encodeURIComponent(sessionId)}/compact`, {
    method: "POST",
  });
}

export function forkSession(sessionId: string, at?: string): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}/fork`, {
    method: "POST",
    body: JSON.stringify({ at: at || null, title: null, agent_id: null }),
  });
}

export function getCurrentGoal(sessionId: string): Promise<Goal | null> {
  return request<Goal | null>(`/v1/sessions/${encodeURIComponent(sessionId)}/goals/current`);
}

export function createGoal(sessionId: string, objective: string): Promise<Goal> {
  return request<Goal>(`/v1/sessions/${encodeURIComponent(sessionId)}/goals`, {
    method: "POST",
    body: JSON.stringify({ objective }),
  });
}

export function pauseGoal(sessionId: string): Promise<Goal> {
  return request<Goal>(`/v1/sessions/${encodeURIComponent(sessionId)}/goals/current/pause`, {
    method: "POST",
  });
}

export function resumeGoal(sessionId: string): Promise<Goal> {
  return request<Goal>(`/v1/sessions/${encodeURIComponent(sessionId)}/goals/current/resume`, {
    method: "POST",
  });
}

export function cancelGoal(sessionId: string): Promise<Goal> {
  return request<Goal>(`/v1/sessions/${encodeURIComponent(sessionId)}/goals/current/cancel`, {
    method: "POST",
  });
}

export function stopBackgroundTerminals(sessionId: string): Promise<{ closed: number }> {
  return request<{ closed: number }>(`/v1/sessions/${encodeURIComponent(sessionId)}/terminals`, {
    method: "DELETE",
  });
}

export function updateSessionMode(sessionId: string, mode: SessionMode): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}/mode`, {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });
}

export function updateSessionSelection(
  sessionId: string,
  provider: string,
  model: string,
  reasoningEffort: string,
): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({
      provider,
      model,
      reasoning_effort: reasoningEffort,
    }),
  });
}

export function listProviders(): Promise<ProviderProfile[]> {
  return request<ProviderProfile[]>("/v1/providers");
}

export function listAgents(): Promise<AgentPublic[]> {
  return request<AgentPublic[]>("/v1/agents");
}

export function createAgent(agent: AgentCreate): Promise<AgentDetail> {
  return request<AgentDetail>("/v1/agents", {
    method: "POST",
    body: JSON.stringify(agent),
  });
}

export function updateSessionAgent(sessionId: string, agentId: string): Promise<Session> {
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}/agent`, {
    method: "PUT",
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export function recentSession(workingDirectory: string): Promise<Session | null> {
  const parameters = new URLSearchParams({
    working_directory: workingDirectory,
    active_within_seconds: "31536000",
  });
  return request<Session | null>(`/v1/sessions/recent?${parameters.toString()}`);
}

export function listMemories(
  sessionId: string,
  layer: MemoryLayer,
  offset = 0,
  limit = 200,
): Promise<MemoryRecord[]> {
  const parameters = new URLSearchParams({
    status: "active",
    layer,
    limit: String(limit),
    offset: String(offset),
  });
  return request<MemoryRecord[]>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/memories?${parameters.toString()}`,
  );
}

export function createMemory(sessionId: string, memory: MemoryCreate): Promise<MemoryRecord> {
  return request<MemoryRecord>(`/v1/sessions/${encodeURIComponent(sessionId)}/memories`, {
    method: "POST",
    body: JSON.stringify(memory),
  });
}

export function deleteMemory(
  sessionId: string,
  memoryId: string,
): Promise<{ memory_id: string; deleted: boolean }> {
  return request<{ memory_id: string; deleted: boolean }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/memories/${encodeURIComponent(memoryId)}`,
    { method: "DELETE" },
  );
}

export function retireAgent(agentId: string): Promise<{ retired_to: string }> {
  return request<{ retired_to: string }>(`/v1/agents/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
}

export function deleteScar(
  sessionId: string,
  scarId: string,
): Promise<{ scar_id: string; deleted: boolean }> {
  return request<{ scar_id: string; deleted: boolean }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/scars/${encodeURIComponent(scarId)}`,
    { method: "DELETE" },
  );
}

export function listAvailableSkills(sessionId: string): Promise<SkillCatalogEntry[]> {
  return request<SkillCatalogEntry[]>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skills/available`,
  );
}

export function deleteSkill(
  sessionId: string,
  slug: string,
): Promise<{ skill_id: string; slug: string; deleted: boolean }> {
  return request<{ skill_id: string; slug: string; deleted: boolean }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skills/${encodeURIComponent(slug)}`,
    { method: "DELETE" },
  );
}

export function authorSkill(
  sessionId: string,
  goal: string,
  scope: "workspace" | "agent",
): Promise<SkillJob> {
  return request<SkillJob>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skills/author`,
    {
      method: "POST",
      body: JSON.stringify({ goal, scope, target_skill_id: null }),
    },
  );
}

export function listSkillJobs(sessionId: string): Promise<SkillJob[]> {
  return request<SkillJob[]>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skill-jobs`,
  );
}

export function listScars(sessionId: string): Promise<Scar[]> {
  return request<Scar[]>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/scars?limit=200`,
  );
}

export function createScar(sessionId: string, scar: ScarCreate): Promise<Scar> {
  return request<Scar>(`/v1/sessions/${encodeURIComponent(sessionId)}/scars`, {
    method: "POST",
    body: JSON.stringify(scar),
  });
}

export function listPlugins(): Promise<PluginView[]> {
  return request<PluginView[]>("/v1/plugins");
}

export function inspectPlugin(path: string): Promise<PluginInspectView> {
  return request<PluginInspectView>("/v1/plugins/inspect", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function inspectPluginUpload(files: PluginUploadFile[]): Promise<PluginUploadInspection> {
  return request<PluginUploadInspection>("/v1/plugins/uploads", {
    method: "POST",
    body: JSON.stringify({ files }),
  });
}

export function installPluginUpload(uploadId: string): Promise<PluginView> {
  return request<PluginView>(`/v1/plugins/uploads/${encodeURIComponent(uploadId)}/install`, {
    method: "POST",
  });
}

export function discardPluginUpload(uploadId: string): Promise<{ discarded: boolean }> {
  return request<{ discarded: boolean }>(`/v1/plugins/uploads/${encodeURIComponent(uploadId)}`, {
    method: "DELETE",
  });
}

export function installPlugin(path: string): Promise<PluginView> {
  return request<PluginView>("/v1/plugins/install", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function enablePlugin(pluginId: string): Promise<PluginView> {
  return request<PluginView>(`/v1/plugins/${encodeURIComponent(pluginId)}/enable`, {
    method: "POST",
  });
}

export function disablePlugin(pluginId: string): Promise<PluginView> {
  return request<PluginView>(`/v1/plugins/${encodeURIComponent(pluginId)}/disable`, {
    method: "POST",
  });
}

export function removePlugin(pluginId: string): Promise<{ removed: boolean }> {
  return request<{ removed: boolean }>(`/v1/plugins/${encodeURIComponent(pluginId)}`, {
    method: "DELETE",
  });
}

export function inspectScar(sessionId: string, scarId: string): Promise<ScarInspection> {
  return request<ScarInspection>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/scars/${encodeURIComponent(scarId)}/inspection`,
  );
}

export function getAvailableSkill(sessionId: string, slug: string): Promise<SkillVersion> {
  return request<SkillVersion>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skills/available/${encodeURIComponent(slug)}`,
  );
}

export function getAgent(agentId: string): Promise<AgentDetail> {
  return request<AgentDetail>(`/v1/agents/${encodeURIComponent(agentId)}`);
}

export function getAgentCapabilities(
  agentId: string,
  workingDirectory: string,
): Promise<AgentCapabilities> {
  const parameters = new URLSearchParams({ working_directory: workingDirectory });
  return request<AgentCapabilities>(
    `/v1/agents/${encodeURIComponent(agentId)}/capabilities?${parameters.toString()}`,
  );
}

export function updateAgent(agentId: string, update: AgentUpdate): Promise<AgentDetail> {
  return request<AgentDetail>(`/v1/agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function updateAgentAvatar(
  agentId: string,
  avatar: AgentAvatarConfig,
): Promise<AgentDetail> {
  return request<AgentDetail>(`/v1/agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    body: JSON.stringify({ avatar }),
  });
}

export function probeProvider(providerId: string): Promise<ProviderProbe> {
  return request<ProviderProbe>(`/v1/providers/${encodeURIComponent(providerId)}/probe`, {
    method: "POST",
  });
}

export function cancelRun(runId: string): Promise<{ cancelled: boolean }> {
  return request<{ cancelled: boolean }>(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export function resolveApproval(
  approvalId: string,
  requestHash: string,
  decision: "approved" | "approved_session" | "denied",
): Promise<{ status: string }> {
  return request<{ status: string }>(`/v1/approvals/${encodeURIComponent(approvalId)}`, {
    method: "POST",
    body: JSON.stringify({ decision, request_hash: requestHash }),
  });
}

export function answerQuestion(
  questionId: string,
  answer: {
    selected_option: string | null;
    selected_options: string[];
    note: string;
    custom_answer: string;
  },
): Promise<{ answer: string }> {
  return request<{ answer: string }>(`/v1/questions/${encodeURIComponent(questionId)}`, {
    method: "POST",
    body: JSON.stringify(answer),
  });
}

export async function loadDashboard(selectedWorkspaceId = ""): Promise<DashboardSnapshot> {
  const bootstrap = await request<WebBootstrap>("/_hames/v1/bootstrap");
  csrfToken = bootstrap.csrf_token;
  const [health, workspaces] = await Promise.all([
    request<GatewayHealth>("/v1/health"),
    listWorkspaces(),
  ]);
  const selectedWorkspace = workspaces.find((workspace) => workspace.id === selectedWorkspaceId)
    ?? workspaces.find((workspace) => workspace.available)
    ?? workspaces[0];
  const parameters = new URLSearchParams({
    has_messages: "true",
    include_titled: "true",
    registered_workspaces_only: "true",
    include_delegated: "false",
  });
  const sessions = await request<Session[]>(`/v1/sessions?${parameters.toString()}`);
  return { bootstrap, health, sessions, workspaces, selected_workspace: selectedWorkspace };
}

export function resetClientForTests(): void {
  csrfToken = "";
}

export const getMessageQueue = (sessionId: string) =>
  request<MessageQueueState>(`/v1/sessions/${encodeURIComponent(sessionId)}/queue`);
export const removeQueuedMessage = (sessionId: string, queueId: string) =>
  request<MessageQueueState>(`/v1/sessions/${encodeURIComponent(sessionId)}/queue/${encodeURIComponent(queueId)}`, { method: "DELETE" });
export const sendQueuedMessageNow = (sessionId: string, queueId: string) =>
  request<MessageAccepted>(`/v1/sessions/${encodeURIComponent(sessionId)}/queue/${encodeURIComponent(queueId)}/send-now`, { method: "POST" });
export const resumeMessageQueue = (sessionId: string) =>
  request<MessageQueueState>(`/v1/sessions/${encodeURIComponent(sessionId)}/queue/resume`, { method: "POST" });

export const editQueuedMessage = (sessionId: string, queueId: string, content: string, expectedContent: string) =>
  request<MessageQueueState>(`/v1/sessions/${encodeURIComponent(sessionId)}/queue/${encodeURIComponent(queueId)}`, {
    method: "PATCH", body: JSON.stringify({ content, expected_content: expectedContent }),
  });

export async function executeUserCommand(sessionId: string, name: string, note = ""): Promise<Session> {
  await request(`/v1/sessions/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
  return request<Session>(`/v1/sessions/${encodeURIComponent(sessionId)}`);
}

export interface ProviderConnection {
  id: string;
  name: string;
  status: "not_connected" | "not_checked" | "connected" | "unavailable";
  models: string[];
  model_source: string;
  can_connect: boolean;
  can_disconnect: boolean;
  configured: boolean;
  source: string;
  key_url: string;
}

export const listConnections = () => request<ProviderConnection[]>("/v1/connections");
export const connectProvider = (id: string, key: string) => request<ProviderConnection[]>(
  `/v1/connections/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ key }) },
);
export const testConnection = (id: string) => request<ProviderConnection[]>(
  `/v1/connections/${encodeURIComponent(id)}/test`, { method: "POST" },
);
export const disconnectProvider = (id: string) => request<ProviderConnection[]>(
  `/v1/connections/${encodeURIComponent(id)}`, { method: "DELETE" },
);

export const sendPlanFeedback = (sessionId: string, content: string) => submitMessage(sessionId, content, "plan_note");
export const getCurrentPlan = (sessionId: string) => request<{ current: { id: string; status: string } | null }>(`/v1/sessions/${encodeURIComponent(sessionId)}/plans/current`);
export const executePlan = (sessionId: string) => request<{ run_id: string }>(`/v1/sessions/${encodeURIComponent(sessionId)}/plans/current/execute`, {
  method: "POST", body: JSON.stringify({ strategy: "keep" }),
});

export function getDelegatedSessions(): Promise<Session[]> {
  return request<Session[]>("/v1/sessions");
}

export interface UserCommand { name: string; description: string; agent: string; source: string; action: "execute_plan"; }
export const listUserCommands = (sessionId: string) => request<UserCommand[]>(`/v1/sessions/${encodeURIComponent(sessionId)}/commands`);

export interface AutomationSpec {
  title: string; instructions: string; working_directory: string; agent_id: string;
  provider: string; model: string; reasoning_effort: string;
  frequency: "once" | "daily" | "weekly"; time: string; timezone: string;
  weekdays: number[]; date: string; enabled: boolean; catch_up: boolean;
  retries: number; notify: "results" | "failures" | "off";
}
export interface Automation extends AutomationSpec { id: string; next_run: string | null; updated_at: string }
export interface AutomationRun {
  id: string; automation_id: string; status: string; attempt: number; due_at: string;
  session_id: string | null; run_id: string | null; message: string; created_at: string; finished_at: string | null;
}
export interface AutomationCatalog { items: Automation[]; runs: AutomationRun[]; native_notifications: boolean }
export const listAutomations = () => request<AutomationCatalog>("/v1/automations");
export const saveAutomation = (spec: AutomationSpec, id?: string) => request<Automation>(
  id ? `/v1/automations/${encodeURIComponent(id)}` : "/v1/automations",
  { method: id ? "PUT" : "POST", body: JSON.stringify(spec) });
export const runAutomation = (id: string) => request(`/v1/automations/${encodeURIComponent(id)}/run`, { method: "POST" });
export const deleteAutomation = (id: string) => request(`/v1/automations/${encodeURIComponent(id)}`, { method: "DELETE" });

// A flow is a saved recipe for the normal coordinator chat.

export const controlWorker = (sessionId: string, action: "stop" | "return" | "hold") => request<{ accepted: boolean }>(
  `/v1/sessions/${encodeURIComponent(sessionId)}/worker/${action}`, { method: "POST" });
