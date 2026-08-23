use std::env;
use std::fs;
use std::io::{self, Write};

use anyhow::{Context, Result, bail};
use futures_util::StreamExt;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;

use crate::api::{
    GatewayClient, LiveEnvelope, PROTOCOL_VERSION, ProviderModel, ProviderProbe, ProviderProfile,
    Session,
};
use crate::local::{LocalPaths, start_backend};

pub async fn run() -> Result<()> {
    let paths = LocalPaths::resolve()?;
    ensure_gateway(&paths).await?;
    let client = GatewayClient::from_paths(&paths)?;
    let health = client.health().await?;
    if health.protocol_version != PROTOCOL_VERSION {
        bail!(
            "gateway protocol {} is incompatible with client protocol {}",
            health.protocol_version,
            PROTOCOL_VERSION
        );
    }
    let mut editor = DefaultEditor::new()?;
    if paths.history.exists() {
        let _ = editor.load_history(&paths.history);
    }
    let cwd = env::current_dir()?.canonicalize()?;
    let mut provider = paths.configured_provider()?;
    if !health
        .provider_profiles
        .iter()
        .any(|item| item == &provider)
    {
        bail!("provider {provider} is not configured in the gateway");
    }
    let mut model = paths.configured_model(&provider)?;
    let mut reasoning = paths.configured_reasoning(&provider)?;
    let profiles = client.providers().await?;
    let profile = find_profile(&profiles, &provider)?;
    if model.is_empty() {
        model.clone_from(&profile.configured_model);
    }
    if reasoning.is_empty() {
        reasoning.clone_from(&profile.default_reasoning_effort);
    }
    let probe = client.probe_provider(&provider).await?;
    model = select_model(&mut editor, &probe, &model)?;
    let mut session = client
        .create_session(&cwd.to_string_lossy(), &provider, &model, &reasoning)
        .await?;
    println!(
        "Hames {} · gateway {} · {} / {} · {}",
        env!("CARGO_PKG_VERSION"),
        health.version,
        provider,
        model,
        cwd.display()
    );
    if !health.database_ready {
        bail!("gateway database is not ready");
    }
    println!("Type /help for commands. The Python gateway remains running after exit.");

    loop {
        let Some(input) = read_input(&mut editor)? else {
            break;
        };
        if input.trim().is_empty() {
            continue;
        }
        let _ = editor.add_history_entry(input.as_str());
        if input.starts_with('/') {
            match handle_command(
                &client,
                &mut editor,
                &cwd.to_string_lossy(),
                &input,
                &mut session,
                &mut provider,
                &mut model,
                &mut reasoning,
            )
            .await
            {
                Ok(CommandOutcome::Continue) => continue,
                Ok(CommandOutcome::Exit) => break,
                Err(error) => eprintln!("error: {error:#}"),
            }
            continue;
        }
        if let Err(error) = stream_message(&client, &session, &input).await {
            eprintln!("error: {error:#}");
        }
    }
    fs::create_dir_all(&paths.root).ok();
    editor.save_history(&paths.history)?;
    make_history_private(&paths.history)?;
    Ok(())
}

#[cfg(unix)]
fn make_history_private(path: &std::path::Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(not(unix))]
fn make_history_private(_: &std::path::Path) -> Result<()> {
    Ok(())
}

pub(crate) async fn ensure_gateway(paths: &LocalPaths) -> Result<()> {
    let url = paths.gateway_url()?;
    if let Ok(health) = GatewayClient::health_unauthenticated(&url).await
        && health.status == "ok"
        && health.protocol_version == PROTOCOL_VERSION
    {
        return Ok(());
    }
    start_backend()?;
    let health = GatewayClient::health_unauthenticated(&url)
        .await
        .context("gateway did not become reachable")?;
    if health.protocol_version != PROTOCOL_VERSION {
        bail!("started gateway has incompatible protocol version")
    }
    Ok(())
}

