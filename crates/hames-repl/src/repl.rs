use std::env;
use std::fs;
use std::io::{self, Write};

use anyhow::{Context, Result, bail};
use futures_util::StreamExt;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;

use crate::api::{
    GatewayClient, LiveEnvelope, PROTOCOL_VERSION, ProviderModel, ProviderStatus, Session,
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
    let mut model = paths.configured_model(&provider)?;
    let mut reasoning = paths.configured_reasoning(&provider)?;
    let statuses = client.providers().await?;
    model = select_model(&mut editor, &statuses, &provider, &model)?;
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
    Ok(())
}

async fn ensure_gateway(paths: &LocalPaths) -> Result<()> {
    let url = paths.gateway_url()?;
    if let Ok(health) = GatewayClient::health_unauthenticated(&url).await
        && health.status == "ok"
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
                    "{}  {:<8}  {}  {} / {}  {}  {}",
                    item.id,
                    item.status,
                    item.created_at,
                    item.provider,
                    item.model,
                    item.agent_id,
                    item.title.as_deref().unwrap_or(&item.working_directory)
                );
            }
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
            let statuses = client.providers().await?;
            let selected_provider = parts.get(1).copied().unwrap_or(provider);
            let requested_model = parts.get(2).copied().unwrap_or("");
            let selected_model =
                select_model(editor, &statuses, selected_provider, requested_model)?;
            *session = client
                .update_session(&session.id, selected_provider, &selected_model, reasoning)
                .await?;
            *provider = selected_provider.to_owned();
            *model = selected_model;
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
            let effort = parts
                .get(1)
                .context("usage: /reasoning off|low|medium|xhigh")?;
            *session = client
                .update_session(&session.id, provider, model, effort)
                .await?;
            *reasoning = (*effort).to_owned();
            println!("reasoning effort: {reasoning}");
        }
        "/status" => print_statuses(&client.providers().await?),
        unknown => bail!("unknown command: {unknown}; use /help"),
    }
    Ok(CommandOutcome::Continue)
}

fn select_model(
    editor: &mut DefaultEditor,
    statuses: &[ProviderStatus],
    provider: &str,
    requested: &str,
) -> Result<String> {
    let status = statuses
        .iter()
        .find(|item| item.id == provider)
        .with_context(|| format!("provider {provider} is not configured"))?;
    if !status.available {
        bail!(
            "provider {provider} is unavailable: {}",
            status.error.as_deref().unwrap_or("unknown error")
        );
    }
    if !requested.is_empty() {
        if status.models.iter().any(|item| item.id == requested) {
            return Ok(requested.to_owned());
        }
        bail!("provider {provider} does not report model {requested}");
    }
    if status.models.len() == 1 {
        return Ok(status.models[0].id.clone());
    }
    if status.models.is_empty() {
        bail!("provider {provider} reports no models");
    }
    println!("Select a model for {provider}:");
    for (index, item) in status.models.iter().enumerate() {
        println!("  {}. {} ({})", index + 1, item.id, model_summary(item));
    }
    let choice = editor.readline("model> ")?;
    let index: usize = choice
        .trim()
        .parse()
        .context("model choice must be a number")?;
    status
        .models
        .get(index.saturating_sub(1))
        .map(|item| item.id.clone())
        .context("model choice is outside the displayed range")
}

fn print_statuses(statuses: &[ProviderStatus]) {
    for provider in statuses {
        if provider.available {
            println!("{}: available", provider.id);
            for model in &provider.models {
                println!("  {} — {}", model.id, model_summary(model));
            }
        } else {
            println!(
                "{}: unavailable ({})",
                provider.id,
                provider.error.as_deref().unwrap_or("unknown error")
            );
        }
    }
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
        Some(true) => fields.push(format!("thinking: {}", model.reasoning_efforts.join("/"))),
        None => fields.push("thinking: unknown until loaded".to_owned()),
        Some(false) => {}
    }
    fields.join(", ")
}

async fn stream_message(client: &GatewayClient, session: &Session, content: &str) -> Result<()> {
    let events = client.events(&session.id).await?;
    let after = events.iter().map(|event| event.sequence).max().unwrap_or(0);
    let response = client.event_stream(&session.id, after).await?;
    let accepted = client.send_message(&session.id, content).await?;
    let run_id = accepted.run_id;
    let mut stream = response.bytes_stream();
    let mut decoder = SseDecoder::default();
    let mut showed_reasoning = false;
    let mut showed_answer = false;
    let mut cancelled = false;

    loop {
        tokio::select! {
            chunk = stream.next() => {
                let Some(chunk) = chunk else { bail!("gateway event stream ended") };
                let bytes = chunk?;
                for data in decoder.push(&bytes) {
                    let envelope: LiveEnvelope = serde_json::from_str(&data)
                        .context("gateway emitted malformed SSE data")?;
                    if process_envelope(
                        &envelope,
                        &run_id,
                        &mut showed_reasoning,
                        &mut showed_answer,
                    )? {
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
    showed_reasoning: &mut bool,
    showed_answer: &mut bool,
) -> Result<bool> {
    if envelope.durable {
        let Some(event) = &envelope.event else {
            return Ok(false);
        };
        if event.run_id.as_deref() != Some(run_id) {
            return Ok(false);
        }
        match event.event_type.as_str() {
            "assistant.reasoning" if !*showed_reasoning => {
                if let Some(content) = event
                    .payload
                    .get("content")
                    .and_then(|value| value.as_str())
                {
                    print!("thinking> {content}");
                    io::stdout().flush()?;
                }
            }
            "assistant.message" if !*showed_answer => {
                if let Some(content) = event
                    .payload
                    .get("content")
                    .and_then(|value| value.as_str())
                {
                    print!("assistant> {content}");
                    io::stdout().flush()?;
                }
            }
            "model.response.completed" | "model.response.failed" | "run.cancelled" => {
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
            if !*showed_reasoning {
                print!("thinking> ");
                *showed_reasoning = true;
            }
            print!("{text}");
            io::stdout().flush()?;
        }
        Some("response.text_delta") => {
            if !*showed_answer {
                if *showed_reasoning {
                    println!();
                }
                print!("assistant> ");
                *showed_answer = true;
            }
            print!("{text}");
            io::stdout().flush()?;
        }
        _ => {}
    }
    Ok(false)
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
        "/help\n/new\n/sessions\n/resume <id>\n/provider [provider] [model]\n\
         /model\n/status\n/reasoning off|low|medium|xhigh\n/quit"
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
