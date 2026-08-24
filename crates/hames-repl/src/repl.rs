use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use futures_util::StreamExt;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use unicode_width::UnicodeWidthChar;

use crate::activity::{ActivityBoard, ActivityCategory};
use crate::api::{
    ContextInspection, Event, GatewayClient, LiveEnvelope, MemoryJob, MemoryRecord,
    PROTOCOL_VERSION, ProviderModel, ProviderProbe, ProviderProfile, RunInspection, Scar, Session,
    SkillJob, SkillSummary, SkillVersion,
};
use crate::local::{LocalPaths, start_backend, write_private_export};
use crate::style;

pub async fn run() -> Result<()> {
    style::init();
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
        .create_session(
            &cwd.to_string_lossy(),
            "default",
            &provider,
            &model,
            &reasoning,
        )
        .await?;
    println!(
        "{}",
        style::banner_lines(
            env!("CARGO_PKG_VERSION"),
            &health.version,
            &provider,
            &model,
            if reasoning.is_empty() {
                "default"
            } else {
                &reasoning
            },
            &cwd.display().to_string(),
            &session.id,
        )
    );
    if !health.database_ready {
        bail!("gateway database is not ready");
    }
    println!(
        "{}",
        style::dim("Type /help for commands. The Python gateway remains running after exit.")
    );
    println!();
    ensure_trust(&client, &mut editor, &session).await?;
    let mut remember_next = false;

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
                &mut remember_next,
            )
            .await
            {
                Ok(CommandOutcome::Continue) => continue,
                Ok(CommandOutcome::Exit) => break,
                Err(error) => eprintln!("{} {error:#}", style::badge(style::Badge::Error, false)),
            }
            continue;
        }
        let remember = std::mem::take(&mut remember_next);
        if remember {
            println!(
                "{} {}",
                style::badge(style::Badge::Hames, false),
                style::dim("this turn will be captured explicitly")
            );
        }
        if let Err(error) = stream_message(&client, &mut editor, &session, &input, remember).await {
            eprintln!("{} {error:#}", style::badge(style::Badge::Error, false));
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
    if gateway_accepts_local_token(paths, &url).await? {
        return Ok(());
    }
    start_backend()?;
    if gateway_accepts_local_token(paths, &url).await? {
        return Ok(());
    }
    bail!(
        "gateway on {url} rejected {}; stop the Hames process occupying that port and retry",
        paths.token.display()
    )
}

async fn gateway_accepts_local_token(paths: &LocalPaths, url: &str) -> Result<bool> {
    let Ok(health) = GatewayClient::health_unauthenticated(url).await else {
        return Ok(false);
    };
    if health.status != "ok" || health.protocol_version != PROTOCOL_VERSION {
        return Ok(false);
    }
    if !paths.token.exists() {
        return Ok(false);
    }
    GatewayClient::from_paths(paths)?.token_accepted().await
}