fn read_input(editor: &mut DefaultEditor) -> Result<Option<String>> {
    let mut result = String::new();
    let mut prompt = "you> ";
    loop {
        match editor.readline(prompt) {
            Ok(mut line) => {
                let continued = line.ends_with('\\');
                if continued {
                    line.pop();
                }
                result.push_str(&line);
                if !continued {
                    return Ok(Some(result));
                }
                result.push('\n');
                prompt = "...> ";
            }
            Err(ReadlineError::Interrupted) => return Ok(Some(String::new())),
            Err(ReadlineError::Eof) => return Ok(None),
            Err(error) => return Err(error.into()),
        }
    }
}

enum CommandOutcome {
    Continue,
    Exit,
}

#[allow(clippy::too_many_arguments)]
async fn handle_command(
    client: &GatewayClient,
    editor: &mut DefaultEditor,
    cwd: &str,
    input: &str,
    session: &mut Session,
    provider: &mut String,
    model: &mut String,
    reasoning: &mut String,
) -> Result<CommandOutcome> {
    let parts: Vec<&str> = input.split_whitespace().collect();
    match parts.first().copied().unwrap_or("") {
        "/help" => print_help(),
        "/quit" | "/exit" => return Ok(CommandOutcome::Exit),
        "/new" => {
            *session = client
                .create_session(cwd, provider, model, reasoning)
                .await?;
            println!("new session {}", session.id);
        }
        "/sessions" => {
            for item in client.sessions().await? {
                println!(
                    "{}  {:<8}  {}  {} / {}  {}  {}{}",
                    item.id,
                    item.status,
                    item.created_at,
                    item.provider,
                    item.model,
                    item.agent_id,
                    item.title.as_deref().unwrap_or(&item.working_directory),
                    item.parent_session_id
                        .as_ref()
                        .map(|parent| format!("  branch-of {parent}"))
                        .unwrap_or_default()
                );
            }
        }
        "/session" => print_session(session),
        "/events" => {
            let count = parts
                .get(1)
                .map(|value| value.parse::<usize>())
                .transpose()
                .context("usage: /events [count]")?
                .unwrap_or(20);
            let history = client.history(&session.id).await?;
            let start = history.len().saturating_sub(count);
            for event in &history[start..] {
                let short_id = event.id.get(..8).unwrap_or(&event.id);
                let summary = event
                    .payload
                    .get("content")
                    .and_then(|value| value.as_str())
                    .map(|value| {
                        value
                            .replace('\n', " ")
                            .chars()
                            .take(60)
                            .collect::<String>()
                    })
                    .unwrap_or_default();
                println!(
                    "{:>6}  {}  {:<28} {}",
                    event.sequence, short_id, event.event_type, summary
                );
            }
        }
        "/fork" => {
            *session = client
                .fork_session(&session.id, parts.get(1).copied())
                .await?;
            *provider = session.provider.clone();
            *model = session.model.clone();
            *reasoning = session.reasoning_effort.clone();
            println!(
                "forked session {} from {}",
                session.id,
                session.fork_event_id.as_deref().unwrap_or("unknown event")
            );
        }
        "/resume" => {
            let id = parts.get(1).context("usage: /resume <session-id>")?;
            *session = client.session(id).await?;
            *provider = session.provider.clone();
            *model = session.model.clone();
            *reasoning = session.reasoning_effort.clone();
            println!("resumed session {}", session.id);
        }
        "/provider" => {
            let profiles = client.providers().await?;
            let selected_provider = parts.get(1).copied().unwrap_or(provider);
            let profile = find_profile(&profiles, selected_provider)?;
            let requested_model = parts.get(2).copied().unwrap_or(&profile.configured_model);
            let probe = client.probe_provider(selected_provider).await?;
            let selected_model = select_model(editor, &probe, requested_model)?;
            let selected_reasoning = profile.default_reasoning_effort.clone();
            *session = client
                .update_session(
                    &session.id,
                    selected_provider,
                    &selected_model,
                    &selected_reasoning,
                )
                .await?;
            *provider = selected_provider.to_owned();
            *model = selected_model;
            *reasoning = selected_reasoning;
            println!("provider: {provider} / {model}");
        }
        "/model" => println!(
            "provider: {}\nmodel: {}\nreasoning: {}",
            provider,
            model,
            if reasoning.is_empty() {
                "provider default"
            } else {
                reasoning
            }
        ),
        "/reasoning" => {
            let probe = client.probe_provider(provider).await?;
            let selected = probe
                .models
                .iter()
                .find(|item| item.id == *model)
                .with_context(|| format!("provider {provider} does not report model {model}"))?;
            let Some(requested) = parts.get(1) else {
                println!(
                    "reasoning: {}\nsupported: default/off{}",
                    if reasoning.is_empty() {
                        "provider default"
                    } else {
                        reasoning
                    },
                    if selected.reasoning_efforts.is_empty() {
                        String::new()
                    } else {
                        format!("/{}", selected.reasoning_efforts.join("/"))
                    }
                );
                return Ok(CommandOutcome::Continue);
            };
            let effort = if *requested == "default" {
                ""
            } else {
                *requested
            };
            if effort != "off"
                && !effort.is_empty()
                && !selected
                    .reasoning_efforts
                    .iter()
                    .any(|supported| supported == effort)
            {
                bail!(
                    "model {model} does not advertise reasoning effort {effort}; use /reasoning for its supported values"
                );
            }
            *session = client
                .update_session(&session.id, provider, model, effort)
                .await?;
            *reasoning = effort.to_owned();
            println!(
                "reasoning effort: {}",
                if reasoning.is_empty() {
                    "provider default"
                } else {
                    reasoning
                }
            );
        }
        "/status" => print_statuses(client).await?,
        unknown => bail!("unknown command: {unknown}; use /help"),
    }
    Ok(CommandOutcome::Continue)
}

