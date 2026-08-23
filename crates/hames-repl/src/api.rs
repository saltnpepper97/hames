use std::fs;

use anyhow::{Context, Result, bail};
use reqwest::{Client, Response, StatusCode};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;

use crate::local::LocalPaths;

pub const PROTOCOL_VERSION: u32 = 1;

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
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderStatus {
    pub id: String,
    pub available: bool,
    pub models: Vec<ProviderModel>,
    pub error: Option<String>,
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

#[derive(Clone, Debug, Deserialize)]
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
}

#[derive(Clone, Debug, Deserialize)]
pub struct Event {
    pub sequence: u64,
    pub run_id: Option<String>,
    #[serde(rename = "type")]
    pub event_type: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RunAccepted {
    pub run_id: String,
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

    pub async fn providers(&self) -> Result<Vec<ProviderStatus>> {
        decode(self.get("/v1/providers").send().await?).await
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

    pub async fn send_message(&self, session_id: &str, content: &str) -> Result<RunAccepted> {
        decode(
            self.post(&format!("/v1/sessions/{session_id}/messages"))
                .json(&serde_json::json!({"content": content}))
                .send()
                .await?,
        )
        .await
    }

    pub async fn event_stream(&self, session_id: &str, after: u64) -> Result<Response> {
        let after_text = after.to_string();
        let response = self
            .get("/v1/events")
            .query(&[("session_id", session_id), ("after_sequence", &after_text)])
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
