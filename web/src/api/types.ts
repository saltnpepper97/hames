export interface WebBootstrap {
  protocol_version: number;
  gateway_protocol_version: number;
  working_directory: string;
  csrf_token: string;
}

export interface GatewayHealth {
  status: string;
  version: string;
  protocol_version: number;
  database_ready: boolean;
  provider_profiles: string[];
  default_provider: string;
  active_runs: number;
  active_terminals: number;
  mcp_servers: number;
  mcp_ready: number;
  mcp_degraded: number;
}

export interface Session {
  id: string;
  created_at: string;
  status: string;
  title: string | null;
  working_directory: string;
  agent_id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
  interaction_mode: SessionMode;
}

export type SessionMode = "manual" | "auto" | "plan";

export interface HamesEvent {
  id: string;
  sequence: number;
  session_id: string;
  run_id: string | null;
  agent_id: string | null;
  type: string;
  schema_version: number;
  created_at: string;
  causation_id: string | null;
  correlation_id: string | null;
  payload: Record<string, unknown>;
  blob_hash: string | null;
  payload_hash: string;
  redaction_state: string;
}

export interface DurableEventEnvelope {
  durable: true;
  event: HamesEvent;
}

export interface TransientEventEnvelope {
  durable: false;
  session_id: string;
  run_id: string;
  type: string;
  payload: Record<string, unknown>;
}

export type EventEnvelope = DurableEventEnvelope | TransientEventEnvelope;

export interface MessageAccepted {
  submission_id: string;
  replayed: boolean;
  disposition: "started" | "queued";
  run_id: string | null;
  queued: { queue_id: string; position: number } | null;
}

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

export interface DashboardSnapshot {
  bootstrap: WebBootstrap;
  health: GatewayHealth;
  sessions: Session[];
}