fn select_model(
    editor: &mut DefaultEditor,
    probe: &ProviderProbe,
    requested: &str,
) -> Result<String> {
    if !probe.reachable {
        bail!(
            "provider {} is unavailable: {}",
            probe.id,
            probe
                .error
                .as_ref()
                .map(|error| error.message.as_str())
                .unwrap_or("unknown error")
        );
    }
    if !requested.is_empty() {
        if probe.models.iter().any(|item| item.id == requested) {
            return Ok(requested.to_owned());
        }
        bail!("provider {} does not report model {requested}", probe.id);
    }
    if probe.models.len() == 1 {
        return Ok(probe.models[0].id.clone());
    }
    if probe.models.is_empty() {
        bail!("provider {} reports no models", probe.id);
    }
    println!("Select a model for {}:", probe.id);
    for (index, item) in probe.models.iter().enumerate() {
        println!("  {}. {} ({})", index + 1, item.id, model_summary(item));
    }
    let choice = editor.readline("model> ")?;
    let index: usize = choice
        .trim()
        .parse()
        .context("model choice must be a number")?;
    probe
        .models
        .get(index.saturating_sub(1))
        .map(|item| item.id.clone())
        .context("model choice is outside the displayed range")
}

fn find_profile<'a>(
    profiles: &'a [ProviderProfile],
    provider: &str,
) -> Result<&'a ProviderProfile> {
    profiles
        .iter()
        .find(|item| item.id == provider)
        .with_context(|| format!("provider {provider} is not configured"))
}

async fn print_statuses(client: &GatewayClient) -> Result<()> {
    let health = client.health().await?;
    println!(
        "gateway: {} · default: {} · active runs: {}",
        health.status, health.default_provider, health.active_runs
    );
    let profiles = client.providers().await?;
    let probes = futures_util::future::join_all(
        profiles
            .iter()
            .map(|profile| client.probe_provider(&profile.id)),
    )
    .await;
    for (profile, result) in profiles.iter().zip(probes) {
        let provider = result?;
        if provider.adapter != profile.adapter {
            bail!(
                "provider {} reported adapter {} but is configured as {}",
                provider.id,
                provider.adapter,
                profile.adapter
            );
        }
        if provider.reachable {
            println!(
                "{} [{}] {}: available",
                provider.id, profile.adapter, profile.endpoint
            );
            if !profile.supported_reasoning_efforts.is_empty() {
                println!(
                    "  declared thinking levels: {}",
                    profile.supported_reasoning_efforts.join("/")
                );
            }
            for model in &provider.models {
                println!("  {} — {}", model.id, model_summary(model));
            }
        } else {
            println!(
                "{} [{}]: unavailable ({}: {}; retryable: {})",
                provider.id,
                profile.adapter,
                provider
                    .error
                    .as_ref()
                    .map(|error| error.code.as_str())
                    .unwrap_or("unknown_error"),
                provider
                    .error
                    .as_ref()
                    .map(|error| error.message.as_str())
                    .unwrap_or("unknown error"),
                provider.error.as_ref().is_some_and(|error| error.retryable)
            );
        }
    }
    Ok(())
}