fn read_input(editor: &mut DefaultEditor) -> Result<Option<String>> {
    let mut result = String::new();
    let mut continuation = false;
    loop {
        let prompt = if continuation {
            style::continue_prompt().to_owned()
        } else {
            style::prompt()
        };
        match editor.readline(&prompt) {
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
                continuation = true;
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
    remember_next: &mut bool,
) -> Result<CommandOutcome> {
    let parts: Vec<&str> = input.split_whitespace().collect();
    match parts.first().copied().unwrap_or("") {
        "/help" => print_help(),
        "/quit" | "/exit" => return Ok(CommandOutcome::Exit),
        "/new" => {
            *remember_next = false;
            *session = client
                .create_session(cwd, &session.agent_id, provider, model, reasoning)
                .await?;
            ensure_trust(client, editor, session).await?;
            println!("{}", style::success(&format!("New session {}", session.id)));
        }
        "/clear" => {
            *remember_next = false;
            *session = client
                .create_session(cwd, &session.agent_id, provider, model, reasoning)
                .await?;
            ensure_trust(client, editor, session).await?;
            print!("\x1b[2J\x1b[H");
            io::stdout().flush()?;
            println!(
                "{}",
                style::success(&format!("Fresh session {}", session.id))
            );
        }
        "/sessions" => {
            let sessions = client.sessions().await?;
            println!("{}", style::section("Sessions"));
            if sessions.is_empty() {
                println!("  {}", style::dim("No sessions"));
            }
            for item in sessions {
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
        "/project" => {
            println!("{}", style::section("Project"));
            println!(
                "{}",
                style::key_value("Working directory", &session.working_directory)
            );
        }
        "/events" => {
            let count = parts
                .get(1)
                .map(|value| value.parse::<usize>())
                .transpose()
                .context("usage: /events [count]")?
                .unwrap_or(20);
            let history = client.history(&session.id).await?;
            let start = history.len().saturating_sub(count);
            println!("{}", style::section("Events"));
            if history[start..].is_empty() {
                println!("  {}", style::dim("No events"));
            }
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
                .fork_session(&session.id, parts.get(1).copied(), None)
                .await?;
            *provider = session.provider.clone();
            *model = session.model.clone();
            *reasoning = session.reasoning_effort.clone();
            ensure_trust(client, editor, session).await?;
            println!(
                "{}",
                style::success(&format!(
                    "Forked session {} from {}",
                    session.id,
                    session.fork_event_id.as_deref().unwrap_or("unknown event")
                ))
            );
        }
        "/agent" => {
            if let Some(agent_id) = parts.get(1) {
                *session = client.update_session_agent(&session.id, agent_id).await?;
                println!(
                    "{}",
                    style::success(&format!("Next turn will use agent {}", session.agent_id))
                );
            } else {
                println!("{}", style::section("Agents"));
                println!("{}", style::key_value("Active", &session.agent_id));
                for agent in client.agents().await? {
                    let active = if agent.id == session.agent_id {
                        "*"
                    } else {
                        " "
                    };
                    println!(
                        "{active} {:<20} {:<10} {}",
                        agent.id, agent.authority, agent.name
                    );
                }
            }
        }
        "/resume" => {
            *remember_next = false;
            let id = parts.get(1).context("usage: /resume <session-id>")?;
            *session = client.session(id).await?;
            *provider = session.provider.clone();
            *model = session.model.clone();
            *reasoning = session.reasoning_effort.clone();
            ensure_trust(client, editor, session).await?;
            println!(
                "{}",
                style::success(&format!("Resumed session {}", session.id))
            );
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
            println!(
                "{}",
                style::success(&format!("Provider: {provider} / {model}"))
            );
        }
        "/model" => {
            println!("{}", style::section("Model"));
            println!("{}", style::key_value("Provider", provider));
            println!("{}", style::key_value("Model", model));
            println!(
                "{}",
                style::key_value(
                    "Reasoning",
                    if reasoning.is_empty() {
                        "provider default"
                    } else {
                        reasoning
                    }
                )
            );
        }
        "/reasoning" => {
            let probe = client.probe_provider(provider).await?;
            let selected = probe
                .models
                .iter()
                .find(|item| item.id == *model)
                .with_context(|| format!("provider {provider} does not report model {model}"))?;
            let Some(requested) = parts.get(1) else {
                println!("{}", style::section("Reasoning"));
                println!(
                    "{}",
                    style::key_value(
                        "Current",
                        if reasoning.is_empty() {
                            "provider default"
                        } else {
                            reasoning
                        }
                    )
                );
                println!(
                    "{}",
                    style::key_value(
                        "Supported",
                        if selected.reasoning_efforts.is_empty() {
                            "default/off".to_owned()
                        } else {
                            format!("default/off/{}", selected.reasoning_efforts.join("/"))
                        }
                    )
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
                "{}",
                style::success(&format!(
                    "Reasoning effort: {}",
                    if reasoning.is_empty() {
                        "provider default"
                    } else {
                        reasoning
                    }
                ))
            );
        }
        "/status" => print_statuses(client).await?,
        "/usage" => print_usage(client, session).await?,
        "/inspect" => {
            let inspection = if let Some(run_id) = parts.get(1) {
                client.inspect_run(run_id).await?
            } else {
                let runs = client.runs(&session.id).await?;
                let latest = runs
                    .last()
                    .context("session has no model runs to inspect")?;
                client.inspect_run(&latest.run_id).await?
            };
            print_inspection(&inspection);
        }
        "/context" => {
            let context = if let Some(event_id) = parts.get(1) {
                client.inspect_context(event_id).await?
            } else {
                let runs = client.runs(&session.id).await?;
                let latest = runs
                    .last()
                    .context("session has no model runs to inspect")?;
                let inspection = client.inspect_run(&latest.run_id).await?;
                inspection
                    .contexts
                    .last()
                    .cloned()
                    .context("latest run has no compiled context")?
            };
            print_context(&context);
        }
        "/remember" => {
            let content = input.strip_prefix("/remember").unwrap_or("").trim();
            if content.is_empty() {
                *remember_next = true;
                println!(
                    "{}",
                    style::success("Memory capture armed for the next message")
                );
            } else {
                let job = client.capture_memory(&session.id, content).await?;
                println!(
                    "{}",
                    style::success(&format!("Queued memory extraction job {}", job.id))
                );
            }
        }
        "/memory" => handle_memory_command(client, session, &parts).await?,
        "/skills" => handle_skills_command(client, session, &parts).await?,
        "/plugins" => handle_plugins_command(client, &parts).await?,
        "/evolution" => handle_evolution_command(client, session, &parts).await?,
        "/correct" => {
            let content = input.strip_prefix("/correct").unwrap_or("").trim();
            if content.is_empty() {
                bail!("usage: /correct <short explanation of what Hames got wrong>");
            }
            let scar = client.submit_correction(&session.id, content).await?;
            println!(
                "{}",
                style::success(&format!(
                    "Scar {} recorded as {} ({})",
                    &scar.id[..8.min(scar.id.len())],
                    scar.status,
                    scar.title
                ))
            );
        }
        "/export" => {
            let path = parts
                .get(1)
                .context("usage: /export <path> [markdown|jsonl]")?;
            let format = parts.get(2).copied().unwrap_or("markdown");
            if !matches!(format, "markdown" | "jsonl") {
                bail!("usage: /export <path> [markdown|jsonl]");
            }
            let transcript = client.transcript(&session.id, format).await?;
            write_private_export(Path::new(path), &transcript, false)?;
            println!(
                "{}",
                style::success(&format!("Exported {format} audit transcript to {path}"))
            );
        }
        "/cancel" => println!(
            "{}",
            style::empty("No active run; press Ctrl-C while a run is active to cancel it")
        ),
        "/trust" => match parts.get(1).copied() {
            None | Some("status") => print_trust(client, session).await?,
            Some("revoke") => {
                let status = client.revoke_trust(&session.id).await?;
                println!(
                    "{}",
                    style::success(&format!("Trust revoked for {}", status.path))
                );
            }
            Some(_) => bail!("usage: /trust [status|revoke]"),
        },
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
    println!("{}", style::section("Select model"));
    println!("{}", style::key_value("Provider", &probe.id));
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
    println!("{}", style::section("Status"));
    println!("{}", style::key_value("Gateway", &health.status));
    println!(
        "{}",
        style::key_value("Default provider", &health.default_provider)
    );
    println!("{}", style::key_value("Active runs", health.active_runs));
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
        println!();
        println!("{}", style::section(&provider.id));
        println!("{}", style::key_value("Adapter", &profile.adapter));
        println!("{}", style::key_value("Endpoint", &profile.endpoint));
        if provider.reachable {
            println!("{}", style::key_value("State", "available"));
            if !profile.supported_reasoning_efforts.is_empty() {
                println!(
                    "{}",
                    style::key_value(
                        "Thinking levels",
                        profile.supported_reasoning_efforts.join("/")
                    )
                );
            }
            for model in &provider.models {
                println!(
                    "  {}  {}",
                    style::paint("1", &model.id),
                    style::dim(&model_summary(model))
                );
            }
        } else {
            println!("{}", style::key_value("State", "unavailable"));
            println!(
                "{}",
                style::key_value(
                    "Error",
                    format!(
                        "{}: {}",
                        provider
                            .error
                            .as_ref()
                            .map(|error| error.code.as_str())
                            .unwrap_or("unknown_error"),
                        provider
                            .error
                            .as_ref()
                            .map(|error| error.message.as_str())
                            .unwrap_or("unknown error")
                    )
                )
            );
            println!(
                "{}",
                style::key_value(
                    "Retryable",
                    provider.error.as_ref().is_some_and(|error| error.retryable)
                )
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
    println!("{}", style::section("Session"));
    println!("{}", style::key_value("ID", &session.id));
    println!("{}", style::key_value("Status", &session.status));
    println!(
        "{}",
        style::key_value("Directory", &session.working_directory)
    );
    println!("{}", style::key_value("Agent", &session.agent_id));
    println!("{}", style::key_value("Provider", &session.provider));
    println!("{}", style::key_value("Model", &session.model));
    println!(
        "{}",
        style::key_value(
            "Reasoning",
            if session.reasoning_effort.is_empty() {
                "provider default"
            } else {
                &session.reasoning_effort
            }
        )
    );
    println!(
        "{}",
        style::key_value(
            "Context window",
            format!(
                "{} ({})",
                session.context_window_tokens, session.context_window_source
            )
        )
    );
    println!(
        "{}",
        style::key_value(
            "Parent",
            session.parent_session_id.as_deref().unwrap_or("none")
        )
    );
    println!(
        "{}",
        style::key_value(
            "Fork event",
            session.fork_event_id.as_deref().unwrap_or("none")
        )
    );
}

async fn ensure_trust(
    client: &GatewayClient,
    editor: &mut DefaultEditor,
    session: &Session,
) -> Result<()> {
    let status = client.trust_status(&session.id).await?;
    if status.trusted {
        return Ok(());
    }
    println!("{}", style::badge(style::Badge::Approval, false));
    println!("{}", style::key_value("Directory", &status.path));
    println!(
        "  {}",
        style::dim("Hames needs permission to work in this exact folder.")
    );
    let answer = editor.readline("Trust and remember this folder? [y/N] › ")?;
    if !matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
        bail!("working directory was not trusted");
    }
    let granted = client.trust_session(&session.id).await?;
    println!("{}", style::success(&format!("Trusted {}", granted.path)));
    Ok(())
}

async fn print_trust(client: &GatewayClient, session: &Session) -> Result<()> {
    let status = client.trust_status(&session.id).await?;
    println!("{}", style::section("Trust"));
    println!("{}", style::key_value("Directory", &status.path));
    println!(
        "{}",
        style::key_value(
            "State",
            if status.trusted {
                "trusted"
            } else {
                "not trusted"
            }
        )
    );
    println!(
        "{}",
        style::key_value("Grant", status.grant_id.as_deref().unwrap_or("none"))
    );
    println!(
        "{}",
        style::key_value("Created", status.created_at.as_deref().unwrap_or("none"))
    );
    Ok(())
}

async fn print_usage(client: &GatewayClient, session: &Session) -> Result<()> {
    let usage = client.usage(&session.id).await?;
    println!("{}", style::section("Usage"));
    println!(
        "{}",
        style::key_value("Estimated input", usage.estimated_input_tokens)
    );
    println!("{}", style::key_value("Reported input", usage.input_tokens));
    println!("{}", style::key_value("Output", usage.output_tokens));
    println!("{}", style::key_value("Cached", usage.cached_input_tokens));
    println!("{}", style::key_value("Reasoning", usage.reasoning_tokens));
    println!("{}", style::key_value("Requests", usage.model_requests));
    println!(
        "{}",
        style::key_value("Cost", format!("{:.6}", usage.provider_reported_cost))
    );
    Ok(())
}

fn print_inspection(inspection: &RunInspection) {
    println!("{}", style::section("Run inspection"));
    println!("{}", style::key_value("Run", &inspection.run_id));
    println!("{}", style::key_value("Status", &inspection.status));
    println!(
        "{}",
        style::key_value("Model requests", inspection.model_requests)
    );
    println!("{}", style::key_value("Tool calls", inspection.tool_calls));
    println!(
        "{}",
        style::key_value(
            "Usage",
            format!(
                "{} estimated · {} input · {} output · {} reasoning",
                inspection.usage.estimated_input_tokens,
                inspection.usage.input_tokens,
                inspection.usage.output_tokens,
                inspection.usage.reasoning_tokens
            )
        )
    );
    println!();
    println!("{}", style::section("Timeline"));
    for item in &inspection.timeline {
        let short_id = item.event_id.get(..8).unwrap_or(&item.event_id);
        println!(
            "{:>6}  {}  {:<9}  {:<28} {}",
            item.sequence,
            short_id,
            item.channel,
            item.event_type,
            item.summary.replace('\n', " "),
        );
    }
}

fn print_context(context: &ContextInspection) {
    let manifest = &context.manifest;
    println!("{}", style::section("Context"));
    println!("{}", style::key_value("Event", &context.event_id));
    println!("{}", style::key_value("Run", &context.run_id));
    println!(
        "{}",
        style::key_value(
            "Compiler",
            format!(
                "{} ({})",
                manifest.compiler_version, manifest.estimator_version
            )
        )
    );
    println!(
        "{}",
        style::key_value(
            "Model",
            format!("{} / {}", manifest.provider, manifest.model)
        )
    );
    println!(
        "{}",
        style::key_value(
            "Reasoning",
            if manifest.reasoning_effort.is_empty() {
                "provider default"
            } else {
                &manifest.reasoning_effort
            }
        )
    );
    println!(
        "{}",
        style::key_value(
            "Window",
            format!(
                "{} ({})",
                manifest.context_window_tokens, manifest.context_window_source
            )
        )
    );
    println!(
        "{}",
        style::key_value("Input budget", manifest.input_budget_tokens)
    );
    println!(
        "{}",
        style::key_value("Estimated input", manifest.estimated_input_tokens)
    );
    println!(
        "{}",
        style::key_value("Output reserve", manifest.output_reserve_tokens)
    );
    println!();
    println!("{}", style::section("Selected sources"));
    for source in &manifest.selected_sources {
        if !source.skill_version_id.is_empty() {
            println!(
                "  {:>6} tokens  skill/{:<14} {} v{} · {} · score {:.3}",
                source.selected_tokens,
                source.source_type,
                source.skill_slug,
                source.skill_version,
                source.skill_scope,
                source.retrieval_score,
            );
        } else if source.memory_id.is_empty() {
            println!(
                "  {:>6} tokens  {:<14} {}",
                source.selected_tokens, source.source_type, source.source_id
            );
        } else {
            println!(
                "  {:>6} tokens  memory/{:<12} {} · {} · score {:.3}",
                source.selected_tokens,
                source.memory_layer,
                source.memory_id,
                source.memory_visibility,
                source.retrieval_score,
            );
        }
    }
    if !manifest.omitted_sources.is_empty() {
        println!();
        println!("{}", style::section("Omitted or compacted"));
        for source in &manifest.omitted_sources {
            println!(
                "  {:>6} tokens  {:<14} {} · {} · {}",
                source.estimated_tokens,
                source.source_type,
                source.source_id,
                source.reason,
                source.truncation,
            );
        }
    }
    println!();
    println!(
        "{}",
        style::key_value("Request hash", &manifest.request_hash)
    );
}

async fn handle_memory_command(
    client: &GatewayClient,
    session: &Session,
    parts: &[&str],
) -> Result<()> {
    match parts.get(1).copied() {
        None | Some("list") => {
            print_memories(&client.memories(&session.id, "active", "").await?);
        }
        Some("all") => {
            print_memories(&client.memories(&session.id, "all", "").await?);
        }
        Some("search") => {
            let query = parts.get(2..).unwrap_or_default().join(" ");
            if query.is_empty() {
                bail!("usage: /memory search <query>");
            }
            print_memories(&client.memories(&session.id, "active", &query).await?);
        }
        Some("show") => {
            let id = parts.get(2).context("usage: /memory show <memory-id>")?;
            print_memory_detail(&client.memory(&session.id, id).await?);
        }
        Some("proposals") => {
            print_memories(&client.memories(&session.id, "proposed", "").await?);
        }
        Some("accept" | "reject") => {
            let action = parts[1];
            let id = parts
                .get(2)
                .with_context(|| format!("usage: /memory {action} <memory-id>"))?;
            let record = client.transition_memory(&session.id, id, action).await?;
            println!(
                "{}",
                style::success(&format!("Memory {} is {}", record.id, record.status))
            );
        }
        Some("forget" | "retract") => {
            let id = parts.get(2).context("usage: /memory forget <memory-id>")?;
            let record = client.transition_memory(&session.id, id, "retract").await?;
            println!(
                "{}",
                style::success(&format!("Memory {} is {}", record.id, record.status))
            );
        }
        Some("promote") => {
            let id = parts
                .get(2)
                .context("usage: /memory promote <memory-id> <visibility>")?;
            let visibility = parts
                .get(3)
                .context("usage: /memory promote <memory-id> <visibility>")?;
            if !matches!(
                *visibility,
                "global" | "agent_private" | "workspace" | "session_team"
            ) {
                bail!("visibility must be global, agent_private, workspace, or session_team");
            }
            let record = client.promote_memory(&session.id, id, visibility).await?;
            println!(
                "{}",
                style::success(&format!(
                    "Promoted {} to {} as {}",
                    id, record.visibility, record.id
                ))
            );
        }
        Some("status") => {
            print_memory_jobs(&client.memory_jobs(&session.id).await?);
        }
        Some("retry") => {
            let id = parts
                .get(2)
                .context("usage: /memory retry <memory-job-id>")?;
            let job = client.retry_memory_job(&session.id, id).await?;
            println!(
                "{}",
                style::success(&format!("Memory retry queued for {}", job.id))
            );
        }
        Some(_) => bail!(
            "usage: /memory [list|all|search|show|proposals|accept|reject|forget|promote|status|retry]"
        ),
    }
    Ok(())
}

async fn handle_plugins_command(client: &GatewayClient, parts: &[&str]) -> Result<()> {
    match parts.get(1).copied() {
        None | Some("list") => {
            let plugins = client.plugins().await?;
            println!("{}", style::section("Plugins"));
            if plugins.is_empty() {
                println!("  {}", style::dim("None installed"));
                return Ok(());
            }
            for plugin in plugins {
                let state = if plugin.running {
                    "running"
                } else if plugin.enabled {
                    "enabled"
                } else {
                    "disabled"
                };
                println!("{:<20} {:<10} {}", plugin.id, state, plugin.name);
            }
        }
        Some("show") => {
            let id = parts.get(2).context("usage: /plugins show <id>")?;
            let plugin = client.plugin(id).await?;
            println!("{}", style::section("Plugin"));
            println!(
                "{}  {}  v{}",
                plugin.id,
                if plugin.running {
                    "running"
                } else if plugin.enabled {
                    "enabled"
                } else {
                    "disabled"
                },
                plugin.version
            );
            if !plugin.permissions.is_empty() {
                println!(
                    "{}",
                    style::key_value("Permissions", plugin.permissions.join(", "))
                );
            }
            if !plugin.tools.is_empty() {
                println!("{}", style::key_value("Tools", plugin.tools.join(", ")));
            }
            if !plugin.warning.is_empty() {
                println!("{}", style::warning(&plugin.warning));
            }
        }
        Some("proposals") => {
            let proposals = client.plugin_proposals().await?;
            println!("{}", style::section("Plugin proposals"));
            if proposals.is_empty() {
                println!("  {}", style::dim("No proposals"));
                return Ok(());
            }
            for proposal in proposals {
                println!(
                    "{}  {:<10} {}  {}",
                    &proposal.id[..8.min(proposal.id.len())],
                    proposal.status,
                    proposal.plugin_id,
                    proposal.package_path
                );
            }
        }
        Some("proposal") => {
            let id = parts.get(2).context("usage: /plugins proposal <id>")?;
            let proposal = client.plugin_proposal(id).await?;
            println!("{}", style::section("Plugin proposal"));
            println!(
                "{}  {}  {}\n{}",
                proposal.id, proposal.status, proposal.plugin_id, proposal.package_path
            );
            if !proposal.permissions.is_empty() {
                println!(
                    "{}",
                    style::key_value("Permissions", proposal.permissions.join(", "))
                );
            }
        }
        Some(_) => bail!("usage: /plugins [list|show <id>|proposals|proposal <id>]"),
    }
    Ok(())
}

async fn handle_evolution_command(
    client: &GatewayClient,
    session: &Session,
    parts: &[&str],
) -> Result<()> {
    match parts.get(1).copied() {
        None | Some("list") => {
            print_scars(&client.scars(&session.id).await?);
        }
        Some("show") => {
            let id = parts.get(2).context("usage: /evolution show <scar-id>")?;
            print_scar_detail(&client.scar(&session.id, id).await?);
        }
        Some("open" | "guarded" | "healed" | "regressed") => {
            let status = parts[1];
            let scars = client.scars(&session.id).await?;
            let filtered: Vec<Scar> = scars
                .into_iter()
                .filter(|scar| scar.status == status)
                .collect();
            print_scars(&filtered);
        }
        Some(_) => bail!("usage: /evolution [list|open|guarded|healed|regressed|show <scar-id>]"),
    }
    Ok(())
}

fn print_scars(scars: &[Scar]) {
    println!("{}", style::section("Evolution"));
    if scars.is_empty() {
        println!("  {}", style::dim("No scars recorded"));
        return;
    }
    for scar in scars {
        println!(
            "{}  {:<10} {:<8} {:<9} g{} r{}  {}",
            &scar.id[..8.min(scar.id.len())],
            scar.status,
            scar.severity,
            scar.detection,
            scar.successful_guard_count,
            scar.regression_count,
            scar.title
        );
    }
}

fn print_scar_detail(scar: &Scar) {
    println!("{}", style::section("Scar"));
    println!("{}", style::key_value("ID", &scar.id));
    println!("{}", style::key_value("Title", &scar.title));
    println!(
        "{}",
        style::key_value(
            "Status",
            format!("{} (severity {})", scar.status, scar.severity)
        )
    );
    println!("{}", style::key_value("Detection", &scar.detection));
    println!("{}", style::key_value("Signature", &scar.failure_signature));
    println!("{}", style::key_value("Description", &scar.description));
    println!("{}", style::key_value("Expected", &scar.expected_behavior));
    println!(
        "{}",
        style::key_value(
            "Guards",
            format!(
                "{} clean, {} regressions",
                scar.successful_guard_count, scar.regression_count
            )
        )
    );
    println!("{}", style::key_value("Updated", &scar.updated_at));
}

fn print_memories(records: &[MemoryRecord]) {
    println!("{}", style::section("Memories"));
    if records.is_empty() {
        println!("  {}", style::dim("No matching records"));
        return;
    }
    for record in records {
        println!(
            "{}  {:<12} {:<10} {:<13} {}",
            record.id, record.layer, record.status, record.visibility, record.summary
        );
    }
}

fn print_memory_detail(record: &MemoryRecord) {
    println!("{}", style::section("Memory"));
    println!("{}", style::key_value("ID", &record.id));
    println!("{}", style::key_value("Layer", &record.layer));
    println!("{}", style::key_value("Status", &record.status));
    println!("{}", style::key_value("Visibility", &record.visibility));
    println!("{}", style::key_value("Origin", &record.origin_kind));
    println!("{}", style::key_value("Subject", &record.subject));
    println!("{}", style::key_value("Predicate", &record.predicate));
    println!("{}", style::key_value("Value", &record.value));
    println!(
        "{}",
        style::key_value("Confidence", format!("{:.2}", record.confidence))
    );
    println!(
        "{}",
        style::key_value("Importance", format!("{:.2}", record.importance))
    );
    println!("{}", style::key_value("Summary", &record.summary));
    if !record.anchors.is_empty() {
        println!();
        println!("{}", style::section("Anchors"));
        for anchor in &record.anchors {
            println!("  {}: {}", anchor.kind, anchor.value);
        }
    }
    if !record.provenance_event_ids.is_empty() {
        println!();
        println!("{}", style::section("Provenance"));
        for event_id in &record.provenance_event_ids {
            println!("  {event_id}");
        }
    }
}

fn print_memory_jobs(jobs: &[MemoryJob]) {
    println!("{}", style::section("Memory jobs"));
    if jobs.is_empty() {
        println!("  {}", style::dim("No extraction jobs"));
        return;
    }
    for job in jobs {
        println!(
            "{}  {:<16} {:<10} attempts {}{}",
            job.id,
            job.kind,
            job.status,
            job.attempts,
            job.error_message
                .as_ref()
                .map(|message| format!(" · {message}"))
                .unwrap_or_default(),
        );
    }
}

async fn handle_skills_command(
    client: &GatewayClient,
    session: &Session,
    parts: &[&str],
) -> Result<()> {
    match parts.get(1).copied() {
        None | Some("list") => print_skills(&client.skills(&session.id, "").await?),
        Some("search") => {
            let query = parts.get(2..).unwrap_or_default().join(" ");
            if query.is_empty() {
                bail!("usage: /skills search <query>");
            }
            print_skills(&client.skills(&session.id, &query).await?);
        }
        Some("show") => {
            let slug = parts.get(2).context("usage: /skills show <id>")?;
            print_skill_detail(&client.skill(&session.id, slug).await?);
        }
        Some("history") => {
            let slug = parts.get(2).context("usage: /skills history <id>")?;
            print_skill_history(&client.skill_history(&session.id, slug).await?);
        }
        Some("jobs" | "status") => print_skill_jobs(&client.skill_jobs(&session.id).await?),
        Some("author") => {
            let goal = parts.get(2..).unwrap_or_default().join(" ");
            if goal.is_empty() {
                bail!("usage: /skills author <goal>");
            }
            let job = client
                .author_skill(&session.id, &goal, "workspace", None)
                .await?;
            println!(
                "{}",
                style::success(&format!("Autonomous authoring job {} queued", job.id))
            );
        }
        Some("correct") => {
            let slug = parts
                .get(2)
                .context("usage: /skills correct <id> <correction>")?;
            let goal = parts.get(3..).unwrap_or_default().join(" ");
            if goal.is_empty() {
                bail!("usage: /skills correct <id> <correction>");
            }
            let current = client.skill(&session.id, slug).await?;
            let job = client
                .author_skill(&session.id, &goal, &current.scope, Some(&current.skill_id))
                .await?;
            println!(
                "{}",
                style::success(&format!("Autonomous correction job {} queued", job.id))
            );
        }
        Some("retry") => {
            let id = parts.get(2).context("usage: /skills retry <job-id>")?;
            let job = client.retry_skill_job(&session.id, id).await?;
            println!(
                "{}",
                style::success(&format!("Skill retry queued for {}", job.id))
            );
        }
        Some(action @ ("pin" | "unpin" | "archive" | "restore" | "rollback")) => {
            let slug = parts
                .get(2)
                .with_context(|| format!("usage: /skills {action} <id>"))?;
            let skill = client.control_skill(&session.id, slug, action).await?;
            println!(
                "{}",
                style::success(&format!(
                    "{} v{} is {}{}",
                    skill.slug,
                    skill.version,
                    skill.status,
                    if skill.pinned { " (pinned)" } else { "" }
                ))
            );
        }
        Some(_) => bail!(
            "usage: /skills [list|search|show|history|jobs|author|correct|retry|pin|unpin|archive|restore|rollback]"
        ),
    }
    Ok(())
}

fn print_skills(skills: &[SkillSummary]) {
    println!("{}", style::section("Skills"));
    if skills.is_empty() {
        println!("  {}", style::dim("No matching active Skills"));
        return;
    }
    for skill in skills {
        println!(
            "{:<28} v{:<3} {:<10} {}{}",
            skill.slug,
            skill.version,
            skill.scope,
            skill.description,
            if skill.pinned { " · pinned" } else { "" },
        );
    }
}

fn print_skill_detail(skill: &SkillVersion) {
    println!("{}", style::section("Skill"));
    println!(
        "{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n\n{}",
        style::key_value("Name", format!("{} v{}", skill.slug, skill.version)),
        style::key_value("Status", &skill.status),
        style::key_value("Scope", &skill.scope),
        style::key_value("Hash", &skill.content_hash),
        style::key_value("Created by", &skill.created_by),
        style::key_value("Package", &skill.package_path),
        style::key_value("Tools", skill.metadata.tools.join(", ")),
        style::key_value("Triggers", skill.metadata.triggers.join(", ")),
        skill.instructions,
    );
    if !skill.metadata.scripts.is_empty() {
        println!();
        println!("{}", style::section("Scripts"));
        for script in &skill.metadata.scripts {
            println!(
                "  {} ({}) · {}",
                script.id, script.interpreter, script.description
            );
        }
    }
}

fn print_skill_history(history: &[SkillVersion]) {
    println!("{}", style::section("Skill history"));
    if history.is_empty() {
        println!("  {}", style::dim("No versions"));
        return;
    }
    for skill in history {
        println!(
            "{} v{:<3} {:<12} {}{}",
            skill.slug,
            skill.version,
            skill.status,
            skill.content_hash,
            if skill.pinned { " · pinned" } else { "" },
        );
    }
}

fn print_skill_jobs(jobs: &[SkillJob]) {
    println!("{}", style::section("Skill jobs"));
    if jobs.is_empty() {
        println!("  {}", style::dim("No authoring jobs"));
        return;
    }
    for job in jobs {
        println!(
            "{}  {:<10} {:<12} attempts {} · {}{}",
            job.id,
            job.kind,
            job.status,
            job.attempts,
            job.goal,
            job.error_message
                .as_ref()
                .map(|message| format!(" · {message}"))
                .unwrap_or_default(),
        );
    }
}

async fn stream_message(
    client: &GatewayClient,
    editor: &mut DefaultEditor,
    session: &Session,
    content: &str,
    remember: bool,
) -> Result<()> {
    let events = client.events(&session.id).await?;
    let mut after = events.iter().map(|event| event.sequence).max().unwrap_or(0);
    let response = client.event_stream(&session.id, after).await?;
    let accepted = client.send_message(&session.id, content, remember).await?;
    let run_id = accepted.run_id;
    let mut stream = Box::pin(response.bytes_stream());
    let mut decoder = SseDecoder::default();
    let mut output = RenderedOutput::default();
    let mut cancelled = false;
    let mut reconnects = 0_u8;
    let mut sheen = tokio::time::interval(Duration::from_millis(80));
    sheen.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            _ = sheen.tick() => {
                output.tick_sheen()?;
            }
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
                    let finished = process_envelope(&envelope, &run_id, &mut output)?;
                    if let Some(event) = &envelope.event {
                        after = after.max(event.sequence);
                        if event.run_id.as_deref() == Some(run_id.as_str())
                            && event.event_type == "approval.requested"
                        {
                            output.detach_activity();
                            handle_approval(client, editor, event).await?;
                        }
                    }
                    if finished {
                        output.finish_turn()?;
                        return Ok(());
                    }
                }
            }
            _ = tokio::signal::ctrl_c(), if !cancelled => {
                cancelled = true;
                client.cancel(&run_id).await?;
                output.cancel_activity()?;
                output.note_line("cancelling")?;
            }
        }
    }
}

async fn handle_approval(
    client: &GatewayClient,
    editor: &mut DefaultEditor,
    event: &Event,
) -> Result<()> {
    let approval_id = event.payload["approval_id"]
        .as_str()
        .context("approval event omitted its ID")?;
    let request_hash = event.payload["request_hash"]
        .as_str()
        .context("approval event omitted its request hash")?;
    let name = event.payload["name"].as_str().unwrap_or("unknown tool");
    let reason = event.payload["reason"]
        .as_str()
        .unwrap_or("policy confirmation");
    let arguments = serde_json::to_string_pretty(&event.payload["arguments"])?;
    println!();
    println!("{}", style::badge(style::Badge::Approval, false));
    println!("{}", style::key_value("Action", name));
    println!("{}", style::key_value("Reason", reason));
    println!("{}", style::key_value("Arguments", &arguments));
    println!("{}", style::key_value("Request hash", request_hash));
    let answer = editor.readline("Approve this exact action once? [y/N] › ")?;
    let decision = if matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
        "approved"
    } else {
        "denied"
    };
    let resolved = client
        .resolve_approval(approval_id, request_hash, decision)
        .await?;
    let settled = if resolved.status == "approved" {
        style::success(&resolved.status)
    } else {
        format!(
            "{} {}",
            style::badge(style::Badge::Error, false),
            resolved.status
        )
    };
    println!("{settled}");
    Ok(())
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
            "context.compiled" => output.note_compacting(event)?,
            "model.requested" => output.begin_turn()?,
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
                    if !content.is_empty() {
                        output.reconcile_answer(content)?;
                    }
                }
            }
            "model.tool_call" | "tool.requested" | "policy.requested" | "policy.decided"
            | "approval.requested" | "approval.resolved" | "tool.started" | "tool.completed"
            | "tool.failed" | "tool.rejected" => output.activity_event(event)?,
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
                output.fail_activity(message)?;
                output.error_line(&format!("{code}: {message}"))?;
            }
            "run.failed" => {
                let code = event.payload["code"].as_str().unwrap_or("run_failed");
                let message = event.payload["message"]
                    .as_str()
                    .unwrap_or("the agent run failed");
                output.fail_activity(message)?;
                output.error_line(&format!("{code}: {message}"))?;
                return Ok(true);
            }
            "run.completed" | "run.cancelled" => {
                if event.event_type == "run.cancelled" {
                    output.cancel_activity()?;
                }
                return Ok(true);
            }
            "scar.recorded" | "scar.opened" | "scar.regressed" | "scar.healed" => {
                let title = event.payload["title"].as_str().unwrap_or("scar");
                let status = event
                    .payload
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                output.note_line(&format!("scar {status}: {title}"))?;
            }
            "scar.guard.succeeded" => {
                let count = event.payload["successful_guard_count"]
                    .as_u64()
                    .unwrap_or(0);
                output.note_line(&format!("guard pass recorded ({count} clean)"))?;
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
        Some("response.tool_call_delta") => {
            if let Some(payload) = &envelope.payload {
                output.activity_delta(payload)?;
            }
        }
        _ => {}
    }
    Ok(false)
}

