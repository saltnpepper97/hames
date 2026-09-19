export interface WebBootstrap {
  protocol_version: number;
  gateway_protocol_version: number;
  working_directory: string;
  csrf_token: string;
}

export interface Workspace {
  id: string;
  path: string;
  title: string;
  created_at: string;
  updated_at: string;
  available: boolean;
}

export interface DirectoryEntry {
  name: string;
  path: string;
}

export interface DirectoryListing {
  path: string;
  parent: string | null;
  directories: DirectoryEntry[];
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
  parent_session_id?: string | null;
  fork_event_id?: string | null;
  lineage_kind?: string;
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
  pinned: boolean;
}

export interface ContextUsage {
  provider: string;
  model: string;
  agent_id: string;
  estimated_input_tokens: number;
  context_window_tokens: number;
  input_budget_tokens: number;
  output_reserve_tokens: number;
  context_window_source: string;
}

export interface ContextSource {
  source_id: string;
  source_type: string;
  content_hash: string;
  priority: number;
  estimated_tokens: number;
  selected_tokens: number;
  visibility: string;
  truncation: string;
  reason: string;
  event_ids: string[];
  origin: string;
  source_path: string;
  memory_id: string;
  memory_layer: string;
  memory_visibility: string;
  skill_id: string;
  skill_version_id: string;
  skill_slug: string;
  skill_version: number;
  skill_scope: string;
}

export interface ContextManifest extends ContextUsage {
  compiler_version: number;
  estimator_version: string;
  reasoning_effort: string;
  selected_sources: ContextSource[];
  omitted_sources: ContextSource[];
  source_order: string[];
  contributing_event_ids: string[];
  request_hash: string;
  request_snapshot_blob_hash: string;
  agent_capsule_hash: string;
  agent_capsule_path: string;
  agent_origin: string;
}

export interface ContextInspection {
  event_id: string;
  session_id: string;
  run_id: string;
  manifest: ContextManifest;
  request_snapshot: Record<string, unknown>;
}

export interface DailyUsage {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  provider_reported_cost: number;
  model_requests: number;
}

export interface AccountUsageWindow {
  used: number;
  remaining: number;
  reset_at: number | string | null;
  window_minutes: number | null;
}

export interface AccountRateLimits {
  plan_type?: string | null;
  limit_id?: string | null;
  observed_at?: number;
  sliding_window_5h?: AccountUsageWindow;
  weekly_window?: AccountUsageWindow;
  primary?: AccountUsageWindow;
  secondary?: AccountUsageWindow;
}

export interface SessionUsage {
  estimated_input_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  provider_reported_cost: number;
  model_requests: number;
  latest_context: ContextUsage | null;
  account_rate_limits: AccountRateLimits | null;
  account_rate_limits_error: string;
  grok_account_usage?: { label: string; window: AccountUsageWindow; observed_at: number } | null;
  grok_account_usage_error?: string;
  grok_account_configured?: boolean;
  daily_activity?: DailyUsage[];
}

export type AgentAvatarShape = "circle" | "square" | "triangle" | "cloud" | "hex" | "drop";
export type AgentAvatarEyes = "dots" | "visor" | "pill";
export type AgentAvatarFace = "solid" | "none";
export type AgentAuthority = "standard" | "read_only";

export interface AgentAvatarConfig {
  shape: AgentAvatarShape;
  eyes: AgentAvatarEyes;
  face: AgentAvatarFace;
  color: string;
}

export interface AgentPublic {
  slug?: string;
  id: string;
  name: string;
  authority: AgentAuthority;
  path: string;
  content_hash: string;
  avatar: AgentAvatarConfig | null;
}

export interface AgentCreate {
  name: string;
  authority: AgentAuthority;
  source: string;
}

export interface AgentDetail extends AgentPublic {
  default_model?: { provider: string; model: string; reasoning_effort: string } | null;
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
  archived: boolean;
}