fn model_summary(model: &ProviderModel) -> String {
    let mut fields = vec![model.status.clone()];
    if let Some(parameters) = &model.parameter_size {
        fields.push(parameters.clone());
    }
    if let Some(quantization) = &model.quantization {
        fields.push(quantization.clone());
    }
    if let Some(context) = model.context_length {
        fields.push(format!("{context} ctx"));
    }
    match model.reasoning_supported {
        Some(true) if model.reasoning_efforts.is_empty() => {
            fields.push("thinking: supported; levels unknown".to_owned());
        }
        Some(true) => fields.push(format!("thinking: {}", model.reasoning_efforts.join("/"))),
        None => fields.push("thinking: unknown until loaded".to_owned()),
        Some(false) => {}
    }
    fields.join(", ")
}

fn print_session(session: &Session) {
    println!(
        "session: {}\nstatus: {}\nworking directory: {}\nagent: {}\nprovider: {}\nmodel: {}\nreasoning: {}\nparent: {}\nfork event: {}",
        session.id,
        session.status,
        session.working_directory,
        session.agent_id,
        session.provider,
        session.model,
        if session.reasoning_effort.is_empty() {
            "provider default"
        } else {
            &session.reasoning_effort
        },
        session.parent_session_id.as_deref().unwrap_or("none"),
        session.fork_event_id.as_deref().unwrap_or("none"),
    );
}

async fn stream_message(client: &GatewayClient, session: &Session, content: &str) -> Result<()> {
    let events = client.events(&session.id).await?;
    let mut after = events.iter().map(|event| event.sequence).max().unwrap_or(0);
    let response = client.event_stream(&session.id, after).await?;
    let accepted = client.send_message(&session.id, content).await?;
    let run_id = accepted.run_id;
    let mut stream = Box::pin(response.bytes_stream());
    let mut decoder = SseDecoder::default();
    let mut output = RenderedOutput::default();
    let mut cancelled = false;
    let mut reconnects = 0_u8;

    loop {
        tokio::select! {
            chunk = stream.next() => {
                let bytes = match chunk {
                    Some(Ok(bytes)) => bytes,
                    Some(Err(error)) => {
                        reconnects += 1;
                        if reconnects > 3 {
                            return Err(error).context("gateway event stream repeatedly failed");
                        }
                        let response = client.event_stream(&session.id, after).await?;
                        stream = Box::pin(response.bytes_stream());
                        decoder = SseDecoder::default();
                        continue;
                    }
                    None => {
                        reconnects += 1;
                        if reconnects > 3 {
                            bail!("gateway event stream repeatedly ended before the run completed");
                        }
                        let response = client.event_stream(&session.id, after).await?;
                        stream = Box::pin(response.bytes_stream());
                        decoder = SseDecoder::default();
                        continue;
                    }
                };
                for data in decoder.push(&bytes) {
                    let envelope: LiveEnvelope = serde_json::from_str(&data)
                        .context("gateway emitted malformed SSE data")?;
                    if let Some(event) = &envelope.event {
                        after = after.max(event.sequence);
                    }
                    if process_envelope(&envelope, &run_id, &mut output)? {
                        println!();
                        return Ok(());
                    }
                }
            }
            _ = tokio::signal::ctrl_c(), if !cancelled => {
                cancelled = true;
                client.cancel(&run_id).await?;
                eprintln!("\n[cancelling]");
            }
        }
    }
}

