use std::fs;

use anyhow::{Context, Result, bail};
use reqwest::{Client, Response, StatusCode};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;

use crate::local::LocalPaths;

pub const PROTOCOL_VERSION: u32 = 5;

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
pub struct RunAccepted {
    pub run_id: String,
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

#[derive(Serialize)]
struct CreateSession<'a> {
    working_directory: &'a str,
    agent_id: &'a str,
    provider: &'a str,
    model: &'a str,
    reasoning_effort: &'a str,
}

#[derive(Serialize)]
struct UpdateSession<'a> {
    provider: &'a str,
    model: &'a str,
    reasoning_effort: &'a str,
}

#[derive(Serialize)]
struct ForkSession<'a> {
    at: Option<&'a str>,
    title: Option<&'a str>,
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
        provider: &str,
        model: &str,
        reasoning_effort: &str,
    ) -> Result<Session> {
        decode(
            self.post("/v1/sessions")
                .json(&CreateSession {
                    working_directory,
                    agent_id: "default",
                    provider,
                    model,
                    reasoning_effort,
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

    pub async fn sessions(&self) -> Result<Vec<Session>> {
        decode(self.get("/v1/sessions").send().await?).await
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

    pub async fn fork_session(&self, session_id: &str, at: Option<&str>) -> Result<Session> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/fork"))
                .json(&ForkSession { at, title: None })
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

    pub async fn send_message(&self, session_id: &str, content: &str) -> Result<RunAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/messages"))
                .json(&serde_json::json!({"content": content}))
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
        bail!("gateway rejected its local token; remove stale runtime state and restart")
    }
    bail!("gateway returned {status}: {body}")
}
