import type {
  AgentAvatarConfig,
  AgentCapabilities,
  AgentDetail,
  AgentPublic,
  AgentUpdate,
  ApiErrorBody,
  DashboardSnapshot,
  GatewayHealth,
  MessageAccepted,
  MemoryLayer,
  MemoryRecord,
  ProviderProbe,
  ProviderProfile,
  Session,
  SessionMode,
  SkillCatalogEntry,
  SkillVersion,
  WebBootstrap,
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

export function sendMessage(sessionId: string, content: string): Promise<MessageAccepted> {
  return request<MessageAccepted>(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({
      submission_id: crypto.randomUUID(),
      content,
      remember: false,
      send_now: false,
      purpose: "turn",
      paste_spans: [],
    }),
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

export function listAvailableSkills(sessionId: string): Promise<SkillCatalogEntry[]> {
  return request<SkillCatalogEntry[]>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/skills/available`,
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
  answer:
    | { selected_option: string; note: string; custom_answer: "" }
    | { selected_option: null; note: ""; custom_answer: string },
): Promise<{ answer: string }> {
  return request<{ answer: string }>(`/v1/questions/${encodeURIComponent(questionId)}`, {
    method: "POST",
    body: JSON.stringify(answer),
  });
}

export async function loadDashboard(): Promise<DashboardSnapshot> {
  const bootstrap = await request<WebBootstrap>("/_hames/v1/bootstrap");
  csrfToken = bootstrap.csrf_token;
  const [health, sessions] = await Promise.all([
    request<GatewayHealth>("/v1/health"),
    request<Session[]>("/v1/sessions?has_messages=true"),
  ]);
  return { bootstrap, health, sessions };
}

export function resetClientForTests(): void {
  csrfToken = "";
}