fn process_envelope(
    envelope: &LiveEnvelope,
    run_id: &str,
    output: &mut RenderedOutput,
) -> Result<bool> {
    if envelope.durable {
        let Some(event) = &envelope.event else {
            return Ok(false);
        };
        if event.run_id.as_deref() != Some(run_id) {
            return Ok(false);
        }
        match event.event_type.as_str() {
            "assistant.reasoning" => {
                if let Some(content) = event
                    .payload
                    .get("content")
                    .and_then(|value| value.as_str())
                {
                    output.reconcile_reasoning(content)?;
                }
            }
            "assistant.message" => {
                if let Some(content) = event
                    .payload
                    .get("content")
                    .and_then(|value| value.as_str())
                {
                    output.reconcile_answer(content)?;
                }
            }
            "model.response.failed" => {
                let code = event
                    .payload
                    .get("code")
                    .and_then(|value| value.as_str())
                    .unwrap_or("model_response_failed");
                let message = event
                    .payload
                    .get("message")
                    .and_then(|value| value.as_str())
                    .unwrap_or("the model run failed");
                eprintln!("\nerror: {code}: {message}");
                return Ok(true);
            }
            "model.response.completed" | "run.cancelled" => {
                return Ok(true);
            }
            _ => {}
        }
        return Ok(false);
    }
    if envelope.run_id.as_deref() != Some(run_id) {
        return Ok(false);
    }
    let text = envelope
        .payload
        .as_ref()
        .and_then(|payload| payload.get("text"))
        .and_then(|value| value.as_str())
        .unwrap_or("");
    match envelope.event_type.as_deref() {
        Some("response.reasoning_delta") => {
            output.push_reasoning(text)?;
        }
        Some("response.text_delta") => {
            output.push_answer(text)?;
        }
        _ => {}
    }
    Ok(false)
}

#[derive(Default)]
struct RenderedOutput {
    reasoning: String,
    answer: String,
    reasoning_started: bool,
    answer_started: bool,
}

impl RenderedOutput {
    fn push_reasoning(&mut self, text: &str) -> Result<()> {
        if !self.reasoning_started {
            print!("thinking> ");
            self.reasoning_started = true;
        }
        print!("{text}");
        self.reasoning.push_str(text);
        io::stdout().flush()?;
        Ok(())
    }

    fn push_answer(&mut self, text: &str) -> Result<()> {
        if !self.answer_started {
            if self.reasoning_started {
                println!();
            }
            print!("assistant> ");
            self.answer_started = true;
        }
        print!("{text}");
        self.answer.push_str(text);
        io::stdout().flush()?;
        Ok(())
    }

    fn reconcile_reasoning(&mut self, content: &str) -> Result<()> {
        let suffix = content
            .strip_prefix(&self.reasoning)
            .unwrap_or(content)
            .to_owned();
        self.push_reasoning(&suffix)
    }

    fn reconcile_answer(&mut self, content: &str) -> Result<()> {
        let suffix = content
            .strip_prefix(&self.answer)
            .unwrap_or(content)
            .to_owned();
        self.push_answer(&suffix)
    }
}

#[derive(Default)]
struct SseDecoder {
    buffer: String,
}

impl SseDecoder {
    fn push(&mut self, bytes: &[u8]) -> Vec<String> {
        self.buffer.push_str(&String::from_utf8_lossy(bytes));
        self.buffer = self.buffer.replace("\r\n", "\n");
        let mut frames = Vec::new();
        while let Some(boundary) = self.buffer.find("\n\n") {
            let frame = self.buffer[..boundary].to_owned();
            self.buffer.drain(..boundary + 2);
            let data = frame
                .lines()
                .filter_map(|line| line.strip_prefix("data: "))
                .collect::<Vec<_>>()
                .join("\n");
            if !data.is_empty() {
                frames.push(data);
            }
        }
        frames
    }
}

fn print_help() {
    println!(
        "/help\n/new\n/session\n/sessions\n/resume <id>\n/events [count]\n\
         /fork [event-id-or-sequence]\n/provider [provider] [model]\n/model\n/status\n\
         /reasoning [default|off|on|level]\n/quit"
    );
}

#[cfg(test)]
mod tests {
    use super::SseDecoder;

    #[test]
    fn sse_decoder_handles_split_frames() {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"event: one\ndata: {\"a\":").is_empty());
        assert_eq!(decoder.push(b"1}\n\n"), vec!["{\"a\":1}"]);
    }
}
