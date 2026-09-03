import type {
  ApiErrorBody,
  DashboardSnapshot,
  GatewayHealth,
  MessageAccepted,
  Session,
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

export function cancelRun(runId: string): Promise<{ cancelled: boolean }> {
  return request<{ cancelled: boolean }>(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export async function loadDashboard(): Promise<DashboardSnapshot> {
  const bootstrap = await request<WebBootstrap>("/_hames/v1/bootstrap");
  csrfToken = bootstrap.csrf_token;
  const [health, sessions] = await Promise.all([
    request<GatewayHealth>("/v1/health"),
    request<Session[]>("/v1/sessions"),
  ]);
  return { bootstrap, health, sessions };
}

export function resetClientForTests(): void {
  csrfToken = "";
}
