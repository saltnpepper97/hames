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

export type AgentAvatarShape = "round" | "square" | "triangle" | "cloud" | "hex";
export type AgentAvatarEyes = "dots" | "visor" | "happy";

export interface AgentAvatarConfig {
  shape: AgentAvatarShape;
  eyes: AgentAvatarEyes;
  color: string;
}

export interface AgentPublic {
  id: string;
  name: string;
  authority: string;
  path: string;
  content_hash: string;
  avatar: AgentAvatarConfig | null;
}

export interface AgentDetail extends AgentPublic {
  source: string;
  instructions: string;
  tools_allow: string[];
  tools_deny: string[];
  skills_allow: string[];
  skills_deny: string[];
  skills_pin: string[];
  delegation_allowed: boolean;
  delegation_targets: string[];
  deprecated_fields: string[];
}

export type SessionMode = "manual" | "auto" | "plan";

export interface ProviderProfile {
  id: string;
  adapter: string;
  endpoint: string;
  configured_model: string;
  default_reasoning_effort: string;
  supported_reasoning_efforts: string[];
}

export interface ProviderModel {
  id: string;
  status: string;
  context_length: number | null;
  parameter_size: string | null;
  quantization: string | null;
  reasoning_supported: boolean | null;
  reasoning_efforts: string[];
}

export interface ProviderProbeError {
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface ProviderProbe {
  id: string;
  adapter: string;
  reachable: boolean;
  models: ProviderModel[];
  error: ProviderProbeError | null;
}

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