#[derive(Default)]
struct RenderedOutput {
    reasoning: String,
    answer: String,
    current: Option<style::Badge>,
    live: bool,
    distance: u16,
    body_col: usize,
    open_line: bool,
    body_started: bool,
    compacted: bool,
    activity: ActivityBoard,
    activity_lines: u16,
    activity_visible: bool,
    activity_detached: bool,
    logged_activity: Vec<String>,
    logged_category: Option<ActivityCategory>,
}

impl RenderedOutput {
    fn begin_turn(&mut self) -> Result<()> {
        let had_output = self.current.is_some()
            || self.body_started
            || !self.reasoning.is_empty()
            || !self.activity.is_empty();
        self.settle()?;
        self.settle_activity();
        if had_output {
            println!();
        }
        self.reasoning.clear();
        self.answer.clear();
        self.body_col = 0;
        self.open_line = false;
        self.body_started = false;
        self.activity.next_turn();
        self.activity_lines = 0;
        self.activity_visible = false;
        self.activity_detached = false;
        self.logged_activity.clear();
        self.logged_category = None;
        Ok(())
    }

    fn tick_sheen(&mut self) -> Result<()> {
        if self.activity_visible && self.activity.has_live_rows() && style::interactive() {
            style::advance_animation();
            return self.repaint_activity();
        }
        if !self.live {
            return Ok(());
        }
        if let Some(kind) = self.current {
            style::sweep_badge(kind, self.distance)?;
        }
        Ok(())
    }

