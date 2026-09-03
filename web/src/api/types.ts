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

export type AgentAvatarShape = "circle" | "square" | "triangle" | "cloud" | "hex";
export type AgentAvatarEyes = "dots" | "visor" | "pill";
export type AgentAvatarFace = "solid" | "none";

export interface AgentAvatarConfig {
  shape: AgentAvatarShape;
  eyes: AgentAvatarEyes;
  face: AgentAvatarFace;
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

export type SkillScope = "global" | "workspace" | "agent";
export type SkillSource = "managed" | "portable" | "builtin";
export type SkillInvocation = "model" | "user" | "both";
export type SkillStatus = "draft" | "verified" | "active" | "stale" | "archived" | "rejected" | "quarantined" | "superseded";

export interface SkillScript {
  id: string;
  path: string;
  interpreter: "python" | "bash";
  description: string;
}

export interface SkillSummary {
  slug: string;
  name: string;
  description: string;
  scope: SkillScope;
}

export interface SkillCatalogEntry extends SkillSummary {
  id: string;
  version_id: string;
  version: number;
  scope_key: string | null;
  status: SkillStatus;
  content_hash: string;
  triggers: string[];
  tools: string[];
  scripts: SkillScript[];
  score: number;
  pinned: boolean;
  invocation: SkillInvocation;
  argument_hint: string;
  source: SkillSource;
}

export interface SkillMetadata {
  id: string;
  name: string;
  description: string;
  version: number;
  scope: string;
  tools: string[];
  triggers: string[];
  requires: string[];
  scripts: SkillScript[];
  invocation: SkillInvocation;
  argument_hint: string;
}

export interface SkillVersion {
  id: string;
  skill_id: string;
  slug: string;
  version: number;
  content_hash: string;
  status: SkillStatus;
  scope: SkillScope;
  scope_key: string | null;
  name: string;
  description: string;
  instructions: string;
  metadata: SkillMetadata;
  package_path: string;
  base_version_id: string | null;
  created_by: string;
  source_session_id: string;
  source_run_id: string | null;
  created_at: string;
  activated_at: string | null;
  last_used_at: string | null;
  pinned: boolean;
}

export interface AgentCapabilities {
  tools: string[];
  skills: SkillSummary[];
}

export interface AgentAccessUpdate {
  allow: string[];
  deny: string[];
  pin?: string[];
}

export interface AgentUpdate {
  name: string;
  instructions: string;
  tools: AgentAccessUpdate;
  skills: AgentAccessUpdate;
}

export type MemoryLayer = "relationship" | "semantic" | "episodic";
export type MemoryStatus = "proposed" | "active" | "rejected" | "superseded" | "retracted";
export type MemoryVisibility = "global" | "agent_private" | "workspace" | "session_team";
export type MemoryValue = null | boolean | number | string | MemoryValue[] | {
  [key: string]: MemoryValue;
};

export interface MemoryAnchor {
  kind: string;
  value: string;
}

export interface MemoryRecord {
  id: string;
  layer: MemoryLayer;
  status: MemoryStatus;
  visibility: MemoryVisibility;
  subject: string;
  predicate: string;
  value: MemoryValue;
  summary: string;
  confidence: number;
  importance: number;
  owner_agent_id: string | null;
  workspace_path: string | null;
  lineage_root_session_id: string | null;
  source_session_id: string;
  source_run_id: string | null;
  origin_kind: "automatic" | "explicit" | "episode";
  valid_from: string | null;
  valid_until: string | null;
  superseded_by_id: string | null;
  created_at: string;
  updated_at: string;
  anchors: MemoryAnchor[];
  provenance_event_ids: string[];
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