export interface SkillJob {
  id: string;
  kind: "author" | "patch" | "revalidate";
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "budget_wait";
  session_id: string;
  run_id: string | null;
  source_event_id: string;
  target_skill_id: string | null;
  goal: string;
  scope: SkillScope;
  attempts: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
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

export type PluginCapability = "tool" | "context" | "event";

export interface PluginView {
  id: string;
  name: string;
  enabled: boolean;
  running: boolean;
  version: string;
  fingerprint: string;
  capabilities: PluginCapability[];
  permissions: string[];
  entrypoint: string;
  package_path: string;
  tools: string[];
  warning: string;
}

export interface PluginInspectView {
  id: string;
  name: string;
  version: string;
  fingerprint: string;
  permissions: string[];
  capabilities: PluginCapability[];
  entrypoint: string;
  files: string[];
}

export interface PluginUploadFile {
  path: string;
  data_base64: string;
}

export interface PluginUploadInspection {
  upload_id: string;
  plugin: PluginInspectView;
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
  default_model?: AgentDetail["default_model"];
  name: string;
  instructions: string;
  tools: AgentAccessUpdate;
  skills: AgentAccessUpdate;
}

export type MemoryLayer = "relationship" | "semantic" | "episodic";
export type MemoryStatus = "proposed" | "active" | "rejected" | "superseded" | "retracted";
export type MemoryVisibility = "global" | "agent_private" | "workspace" | "session_team";
export type JsonValue = null | boolean | number | string | JsonValue[] | {
  [key: string]: JsonValue;
};
export type MemoryValue = JsonValue;

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

export interface MemoryCreate {
  layer: MemoryLayer;
  visibility: MemoryVisibility;
  subject: string;
  predicate: string;
  value: string;
  summary: string;
}

export type ScarStatus = "candidate" | "open" | "repair_proposed" | "guarded" | "healed" | "regressed" | "dismissed";
export type ScarSeverity = "low" | "medium" | "high";
export type ScarScope = "global" | "workspace" | "agent";

export interface ScarCreate {
  title: string;
  severity: ScarSeverity;
  scope: ScarScope;
  failure_signature: string;
  description: string;
  expected_behavior: string;
}

export interface ScarTrigger {
  workspace_paths: string[];
  agent_ids: string[];
  intent_labels: string[];
  entity_ids: string[];
  tool_error_signatures: string[];
  skill_ids: string[];
  context_signatures: string[];
}

export interface Scar {
  id: string;
  title: string;
  scope: ScarScope;
  status: ScarStatus;
  severity: ScarSeverity;
  failure_signature: string;
  description: string;
  trigger: ScarTrigger;
  expected_behavior: string;
  detection: string;
  owner_agent_id: string | null;
  workspace_path: string | null;
  source_session_id: string;
  source_run_id: string | null;
  repair_layer: string | null;
  repair_reference: string | null;
  last_triggered_at: string;
  successful_guard_count: number;
  regression_count: number;
  dismissed_reason: string | null;
  created_at: string;
  updated_at: string;
  evidence_event_ids: string[];
}

export interface TimelineItem {
  sequence: number;
  event_id: string;
  session_id: string;
  run_id: string | null;
  created_at: string;
  event_type: string;
  channel: string;
  summary: string;
  payload: Record<string, JsonValue>;
}

export interface ScarTransition {
  event_id: string;
  event_type: string;
  previous_status: string | null;
  status: string;
  reason: string;
  created_at: string;
}

export interface ScarRepair {
  id: string;
  version: number;
  repair_layer: string;
  risk: string;
  required_authority: string;
  status: string;
  previous_scar_status: string;
  rationale: string;
  proposal: Record<string, JsonValue>;
  created_by: string;
  created_at: string;
  decided_at: string | null;
}

export interface ScarEvaluation {
  event_id: string;
  repair_id: string;
  kind: string;
  status: string;
  score: number;
  report: Record<string, JsonValue>;
  created_at: string;
}

export interface ScarInspection {
  scar_id: string;
  session_id: string;
  title: string;
  scope: ScarScope;
  status: ScarStatus;
  severity: ScarSeverity;
  detection: string;
  failure_signature: string;
  description: string;
  expected_behavior: string;
  trigger: ScarTrigger;
  repair_layer: string | null;
  repair_reference: string | null;
  successful_guard_count: number;
  regression_count: number;
  created_at: string;
  updated_at: string;
  evidence_timeline: TimelineItem[];
  transitions: ScarTransition[];
  repairs: ScarRepair[];
  evaluations: ScarEvaluation[];
  explanation: string;
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
  input_modalities: string[];
  output_modalities: string[];
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

export interface MessageAttachmentUpload {
  name: string;
  media_type: string;
  data_base64: string;
}

export interface MessageAttachment {
  digest: string;
  name: string;
  media_type: string;
  kind: "image" | "text";
  size: number;
}

export interface CompactionAccepted {
  run_id: string;
  trigger: "manual";
}

export type GoalStatus = "running" | "yielded" | "paused" | "achieved" | "blocked" | "cancelled";

export interface Goal {
  id: string;
  session_id: string;
  objective: string;
  status: GoalStatus;
  step_count: number;
  current_run_id: string | null;
  latest_summary: string;
  latest_evidence: string[];
  latest_signature: string;
  repeated_no_progress: number;
  active_seconds: number;
  active_since: string | null;
  created_at: string;
  updated_at: string;
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
  workspaces: Workspace[];
  selected_workspace?: Workspace;
}

export interface MessageQueueState {
  session_id: string;
  paused: boolean;
  items: { id: string; content: string; position: number; attachments: unknown[] }[];
}