    fn close_line(&mut self) -> Result<()> {
        if self.open_line {
            println!();
            self.distance = self.distance.saturating_add(1);
            self.body_col = 0;
            self.open_line = false;
        }
        Ok(())
    }

    fn settle(&mut self) -> Result<()> {
        self.close_line()?;
        if let (Some(kind), true) = (self.current, self.live) {
            if style::color_enabled() && self.distance > 0 {
                let mut out = io::stdout();
                write!(
                    out,
                    "\x1b[s\x1b[{}A\r\x1b[2K{}\x1b[u",
                    self.distance,
                    style::badge(kind, false)
                )?;
                out.flush()?;
            }
        }
        self.live = false;
        self.current = None;
        self.distance = 0;
        self.body_col = 0;
        self.body_started = false;
        Ok(())
    }

    fn finish_turn(&mut self) -> Result<()> {
        self.settle()?;
        self.settle_activity();
        println!();
        Ok(())
    }

    fn open_badge(&mut self, kind: style::Badge) -> Result<()> {
        if self.current == Some(kind) && self.live {
            return Ok(());
        }
        let had_activity = !self.activity.is_empty();
        if had_activity {
            self.settle_activity();
            self.activity.clear();
        }
        let spacer =
            (matches!(kind, style::Badge::Hames) && self.current.is_some()) || had_activity;
        self.settle()?;
        if spacer {
            println!();
        }
        println!("{}", style::badge(kind, true));
        self.current = Some(kind);
        self.live = true;
        self.distance = 1;
        self.body_col = 0;
        self.body_started = false;
        self.open_line = false;
        Ok(())
    }

