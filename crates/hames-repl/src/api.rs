use std::fs;

use anyhow::{Context, Result, bail};
use reqwest::{Client, Response, StatusCode};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;

use crate::local::LocalPaths;

pub const PROTOCOL_VERSION: u32 = 23;
pub const HEAL_SCARS_PROMPT: &str = "Heal behavioral scars now.";

#[derive(Clone)]
pub struct GatewayClient {
    base_url: String,
    token: String,
    http: Client,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Health {
    pub status: String,
    pub version: String,
    pub protocol_version: u32,
    pub database_ready: bool,
    pub provider_profiles: Vec<String>,
    pub default_provider: String,
    pub active_runs: u64,
    #[serde(default)]
    pub search: Option<SearchRuntimeStatus>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SearchRuntimeStatus {
    pub service: SearchServiceStatus,
    pub mcp_status: String,
    pub protocol_version: String,
    pub error: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SearchServiceStatus {
    pub status: String,
    pub runtime: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderProfile {
    pub id: String,
    pub adapter: String,
    pub endpoint: String,
    pub configured_model: String,
    pub default_reasoning_effort: String,
    pub supported_reasoning_efforts: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderProbeError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderProbe {
    pub id: String,
    pub adapter: String,
    pub reachable: bool,
    pub models: Vec<ProviderModel>,
    pub error: Option<ProviderProbeError>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderModel {
    pub id: String,
    pub status: String,
    pub context_length: Option<u64>,
    pub parameter_size: Option<String>,
    pub quantization: Option<String>,
    pub reasoning_supported: Option<bool>,
    pub reasoning_efforts: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Session {
    pub id: String,
    pub created_at: String,
    pub status: String,
    pub title: Option<String>,
    pub working_directory: String,
    pub agent_id: String,
    pub provider: String,
    pub model: String,
    pub reasoning_effort: String,
    pub context_window_tokens: u64,
    pub context_window_source: String,
    pub parent_session_id: Option<String>,
    pub fork_event_id: Option<String>,
    pub lineage_kind: String,
    pub delegation_depth: u64,
    pub interaction_mode: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Agent {
    pub id: String,
    pub name: String,
    pub authority: String,
    pub path: String,
    pub content_hash: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AgentDetail {
    #[serde(flatten)]
    pub agent: Agent,
    pub instructions: String,
    pub tools_allow: Vec<String>,
    pub tools_deny: Vec<String>,
    #[serde(default)]
    pub skills_allow: Vec<String>,
    #[serde(default)]
    pub skills_deny: Vec<String>,
    #[serde(default)]
    pub skills_pin: Vec<String>,
    pub delegation_allowed: bool,
    pub delegation_targets: Vec<String>,
    pub deprecated_fields: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Plugin {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub running: bool,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub fingerprint: String,
    #[serde(default)]
    pub permissions: Vec<String>,
    #[serde(default)]
    pub tools: Vec<String>,
    #[serde(default)]
    pub warning: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct InspectedPlugin {
    pub id: String,
    pub name: String,
    pub version: String,
    pub fingerprint: String,
    pub permissions: Vec<String>,
    pub capabilities: Vec<String>,
    pub entrypoint: String,
    pub files: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PluginProposal {
    pub id: String,
    #[serde(default)]
    pub plugin_id: String,
    #[serde(default)]
    pub scar_id: String,
    pub status: String,
    pub package_path: String,
    #[serde(default)]
    pub permissions: Vec<String>,
    pub created_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Event {
    pub id: String,
    pub sequence: u64,
    pub session_id: String,
    pub run_id: Option<String>,
    pub agent_id: Option<String>,
    #[serde(rename = "type")]
    pub event_type: String,
    pub schema_version: u32,
    pub created_at: String,
    pub causation_id: Option<String>,
    pub correlation_id: Option<String>,
    pub payload: Value,
    pub blob_hash: Option<String>,
    pub payload_hash: String,
    pub redaction_state: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct IntegrityResult {
    pub event_id: String,
    pub ok: bool,
    pub payload_hash: String,
    pub blob_hash: Option<String>,
    pub redaction_state: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MessageAccepted {
    pub disposition: String,
    pub run_id: Option<String>,
    pub queued: Option<QueuedMessage>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct CompactionAccepted {
    pub run_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Goal {
    pub id: String,
    pub session_id: String,
    pub objective: String,
    pub status: String,
    pub step_count: usize,
    pub current_run_id: Option<String>,
    pub latest_summary: String,
    pub latest_evidence: Vec<String>,
    pub repeated_no_progress: usize,
    #[serde(default)]
    pub active_seconds: f64,
    #[serde(default)]
    pub active_since: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct QueuedMessage {
    pub id: String,
    pub session_id: String,
    pub content: String,
    pub remember: bool,
    pub paste_spans: Vec<PasteSpan>,
    #[serde(default = "default_turn_purpose")]
    pub purpose: String,
    pub created_at: String,
    pub position: usize,
}

fn default_turn_purpose() -> String {
    "turn".to_owned()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PlanRevision {
    pub id: String,
    pub session_id: String,
    pub revision: usize,
    pub title: String,
    pub markdown: String,
    pub tasks: Vec<String>,
    pub source_run_id: String,
    pub supersedes_plan_id: Option<String>,
    pub status: String,
    pub strategy: Option<String>,
    pub execution_run_id: Option<String>,
    #[serde(default)]
    pub execution_note: String,
    pub error: String,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PlanState {
    pub session_id: String,
    pub current: Option<PlanRevision>,
    pub revisions: Vec<PlanRevision>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SessionTask {
    pub id: String,
    pub text: String,
    pub status: String,
    pub position: usize,
    pub created_by: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SessionTaskList {
    pub session_id: String,
    pub title: String,
    pub revision: usize,
    pub items: Vec<SessionTask>,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct PlanExecutionAccepted {
    pub plan: PlanState,
    pub tasks: SessionTaskList,
    pub run_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct QueueState {
    pub session_id: String,
    pub paused: bool,
    pub items: Vec<QueuedMessage>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct TrustStatus {
    pub path: String,
    pub trusted: bool,
    pub grant_id: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ApprovalResolution {
    pub status: String,
    pub approval_scope: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ContextUsageProjection {
    pub provider: String,
    pub model: String,
    pub agent_id: String,
    pub estimated_input_tokens: u64,
    pub context_window_tokens: u64,
    pub input_budget_tokens: u64,
    pub output_reserve_tokens: u64,
    pub context_window_source: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct UsageProjection {
    pub estimated_input_tokens: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub reasoning_tokens: u64,
    pub provider_reported_cost: f64,
    pub model_requests: u64,
    #[serde(default)]
    pub latest_context: Option<ContextUsageProjection>,
    #[serde(default)]
    pub account_rate_limits: Option<Value>,
    #[serde(default)]
    pub account_rate_limits_error: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ContextSource {
    pub source_id: String,
    pub source_type: String,
    pub content_hash: String,
    pub priority: i64,
    pub estimated_tokens: u64,
    pub selected_tokens: u64,
    pub visibility: String,
    pub truncation: String,
    pub reason: String,
    pub event_ids: Vec<String>,
    pub origin: String,
    pub source_path: String,
    #[serde(default)]
    pub memory_id: String,
    #[serde(default)]
    pub memory_layer: String,
    #[serde(default)]
    pub memory_visibility: String,
    #[serde(default)]
    pub memory_anchors: Vec<MemoryAnchor>,
    #[serde(default)]
    pub retrieval_score: f64,
    #[serde(default)]
    pub provenance_event_ids: Vec<String>,
    #[serde(default)]
    pub skill_id: String,
    #[serde(default)]
    pub skill_version_id: String,
    #[serde(default)]
    pub skill_slug: String,
    #[serde(default)]
    pub skill_version: u64,
    #[serde(default)]
    pub skill_scope: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryAnchor {
    pub kind: String,
    pub value: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryRecord {
    pub id: String,
    pub layer: String,
    pub status: String,
    pub visibility: String,
    pub subject: String,
    pub predicate: String,
    pub value: Value,
    pub summary: String,
    pub confidence: f64,
    pub importance: f64,
    pub owner_agent_id: Option<String>,
    pub workspace_path: Option<String>,
    pub lineage_root_session_id: Option<String>,
    pub source_session_id: String,
    pub source_run_id: Option<String>,
    pub origin_kind: String,
    pub valid_from: Option<String>,
    pub valid_until: Option<String>,
    pub superseded_by_id: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub anchors: Vec<MemoryAnchor>,
    pub provenance_event_ids: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryJob {
    pub id: String,
    pub kind: String,
    pub status: String,
    pub session_id: String,
    pub run_id: Option<String>,
    pub source_event_id: String,
    pub content: Option<String>,
    pub attempts: u64,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Scar {
    pub id: String,
    pub title: String,
    pub scope: String,
    pub status: String,
    pub severity: String,
    pub failure_signature: String,
    pub description: String,
    pub expected_behavior: String,
    pub detection: String,
    #[serde(default)]
    pub repair_layer: Option<String>,
    #[serde(default)]
    pub repair_reference: Option<String>,
    #[serde(default)]
    pub evidence_event_ids: Vec<String>,
    pub last_triggered_at: String,
    pub successful_guard_count: i64,
    pub regression_count: i64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ScarUpdate {
    pub title: String,
    pub severity: String,
    pub description: String,
    pub expected_behavior: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SkillScript {
    pub id: String,
    pub path: String,
    pub interpreter: String,
    pub description: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SkillMetadata {
    pub id: String,
    pub name: String,
    pub description: String,
    pub version: u64,
    pub scope: String,
    pub tools: Vec<String>,
    pub triggers: Vec<String>,
    pub requires: Vec<String>,
    pub scripts: Vec<SkillScript>,
    #[serde(default = "default_skill_invocation")]
    pub invocation: String,
    #[serde(default)]
    pub argument_hint: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SkillSummary {
    pub id: String,
    pub slug: String,
    pub version_id: String,
    pub version: u64,
    pub name: String,
    pub description: String,
    pub scope: String,
    pub scope_key: Option<String>,
    pub status: String,
    pub content_hash: String,
    pub triggers: Vec<String>,
    pub tools: Vec<String>,
    pub scripts: Vec<SkillScript>,
    pub score: f64,
    pub pinned: bool,
    #[serde(default = "default_skill_invocation")]
    pub invocation: String,
    #[serde(default)]
    pub argument_hint: String,
}

fn default_skill_invocation() -> String {
    "model".to_owned()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SkillVersion {
    pub id: String,
    pub skill_id: String,
    pub slug: String,
    pub version: u64,
    pub content_hash: String,
    pub status: String,
    pub scope: String,
    pub scope_key: Option<String>,
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub metadata: SkillMetadata,
    pub package_path: String,
    pub base_version_id: Option<String>,
    pub created_by: String,
    pub source_session_id: String,
    pub source_run_id: Option<String>,
    pub created_at: String,
    pub activated_at: Option<String>,
    pub last_used_at: Option<String>,
    pub pinned: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SkillJob {
    pub id: String,
    pub kind: String,
    pub status: String,
    pub session_id: String,
    pub run_id: Option<String>,
    pub source_event_id: String,
    pub target_skill_id: Option<String>,
    pub goal: String,
    pub scope: String,
    pub attempts: u64,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ContextManifest {
    pub compiler_version: u32,
    pub estimator_version: String,
    pub provider: String,
    pub model: String,
    pub reasoning_effort: String,
    pub context_window_tokens: u64,
    pub context_window_source: String,
    pub input_budget_tokens: u64,
    pub output_reserve_tokens: u64,
    pub estimated_input_tokens: u64,
    pub selected_sources: Vec<ContextSource>,
    pub omitted_sources: Vec<ContextSource>,
    pub source_order: Vec<String>,
    pub contributing_event_ids: Vec<String>,
    pub request_hash: String,
    pub request_snapshot_blob_hash: String,
    pub agent_id: String,
    pub agent_capsule_hash: String,
    pub agent_capsule_path: String,
    pub agent_origin: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ContextInspection {
    pub event_id: String,
    pub session_id: String,
    pub run_id: String,
    pub manifest: ContextManifest,
    pub request_snapshot: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RunSummary {
    pub run_id: String,
    pub session_id: String,
    pub status: String,
    pub started_at: Option<String>,
    pub completed_at: Option<String>,
    pub model_requests: u64,
    pub tool_calls: u64,
    pub usage: UsageProjection,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TimelineItem {
    pub sequence: u64,
    pub event_id: String,
    pub session_id: String,
    pub run_id: Option<String>,
    pub created_at: String,
    pub event_type: String,
    pub channel: String,
    pub summary: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RunInspection {
    pub run_id: String,
    pub session_id: String,
    pub status: String,
    pub started_at: Option<String>,
    pub completed_at: Option<String>,
    pub model_requests: u64,
    pub tool_calls: u64,
    pub usage: UsageProjection,
    pub timeline: Vec<TimelineItem>,
    pub contexts: Vec<ContextInspection>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct LiveEnvelope {
    pub durable: bool,
    pub event: Option<Event>,
    pub run_id: Option<String>,
    #[serde(rename = "type")]
    pub event_type: Option<String>,
    pub payload: Option<Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PasteSpan {
    pub start_byte: usize,
    pub end_byte: usize,
    pub line_count: usize,
    pub byte_count: usize,
}

#[derive(Serialize)]
struct CreateSession<'a> {
    working_directory: &'a str,
    agent_id: &'a str,
    provider: &'a str,
    model: &'a str,
    reasoning_effort: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    inherit_session_id: Option<&'a str>,
}

#[derive(Serialize)]
struct UpdateSession<'a> {
    provider: &'a str,
    model: &'a str,
    reasoning_effort: &'a str,
}

#[derive(Serialize)]
struct UpdateSessionAgent<'a> {
    agent_id: &'a str,
}

#[derive(Serialize)]
struct UpdateSessionMode<'a> {
    mode: &'a str,
}

#[derive(Serialize)]
struct UpdateSessionTitle<'a> {
    title: &'a str,
}

#[derive(Serialize)]
struct ForkSession<'a> {
    at: Option<&'a str>,
    title: Option<&'a str>,
    agent_id: Option<&'a str>,
}

#[derive(Serialize)]
struct CreateAgent<'a> {
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<&'a str>,
    authority: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    source: Option<&'a str>,
}

#[derive(Serialize)]
struct PluginPath<'a> {
    path: &'a str,
}

#[derive(Serialize)]
struct SubmitCorrection<'a> {
    content: &'a str,
}

impl GatewayClient {
    pub fn from_paths(paths: &LocalPaths) -> Result<Self> {
        let token = fs::read_to_string(&paths.token)
            .with_context(|| format!("failed to read {}", paths.token.display()))?;
        Ok(Self {
            base_url: paths.gateway_url()?,
            token: token.trim().to_owned(),
            http: Client::builder().build()?,
        })
    }

    pub async fn health_unauthenticated(base_url: &str) -> Result<Health> {
        let response = Client::new()
            .get(format!("{base_url}/v1/health"))
            .send()
            .await?;
        decode(response).await
    }

    pub async fn health(&self) -> Result<Health> {
        decode(self.get("/v1/health").send().await?).await
    }

    pub async fn token_accepted(&self) -> Result<bool> {
        let response = self.get("/v1/providers").send().await?;
        if response.status() == StatusCode::UNAUTHORIZED {
            return Ok(false);
        }
        ensure_success(response).await?;
        Ok(true)
    }

    pub async fn providers(&self) -> Result<Vec<ProviderProfile>> {
        decode(self.get("/v1/providers").send().await?).await
    }

    pub async fn probe_provider(&self, profile_id: &str) -> Result<ProviderProbe> {
        decode(
            self.post(&format!("/v1/providers/{profile_id}/probe"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn create_session(
        &self,
        working_directory: &str,
        agent_id: &str,
        provider: &str,
        model: &str,
        reasoning_effort: &str,
    ) -> Result<Session> {
        decode(
            self.post("/v1/sessions")
                .json(&CreateSession {
                    working_directory,
                    agent_id,
                    provider,
                    model,
                    reasoning_effort,
                    inherit_session_id: None,
                })
                .send()
                .await?,
        )
        .await
    }

    pub async fn create_session_from(
        &self,
        working_directory: &str,
        session_id: &str,
    ) -> Result<Session> {
        decode(
            self.post("/v1/sessions")
                .json(&CreateSession {
                    working_directory,
                    agent_id: "default",
                    provider: "",
                    model: "",
                    reasoning_effort: "",
                    inherit_session_id: Some(session_id),
                })
                .send()
                .await?,
        )
        .await
    }

    pub async fn update_session(
        &self,
        session_id: &str,
        provider: &str,
        model: &str,
        reasoning_effort: &str,
    ) -> Result<Session> {
        decode(
            self.http
                .patch(format!("{}/v1/sessions/{session_id}", self.base_url))
                .bearer_auth(&self.token)
                .json(&UpdateSession {
                    provider,
                    model,
                    reasoning_effort,
                })
                .send()
                .await?,
        )
        .await
    }

    pub async fn close_session(&self, session_id: &str) -> Result<Session> {
        decode(
            self.http
                .delete(format!("{}/v1/sessions/{session_id}", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn update_session_agent(&self, session_id: &str, agent_id: &str) -> Result<Session> {
        decode(
            self.http
                .put(format!("{}/v1/sessions/{session_id}/agent", self.base_url))
                .bearer_auth(&self.token)
                .json(&UpdateSessionAgent { agent_id })
                .send()
                .await?,
        )
        .await
    }

    pub async fn update_session_mode(&self, session_id: &str, mode: &str) -> Result<Session> {
        decode(
            self.http
                .put(format!("{}/v1/sessions/{session_id}/mode", self.base_url))
                .bearer_auth(&self.token)
                .json(&UpdateSessionMode { mode })
                .send()
                .await?,
        )
        .await
    }

    pub async fn update_session_title(&self, session_id: &str, title: &str) -> Result<Session> {
        decode(
            self.http
                .put(format!("{}/v1/sessions/{session_id}/title", self.base_url))
                .bearer_auth(&self.token)
                .json(&UpdateSessionTitle { title })
                .send()
                .await?,
        )
        .await
    }

    pub async fn sessions(&self) -> Result<Vec<Session>> {
        decode(self.get("/v1/sessions").send().await?).await
    }

    #[allow(dead_code)] // Consumed by the Ratatui entrypoint in the next implementation slice.
    pub async fn recent_session(
        &self,
        working_directory: &str,
        active_within_seconds: u64,
    ) -> Result<Option<Session>> {
        let active_within_seconds = active_within_seconds.to_string();
        decode(
            self.get("/v1/sessions/recent")
                .query(&[
                    ("working_directory", working_directory),
                    ("active_within_seconds", &active_within_seconds),
                ])
                .send()
                .await?,
        )
        .await
    }

    pub async fn agents(&self) -> Result<Vec<Agent>> {
        decode(self.get("/v1/agents").send().await?).await
    }

    pub async fn tools(&self) -> Result<Vec<String>> {
        decode(self.get("/v1/tools").send().await?).await
    }

    pub async fn agent(&self, id: &str) -> Result<AgentDetail> {
        decode(self.get(&format!("/v1/agents/{id}")).send().await?).await
    }

    pub async fn create_agent(
        &self,
        name: Option<&str>,
        authority: &str,
        source: Option<&str>,
    ) -> Result<AgentDetail> {
        decode(
            self.post("/v1/agents")
                .json(&CreateAgent {
                    name,
                    authority,
                    source,
                })
                .send()
                .await?,
        )
        .await
    }

    pub async fn validate_agent(&self, id: &str) -> Result<AgentDetail> {
        decode(
            self.post(&format!("/v1/agents/{id}/validate"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn retire_agent(&self, id: &str) -> Result<Value> {
        decode(
            self.http
                .delete(format!("{}/v1/agents/{id}", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn plugins(&self) -> Result<Vec<Plugin>> {
        decode(self.get("/v1/plugins").send().await?).await
    }

    pub async fn plugin(&self, id: &str) -> Result<Plugin> {
        decode(self.get(&format!("/v1/plugins/{id}")).send().await?).await
    }

    pub async fn inspect_plugin(&self, path: &str) -> Result<InspectedPlugin> {
        decode(
            self.post("/v1/plugins/inspect")
                .json(&PluginPath { path })
                .send()
                .await?,
        )
        .await
    }

    pub async fn install_plugin(&self, path: &str) -> Result<Plugin> {
        decode(
            self.post("/v1/plugins/install")
                .json(&PluginPath { path })
                .send()
                .await?,
        )
        .await
    }

    pub async fn enable_plugin(&self, id: &str) -> Result<Plugin> {
        decode(
            self.post(&format!("/v1/plugins/{id}/enable"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn disable_plugin(&self, id: &str) -> Result<Plugin> {
        decode(
            self.post(&format!("/v1/plugins/{id}/disable"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn remove_plugin(&self, id: &str) -> Result<Value> {
        decode(
            self.http
                .delete(format!("{}/v1/plugins/{id}", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn plugin_proposals(&self) -> Result<Vec<PluginProposal>> {
        decode(self.get("/v1/plugins/proposals").send().await?).await
    }

    pub async fn plugin_proposal(&self, id: &str) -> Result<PluginProposal> {
        decode(
            self.get(&format!("/v1/plugins/proposals/{id}"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn session(&self, id: &str) -> Result<Session> {
        decode(self.get(&format!("/v1/sessions/{id}")).send().await?).await
    }

    pub async fn events(&self, session_id: &str) -> Result<Vec<Event>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/events"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn history(&self, session_id: &str) -> Result<Vec<Event>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/history"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn runs(&self, session_id: &str) -> Result<Vec<RunSummary>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/runs"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn usage(&self, session_id: &str) -> Result<UsageProjection> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/usage"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn inspect_run(&self, run_id: &str) -> Result<RunInspection> {
        decode(
            self.get(&format!("/v1/runs/{run_id}/inspection"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn inspect_context(&self, event_id: &str) -> Result<ContextInspection> {
        decode(self.get(&format!("/v1/contexts/{event_id}")).send().await?).await
    }

    pub async fn transcript(&self, session_id: &str, format: &str) -> Result<String> {
        ensure_success(
            self.get(&format!("/v1/sessions/{session_id}/transcript"))
                .query(&[("format", format)])
                .send()
                .await?,
        )
        .await?
        .text()
        .await
        .context("gateway returned an invalid transcript")
    }

    pub async fn fork_session(
        &self,
        session_id: &str,
        at: Option<&str>,
        agent_id: Option<&str>,
    ) -> Result<Session> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/fork"))
                .json(&ForkSession {
                    at,
                    title: None,
                    agent_id,
                })
                .send()
                .await?,
        )
        .await
    }

    pub async fn verify_event(&self, event_id: &str) -> Result<IntegrityResult> {
        decode(
            self.get(&format!("/v1/events/{event_id}/verify"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn send_message(
        &self,
        session_id: &str,
        content: &str,
        remember: bool,
    ) -> Result<MessageAccepted> {
        self.send_message_with_pastes(session_id, content, remember, &[])
            .await
    }

    pub async fn send_message_with_pastes(
        &self,
        session_id: &str,
        content: &str,
        remember: bool,
        paste_spans: &[PasteSpan],
    ) -> Result<MessageAccepted> {
        self.send_message_request(session_id, content, remember, paste_spans, false)
            .await
    }

    pub async fn send_message_now_with_pastes(
        &self,
        session_id: &str,
        content: &str,
        remember: bool,
        paste_spans: &[PasteSpan],
    ) -> Result<MessageAccepted> {
        self.send_message_request(session_id, content, remember, paste_spans, true)
            .await
    }

    pub async fn heal_scars(&self, session_id: &str, content: &str) -> Result<MessageAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/messages"))
                .json(&serde_json::json!({
                    "content": content,
                    "remember": false,
                    "paste_spans": [],
                    "send_now": false,
                    "purpose": "heal",
                }))
                .send()
                .await?,
        )
        .await
    }

    async fn send_message_request(
        &self,
        session_id: &str,
        content: &str,
        remember: bool,
        paste_spans: &[PasteSpan],
        send_now: bool,
    ) -> Result<MessageAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/messages"))
                .json(&serde_json::json!({
                    "content": content,
                    "remember": remember,
                    "paste_spans": paste_spans,
                    "send_now": send_now,
                    "purpose": "turn",
                }))
                .send()
                .await?,
        )
        .await
    }

    pub async fn current_plan(&self, session_id: &str) -> Result<PlanState> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/plans/current"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn send_plan_note(
        &self,
        session_id: &str,
        content: &str,
        paste_spans: &[PasteSpan],
    ) -> Result<MessageAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/plans/current/notes"))
                .json(&serde_json::json!({
                    "content": content,
                    "paste_spans": paste_spans,
                }))
                .send()
                .await?,
        )
        .await
    }

    pub async fn execute_plan(
        &self,
        session_id: &str,
        strategy: &str,
        note: Option<&str>,
    ) -> Result<PlanExecutionAccepted> {
        let mut body = serde_json::json!({"strategy": strategy});
        if let Some(note) = note.filter(|value| !value.is_empty()) {
            body["note"] = serde_json::Value::String(note.to_owned());
        }
        decode(
            self.post(&format!("/v1/sessions/{session_id}/plans/current/execute"))
                .json(&body)
                .send()
                .await?,
        )
        .await
    }

    pub async fn tasks(&self, session_id: &str) -> Result<SessionTaskList> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/tasks"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn queue_state(&self, session_id: &str) -> Result<QueueState> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/queue"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn compact_session(&self, session_id: &str) -> Result<CompactionAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/compact"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn start_goal(&self, session_id: &str, objective: &str) -> Result<Goal> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/goals"))
                .json(&serde_json::json!({"objective": objective}))
                .send()
                .await?,
        )
        .await
    }

    pub async fn goals(&self, session_id: &str) -> Result<Vec<Goal>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/goals"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn pause_goal(&self, session_id: &str) -> Result<Goal> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/goals/current/pause"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn resume_goal(&self, session_id: &str) -> Result<Goal> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/goals/current/resume"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn cancel_goal(&self, session_id: &str) -> Result<Goal> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/goals/current/cancel"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn take_latest_queued(&self, session_id: &str) -> Result<QueuedMessage> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/queue/take-latest"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn take_queued(&self, session_id: &str, queue_id: &str) -> Result<QueuedMessage> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/queue/{queue_id}/take"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn delete_queued(&self, session_id: &str, queue_id: &str) -> Result<QueueState> {
        decode(
            self.http
                .delete(format!(
                    "{}/v1/sessions/{session_id}/queue/{queue_id}",
                    self.base_url
                ))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn clear_queue(&self, session_id: &str) -> Result<QueueState> {
        decode(
            self.http
                .delete(format!("{}/v1/sessions/{session_id}/queue", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn pause_queue(&self, session_id: &str) -> Result<QueueState> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/queue/pause"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn resume_queue(&self, session_id: &str) -> Result<QueueState> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/queue/resume"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn memories(
        &self,
        session_id: &str,
        status: &str,
        query: &str,
    ) -> Result<Vec<MemoryRecord>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/memories"))
                .query(&[("status", status), ("query", query)])
                .send()
                .await?,
        )
        .await
    }

    pub async fn memory(&self, session_id: &str, memory_id: &str) -> Result<MemoryRecord> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/memories/{memory_id}"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn delete_memory(&self, session_id: &str, memory_id: &str) -> Result<()> {
        let _: Value = decode(
            self.http
                .delete(format!(
                    "{}/v1/sessions/{session_id}/memories/{memory_id}",
                    self.base_url
                ))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await?;
        Ok(())
    }

    pub async fn capture_memory(&self, session_id: &str, content: &str) -> Result<MemoryJob> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/memories/capture"))
                .json(&serde_json::json!({"content": content}))
                .send()
                .await?,
        )
        .await
    }

    pub async fn submit_correction(&self, session_id: &str, content: &str) -> Result<Scar> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/correct"))
                .json(&SubmitCorrection { content })
                .send()
                .await?,
        )
        .await
    }

    pub async fn scars(&self, session_id: &str) -> Result<Vec<Scar>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/scars"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn scar(&self, session_id: &str, scar_id: &str) -> Result<Scar> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/scars/{scar_id}"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn update_scar(
        &self,
        session_id: &str,
        scar_id: &str,
        update: &ScarUpdate,
    ) -> Result<Scar> {
        decode(
            self.http
                .patch(format!(
                    "{}/v1/sessions/{session_id}/scars/{scar_id}",
                    self.base_url
                ))
                .bearer_auth(&self.token)
                .json(update)
                .send()
                .await?,
        )
        .await
    }

    pub async fn delete_scar(&self, session_id: &str, scar_id: &str) -> Result<()> {
        let _: Value = decode(
            self.http
                .delete(format!(
                    "{}/v1/sessions/{session_id}/scars/{scar_id}",
                    self.base_url
                ))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await?;
        Ok(())
    }

    pub async fn transition_memory(
        &self,
        session_id: &str,
        memory_id: &str,
        action: &str,
    ) -> Result<MemoryRecord> {
        decode(
            self.post(&format!(
                "/v1/sessions/{session_id}/memories/{memory_id}/transition"
            ))
            .json(&serde_json::json!({"action": action}))
            .send()
            .await?,
        )
        .await
    }

    pub async fn promote_memory(
        &self,
        session_id: &str,
        memory_id: &str,
        visibility: &str,
    ) -> Result<MemoryRecord> {
        decode(
            self.post(&format!(
                "/v1/sessions/{session_id}/memories/{memory_id}/promote"
            ))
            .json(&serde_json::json!({"visibility": visibility}))
            .send()
            .await?,
        )
        .await
    }

    pub async fn memory_jobs(&self, session_id: &str) -> Result<Vec<MemoryJob>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/memory-jobs"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn retry_memory_job(&self, session_id: &str, job_id: &str) -> Result<MemoryJob> {
        decode(
            self.post(&format!(
                "/v1/sessions/{session_id}/memory-jobs/{job_id}/retry"
            ))
            .send()
            .await?,
        )
        .await
    }

    pub async fn skills(&self, session_id: &str, query: &str) -> Result<Vec<SkillSummary>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/skills"))
                .query(&[("query", query), ("limit", "200")])
                .send()
                .await?,
        )
        .await
    }

    pub async fn available_skills(&self, session_id: &str) -> Result<Vec<SkillSummary>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/skills/available"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn skill(&self, session_id: &str, slug: &str) -> Result<SkillVersion> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/skills/{slug}"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn skill_history(&self, session_id: &str, slug: &str) -> Result<Vec<SkillVersion>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/skills/{slug}/history"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn author_skill(
        &self,
        session_id: &str,
        goal: &str,
        scope: &str,
        target_skill_id: Option<&str>,
    ) -> Result<SkillJob> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/skills/author"))
                .json(&serde_json::json!({
                    "goal": goal,
                    "scope": scope,
                    "target_skill_id": target_skill_id,
                }))
                .send()
                .await?,
        )
        .await
    }

    pub async fn skill_jobs(&self, session_id: &str) -> Result<Vec<SkillJob>> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/skill-jobs"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn retry_skill_job(&self, session_id: &str, job_id: &str) -> Result<SkillJob> {
        decode(
            self.post(&format!(
                "/v1/sessions/{session_id}/skill-jobs/{job_id}/retry"
            ))
            .send()
            .await?,
        )
        .await
    }

    pub async fn control_skill(
        &self,
        session_id: &str,
        slug: &str,
        action: &str,
    ) -> Result<SkillVersion> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/skills/{slug}/{action}"))
                .json(&serde_json::json!({}))
                .send()
                .await?,
        )
        .await
    }

    pub async fn trust_status(&self, session_id: &str) -> Result<TrustStatus> {
        decode(
            self.get(&format!("/v1/sessions/{session_id}/trust"))
                .send()
                .await?,
        )
        .await
    }

    pub async fn trust_session(&self, session_id: &str) -> Result<TrustStatus> {
        decode(
            self.http
                .put(format!("{}/v1/sessions/{session_id}/trust", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn revoke_trust(&self, session_id: &str) -> Result<TrustStatus> {
        decode(
            self.http
                .delete(format!("{}/v1/sessions/{session_id}/trust", self.base_url))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub async fn resolve_approval(
        &self,
        approval_id: &str,
        request_hash: &str,
        decision: &str,
    ) -> Result<ApprovalResolution> {
        decode(
            self.post(&format!("/v1/approvals/{approval_id}"))
                .json(&serde_json::json!({
                    "request_hash": request_hash,
                    "decision": decision,
                }))
                .send()
                .await?,
        )
        .await
    }

    pub async fn event_stream(&self, session_id: &str, after: u64) -> Result<Response> {
        let after_text = after.to_string();
        let response = self
            .get("/v1/events")
            .query(&[("session_id", session_id)])
            .header("Last-Event-ID", after_text)
            .send()
            .await?;
        ensure_success(response).await
    }

    pub async fn cancel(&self, run_id: &str) -> Result<()> {
        let response = self
            .post(&format!("/v1/runs/{run_id}/cancel"))
            .send()
            .await?;
        ensure_success(response).await?;
        Ok(())
    }

    fn get(&self, path: &str) -> reqwest::RequestBuilder {
        self.http
            .get(format!("{}{}", self.base_url, path))
            .bearer_auth(&self.token)
    }

    fn post(&self, path: &str) -> reqwest::RequestBuilder {
        self.http
            .post(format!("{}{}", self.base_url, path))
            .bearer_auth(&self.token)
    }
}

async fn decode<T: DeserializeOwned>(response: Response) -> Result<T> {
    ensure_success(response)
        .await?
        .json::<T>()
        .await
        .context("gateway returned invalid JSON")
}

async fn ensure_success(response: Response) -> Result<Response> {
    if response.status().is_success() {
        return Ok(response);
    }
    let status = response.status();
    let body = response.text().await.unwrap_or_default();
    if status == StatusCode::UNAUTHORIZED {
        bail!("gateway rejected this Hames home's token; another gateway may be using the port")
    }
    bail!("gateway returned {status}: {body}")
}