    fn write_body(&mut self, text: &str, dim: bool) -> Result<()> {
        if text.is_empty() {
            return Ok(());
        }
        let padded = wrap_body(text, self.body_col, style::columns().max(16));
        let rendered = if dim {
            style::dim(&padded)
        } else {
            padded.clone()
        };
        print!("{rendered}");
        self.body_started = true;
        self.account_visible(&padded);
        self.open_line = !padded.ends_with('\n');
        io::stdout().flush()?;
        Ok(())
    }

    fn account_visible(&mut self, visible: &str) {
        for ch in visible.chars() {
            if ch == '\n' {
                self.distance = self.distance.saturating_add(1);
                self.body_col = 0;
            } else {
                self.body_col += UnicodeWidthChar::width(ch).unwrap_or(0);
            }
        }
    }

    fn note_compacting(&mut self, event: &Event) -> Result<()> {
        if self.compacted || !context_was_compacted(event) {
            return Ok(());
        }
        self.compacted = true;
        self.open_badge(style::Badge::Compacting)?;
        self.write_body("folding context", true)?;
        self.settle()?;
        Ok(())
    }

    fn push_reasoning(&mut self, text: &str) -> Result<()> {
        if text.is_empty() || !self.answer.is_empty() {
            return Ok(());
        }
        if matches!(self.current, Some(style::Badge::Hames)) {
            return Ok(());
        }
        self.open_badge(style::Badge::Thinking)?;
        self.write_body(text, true)?;
        self.reasoning.push_str(text);
        Ok(())
    }

    fn push_answer(&mut self, text: &str) -> Result<()> {
        if text.is_empty() {
            return Ok(());
        }
        self.open_badge(style::Badge::Hames)?;
        self.write_body(text, false)?;
        self.answer.push_str(text);
        Ok(())
    }

    fn error_line(&mut self, body: &str) -> Result<()> {
        self.settle()?;
        self.settle_activity();
        eprintln!("{}", style::badge(style::Badge::Error, false));
        eprintln!("  {body}");
        Ok(())
    }

    fn note_line(&mut self, body: &str) -> Result<()> {
        self.settle()?;
        self.settle_activity();
        println!("{} {}", style::mark(), style::dim(body));
        Ok(())
    }

    fn activity_delta(&mut self, payload: &serde_json::Value) -> Result<()> {
        let was_empty = self.activity.is_empty();
        let Some(index) = self.activity.transient_delta(payload) else {
            return Ok(());
        };
        self.show_activity_update(index, was_empty)
    }

    fn activity_event(&mut self, event: &Event) -> Result<()> {
        let was_empty = self.activity.is_empty();
        let Some(index) = self
            .activity
            .durable_event(&event.event_type, &event.payload)
        else {
            return Ok(());
        };
        self.show_activity_update(index, was_empty)
    }

    fn show_activity_update(&mut self, index: usize, was_empty: bool) -> Result<()> {
        if was_empty {
            let had_block = self.current.is_some() || self.body_started;
            self.settle()?;
            if had_block {
                println!();
            }
        }
        if style::interactive() && !self.activity_detached {
            self.repaint_activity()
        } else {
            self.append_activity_transition(index)
        }
    }

    fn repaint_activity(&mut self) -> Result<()> {
        if self.activity_detached {
            return Ok(());
        }
        let mut out = io::stdout();
        if self.activity_visible {
            for _ in 0..self.activity_lines {
                write!(out, "\x1b[1A\r\x1b[2K")?;
            }
        }
        let lines = self
            .activity
            .render_lines(style::columns().max(24), self.activity.has_live_rows());
        for line in &lines {
            writeln!(out, "{line}")?;
        }
        out.flush()?;
        self.activity_lines = u16::try_from(lines.len()).unwrap_or(u16::MAX);
        self.activity_visible = !lines.is_empty();
        Ok(())
    }

    fn append_activity_transition(&mut self, index: usize) -> Result<()> {
        let Some(line) = self.activity.row_line(index, style::columns().max(24)) else {
            return Ok(());
        };
        if self.logged_activity.get(index) == Some(&line) {
            return Ok(());
        }
        let category = self.activity.row_category(index);
        if category != self.logged_category {
            if let Some(category) = category {
                println!("{}", style::badge(category.badge(), false));
            }
            self.logged_category = category;
        }
        println!("{line}");
        if self.logged_activity.len() <= index {
            self.logged_activity.resize(index + 1, String::new());
        }
        self.logged_activity[index] = line;
        Ok(())
    }

    fn settle_activity(&mut self) {
        self.activity_lines = 0;
        self.activity_visible = false;
        self.activity_detached = false;
        self.logged_category = None;
    }

    fn detach_activity(&mut self) {
        self.activity_lines = 0;
        self.activity_visible = false;
        self.activity_detached = true;
        self.logged_category = None;
        self.logged_activity.clear();
    }

    fn cancel_activity(&mut self) -> Result<()> {
        if self.activity.is_empty() {
            return Ok(());
        }
        self.activity.cancel_live();
        if style::interactive() && !self.activity_detached {
            self.repaint_activity()
        } else {
            let rows = self.logged_activity.len().max(1);
            for index in 0..rows {
                self.append_activity_transition(index)?;
            }
            Ok(())
        }
    }

    fn fail_activity(&mut self, summary: &str) -> Result<()> {
        if self.activity.is_empty() {
            return Ok(());
        }
        self.activity.fail_live(summary);
        if style::interactive() && !self.activity_detached {
            self.repaint_activity()
        } else {
            let rows = self.logged_activity.len().max(1);
            for index in 0..rows {
                self.append_activity_transition(index)?;
            }
            Ok(())
        }
    }

    fn reconcile_reasoning(&mut self, content: &str) -> Result<()> {
        if content.is_empty() || !self.answer.is_empty() {
            return Ok(());
        }
        if let Some(suffix) = content.strip_prefix(&self.reasoning) {
            return self.push_reasoning(suffix);
        }
        Ok(())
    }

    fn reconcile_answer(&mut self, content: &str) -> Result<()> {
        if content.is_empty() {
            return Ok(());
        }
        if let Some(suffix) = content.strip_prefix(&self.answer) {
            return self.push_answer(suffix);
        }
        Ok(())
    }
}

fn wrap_body(text: &str, start_col: usize, cols: usize) -> String {
    let width = cols.saturating_sub(1).max(24);
    let mut out = String::new();
    let mut col = start_col;
    let mut at_line_start = start_col == 0;
    for ch in text.chars() {
        if at_line_start {
            out.push_str("  ");
            col = 2;
            at_line_start = false;
        }
        if ch == '\n' {
            out.push('\n');
            at_line_start = true;
            col = 0;
            continue;
        }
        let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if col + char_width > width {
            out.push('\n');
            out.push_str("  ");
            col = 2;
        }
        out.push(ch);
        col += char_width;
    }
    out
}

fn context_was_compacted(event: &Event) -> bool {
    event
        .payload
        .get("omitted_sources")
        .and_then(|value| value.as_array())
        .is_some_and(|sources| {
            sources.iter().any(|source| {
                source.get("reason").and_then(|value| value.as_str()) == Some("compacted")
                    || source
                        .get("truncation")
                        .and_then(|value| value.as_str())
                        .is_some_and(|value| value != "none" && !value.is_empty())
            })
        })
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
    println!("{}", style::section("Conversation"));
    print_help_rows(&[
        ("/new · /clear", "Start a fresh session"),
        ("/session · /sessions", "Inspect or list sessions"),
        ("/resume <id> · /fork [event]", "Resume or branch history"),
        ("/agent [id]", "Inspect or change the active agent"),
        ("/events [count]", "Show recent durable events"),
    ]);
    println!();
    println!("{}", style::section("Runtime"));
    print_help_rows(&[
        ("/project · /trust", "Inspect project and trust"),
        ("/provider [provider] [model]", "Change provider or model"),
        ("/model · /reasoning [level]", "Inspect model settings"),
        ("/status · /usage", "Inspect services and token usage"),
        (
            "/inspect [run] · /context [event]",
            "Audit a run or context",
        ),
        ("/cancel", "Use Ctrl-C during an active run"),
    ]);
    println!();
    println!("{}", style::section("Knowledge"));
    print_help_rows(&[
        ("/remember [fact]", "Capture durable memory"),
        ("/memory …", "Inspect and control memories"),
        ("/skills …", "Inspect and control Skills"),
        ("/plugins …", "Inspect plugins and proposals"),
        ("/evolution … · /correct …", "Inspect or record corrections"),
    ]);
    println!();
    println!("{}", style::section("Transcript"));
    print_help_rows(&[
        (
            "/export <path> [markdown|jsonl]",
            "Export an audit transcript",
        ),
        ("/help · /quit", "Show help or exit"),
    ]);
    println!(
        "\n  {}",
        style::dim("Create agents with: hames agent create --name Researcher")
    );
}

fn print_help_rows(rows: &[(&str, &str)]) {
    for (command, description) in rows {
        println!("  {:<40} {}", command, style::dim(description));
    }
}

#[cfg(test)]
mod tests {
    use unicode_width::UnicodeWidthStr;

    use super::{SseDecoder, wrap_body};

    #[test]
    fn sse_decoder_handles_split_frames() {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"event: one\ndata: {\"a\":").is_empty());
        assert_eq!(decoder.push(b"1}\n\n"), vec!["{\"a\":1}"]);
    }

    #[test]
    fn body_wrapping_uses_terminal_display_width() {
        let wrapped = wrap_body("wide 界 text", 0, 10);
        assert!(
            wrapped
                .lines()
                .all(|line| UnicodeWidthStr::width(line) <= 24)
        );
        assert!(wrapped.starts_with("  "));
    }
}
