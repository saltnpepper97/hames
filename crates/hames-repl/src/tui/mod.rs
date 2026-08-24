mod app;
mod view;

use std::env;
use std::io::{self, Stdout};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use app::{App, HitAction, MenuAction, MenuOption, Modal, Sheet, SheetKind};
use crossterm::event::{
    DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture, Event,
    EventStream, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent,
    MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use futures_util::StreamExt;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::api::{GatewayClient, LiveEnvelope, PROTOCOL_VERSION, PasteSpan, Session};
use crate::local::LocalPaths;
use crate::repl::ensure_gateway;

const RECENT_SESSION_SECONDS: u64 = 7 * 24 * 60 * 60;

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
    if !health.database_ready {
        bail!("gateway database is not ready");
    }
    let cwd = env::current_dir()?.canonicalize()?;
    let cwd_text = cwd.to_string_lossy();
    let session = match client
        .recent_session(&cwd_text, RECENT_SESSION_SECONDS)
        .await?
    {
        Some(session) => session,
        None => create_session(&client, &paths, None).await?,
    };
    let mut app = load_app(&client, session).await?;
    let (stream_tx, mut stream_rx) = mpsc::channel(256);
    let mut stream_task = spawn_event_stream(
        client.clone(),
        app.session.id.clone(),
        app.last_sequence,
        stream_tx.clone(),
    );
    let mut terminal = TerminalGuard::enter()?;
    let mut input = EventStream::new();
    let mut dirty = true;

    loop {
        if dirty {
            terminal.draw(&mut app)?;
        }
        let tick_delay = if app.animating() {
            Duration::from_millis(80)
        } else {
            Duration::from_secs(3600)
        };
        let effect = tokio::select! {
            event = input.next() => {
                match event {
                    Some(Ok(event)) => handle_terminal_event(&mut app, event),
                    Some(Err(error)) => {
                        app.modal = Some(Modal::Error(format!("terminal input failed: {error}")));
                        None
                    }
                    None => Some(Effect::Quit),
                }
            }
            message = stream_rx.recv() => {
                if let Some(message) = message
                    && message.session_id == app.session.id
                {
                    match message.payload {
                        StreamPayload::Envelope(envelope) => ingest_envelope(&mut app, *envelope),
                        StreamPayload::Warning(message) => app.notice = Some(message),
                    }
                }
                None
            }
            _ = tokio::time::sleep(tick_delay) => {
                app.tick = app.tick.wrapping_add(1);
                None
            }
        };
        dirty = true;
        let Some(effect) = effect else {
            continue;
        };
        if matches!(effect, Effect::Quit) {
            break;
        }

        terminal.draw(&mut app)?;
        match apply_effect(&client, &paths, &mut app, effect).await {
            Ok(Some(session)) => {
                stream_task.abort();
                app = load_app(&client, session).await?;
                stream_task = spawn_event_stream(
                    client.clone(),
                    app.session.id.clone(),
                    app.last_sequence,
                    stream_tx.clone(),
                );
            }
            Ok(None) => {}
            Err(error) => {
                app.modal = Some(Modal::Error(format!("{error:#}")));
                app.notice = None;
            }
        }
        if app.should_quit {
            break;
        }
    }
    stream_task.abort();
    let session_id = app.session.id.clone();
    drop(terminal);
    println!();
    println!("Session saved · use /resume {session_id} to continue where you left off");
    Ok(())
}

async fn create_session(
    client: &GatewayClient,
    paths: &LocalPaths,
    current: Option<&Session>,
) -> Result<Session> {
    let cwd = env::current_dir()?.canonicalize()?;
    let provider = current
        .map(|session| session.provider.clone())
        .unwrap_or(paths.configured_provider()?);
    let model = current
        .map(|session| session.model.clone())
        .unwrap_or(paths.configured_model(&provider)?);
    let reasoning = current
        .map(|session| session.reasoning_effort.clone())
        .unwrap_or(paths.configured_reasoning(&provider)?);
    let agent = current
        .map(|session| session.agent_id.as_str())
        .unwrap_or("default");
    client
        .create_session(&cwd.to_string_lossy(), agent, &provider, &model, &reasoning)
        .await
}

async fn load_app(client: &GatewayClient, session: Session) -> Result<App> {
    let (events, trust) = tokio::try_join!(
        client.history(&session.id),
        client.trust_status(&session.id)
    )?;
    Ok(App::new(session, events, trust.trusted))
}

#[derive(Debug)]
enum Effect {
    Quit,
    Trust,
    ResolveApproval(usize),
    Send(String, Vec<PasteSpan>),
    Cancel,
    Menu(MenuAction),
}

fn handle_terminal_event(app: &mut App, event: Event) -> Option<Effect> {
    match event {
        Event::Key(key) if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) => {
            handle_key(app, key)
        }
        Event::Paste(value) => {
            if app.modal.is_none() {
                app.composer.insert_paste(value);
                app.update_slash_sheet();
            }
            None
        }
        Event::Mouse(mouse) => handle_mouse(app, mouse),
        _ => None,
    }
}

fn handle_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    if app.modal.is_some() {
        return handle_modal_key(app, key);
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('k') {
        app.open_commands();
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('p') {
        if let Some(paste) = app.composer.paste_at_cursor() {
            app.modal = Some(Modal::PastePreview(paste.to_owned()));
        } else {
            app.notice = Some("Move the cursor beside a paste capsule to preview it".to_owned());
        }
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        if app.active_run.is_some() {
            app.notice = Some("Cancelling current work…".to_owned());
            return Some(Effect::Cancel);
        }
        app.composer.clear();
        app.sheet = None;
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('q') {
        return Some(Effect::Quit);
    }
    match key.code {
        KeyCode::Esc => {
            app.sheet = None;
            app.focused_thought = None;
            None
        }
        KeyCode::PageUp => {
            app.scroll = app.scroll.saturating_add(8);
            None
        }
        KeyCode::PageDown => {
            app.scroll = app.scroll.saturating_sub(8);
            None
        }
        KeyCode::Up if app.sheet.is_some() => {
            if let Some(sheet) = &mut app.sheet {
                sheet.selected = sheet.selected.saturating_sub(1);
            }
            None
        }
        KeyCode::Down if app.sheet.is_some() => {
            if let Some(sheet) = &mut app.sheet {
                sheet.selected = (sheet.selected + 1).min(sheet.options.len().saturating_sub(1));
            }
            None
        }
        KeyCode::Enter if app.sheet.is_some() => {
            let action = app.selected_sheet_action();
            app.sheet = None;
            if action.is_some() {
                app.composer.clear();
            }
            action.map(Effect::Menu)
        }
        KeyCode::Up if app.composer.is_empty() => {
            app.focus_thought(-1);
            None
        }
        KeyCode::Down if app.composer.is_empty() && app.focused_thought.is_some() => {
            app.focus_thought(1);
            None
        }
        KeyCode::Enter | KeyCode::Char(' ')
            if app.composer.is_empty() && app.focused_thought.is_some() =>
        {
            if let Some(index) = app.focused_thought {
                app.toggle_thought(index);
            }
            None
        }
        KeyCode::Enter => send_or_command(app),
        _ => {
            app.focused_thought = None;
            app.handle_composer_key(key);
            None
        }
    }
}

fn handle_modal_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    let Some(modal) = &mut app.modal else {
        return None;
    };
    match modal {
        Modal::Trust => match key.code {
            KeyCode::Enter | KeyCode::Char('t' | 'y') => Some(Effect::Trust),
            KeyCode::Esc | KeyCode::Char('q' | 'n') => Some(Effect::Quit),
            _ => None,
        },
        Modal::Approval(approval) => {
            let choices = if approval.allow_session { 3 } else { 2 };
            match key.code {
                KeyCode::Left | KeyCode::Up => {
                    approval.selected = approval.selected.saturating_sub(1);
                    None
                }
                KeyCode::Right | KeyCode::Down => {
                    approval.selected = (approval.selected + 1).min(choices - 1);
                    None
                }
                KeyCode::Char('s') if approval.allow_session => Some(Effect::ResolveApproval(0)),
                KeyCode::Char('y') => {
                    Some(Effect::ResolveApproval(usize::from(approval.allow_session)))
                }
                KeyCode::Char('n' | 'd') | KeyCode::Esc => {
                    Some(Effect::ResolveApproval(choices - 1))
                }
                KeyCode::Enter => Some(Effect::ResolveApproval(approval.selected)),
                _ => None,
            }
        }
        Modal::PastePreview(_) => match key.code {
            KeyCode::Backspace | KeyCode::Delete => {
                app.composer.remove_adjacent_paste();
                app.modal = None;
                None
            }
            KeyCode::Esc | KeyCode::Enter => {
                app.modal = None;
                None
            }
            _ => None,
        },
        Modal::Help | Modal::Session | Modal::Error(_) => {
            if matches!(key.code, KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q')) {
                app.modal = None;
            }
            None
        }
    }
}

fn handle_mouse(app: &mut App, mouse: MouseEvent) -> Option<Effect> {
    match mouse.kind {
        MouseEventKind::ScrollUp => {
            app.scroll = app.scroll.saturating_add(3);
            None
        }
        MouseEventKind::ScrollDown => {
            app.scroll = app.scroll.saturating_sub(3);
            None
        }
        MouseEventKind::Down(MouseButton::Left) => {
            let action = app
                .hits
                .iter()
                .rev()
                .find(|region| region.contains(mouse.column, mouse.row))
                .map(|region| region.action.clone());
            match action {
                Some(HitAction::ToggleThought(index)) => {
                    app.focused_thought = Some(index);
                    app.toggle_thought(index);
                    None
                }
                Some(HitAction::SelectSheet(index)) => {
                    let action = app
                        .sheet
                        .as_ref()
                        .and_then(|sheet| sheet.options.get(index))
                        .map(|option| option.action.clone());
                    app.sheet = None;
                    action.map(Effect::Menu)
                }
                Some(HitAction::Approval(index)) => Some(Effect::ResolveApproval(index)),
                Some(HitAction::TrustWorkspace) => Some(Effect::Trust),
                Some(HitAction::Quit) => Some(Effect::Quit),
                Some(HitAction::CloseModal) => {
                    app.modal = None;
                    None
                }
                Some(HitAction::OpenModes) => {
                    app.open_modes();
                    None
                }
                Some(HitAction::ShowSession) => {
                    app.modal = Some(Modal::Session);
                    None
                }
                Some(HitAction::FocusComposer) | None => None,
            }
        }
        _ => None,
    }
}

fn send_or_command(app: &mut App) -> Option<Effect> {
    let (content, pastes) = app.composer.message();
    let trimmed = content.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Some(action) = parse_command(trimmed) {
        app.composer.clear();
        app.sheet = None;
        return Some(Effect::Menu(action));
    }
    if trimmed.starts_with('/') {
        app.notice = Some(
            "That advanced command remains in `hames repl`; press Ctrl+K for TUI actions"
                .to_owned(),
        );
        return None;
    }
    if app.active_run.is_some() {
        app.notice = Some("Hames is already working; cancel or wait before sending".to_owned());
        return None;
    }
    app.composer.clear();
    app.sheet = None;
    app.notice = Some("Sending…".to_owned());
    Some(Effect::Send(content, pastes))
}

fn parse_command(value: &str) -> Option<MenuAction> {
    let mut parts = value.split_whitespace();
    match parts.next()? {
        "/new" | "/clear" => Some(MenuAction::NewSession),
        "/sessions" => Some(MenuAction::OpenSessions),
        "/fork" => Some(MenuAction::ForkSession),
        "/model" | "/provider" => Some(MenuAction::OpenModels),
        "/agent" => Some(MenuAction::OpenAgents),
        "/mode" => parts
            .next()
            .map(|mode| MenuAction::SetMode(mode.to_owned()))
            .or(Some(MenuAction::OpenModes)),
        "/session" => Some(MenuAction::ShowSession),
        "/resume" => parts.next().map(|id| MenuAction::Resume(id.to_owned())),
        "/cancel" => Some(MenuAction::CancelRun),
        "/help" => Some(MenuAction::Help),
        "/quit" | "/exit" => Some(MenuAction::Quit),
        _ => None,
    }
}

async fn apply_effect(
    client: &GatewayClient,
    paths: &LocalPaths,
    app: &mut App,
    effect: Effect,
) -> Result<Option<Session>> {
    match effect {
        Effect::Quit => app.should_quit = true,
        Effect::Trust => {
            let trust = client.trust_session(&app.session.id).await?;
            app.trusted = trust.trusted;
            app.modal = None;
            app.notice = Some("Workspace trusted for this canonical path".to_owned());
        }
        Effect::ResolveApproval(selected) => {
            let Some(Modal::Approval(approval)) = app.modal.clone() else {
                return Ok(None);
            };
            let decision = if approval.allow_session {
                match selected {
                    0 => "approved_session",
                    1 => "approved",
                    _ => "denied",
                }
            } else if selected == 0 {
                "approved"
            } else {
                "denied"
            };
            let resolved = client
                .resolve_approval(&approval.approval_id, &approval.request_hash, decision)
                .await?;
            app.modal = None;
            app.notice = Some(format!(
                "Permission {} ({})",
                resolved.status, resolved.approval_scope
            ));
        }
        Effect::Send(content, pastes) => {
            let accepted = client
                .send_message_with_pastes(&app.session.id, &content, false, &pastes)
                .await?;
            app.active_run = Some(accepted.run_id);
            app.notice = None;
        }
        Effect::Cancel => {
            if let Some(run_id) = app.active_run.clone() {
                client.cancel(&run_id).await?;
            }
        }
        Effect::Menu(action) => return apply_menu_action(client, paths, app, action).await,
    }
    Ok(None)
}

async fn apply_menu_action(
    client: &GatewayClient,
    paths: &LocalPaths,
    app: &mut App,
    action: MenuAction,
) -> Result<Option<Session>> {
    match action {
        MenuAction::NewSession => {
            app.notice = Some("Starting a new session…".to_owned());
            return Ok(Some(
                create_session(client, paths, Some(&app.session)).await?,
            ));
        }
        MenuAction::OpenSessions => {
            app.notice = Some("Loading sessions…".to_owned());
            let sessions = client.sessions().await?;
            app.notice = None;
            app.sheet = Some(Sheet {
                kind: SheetKind::Sessions,
                title: "Open sessions".to_owned(),
                options: sessions
                    .into_iter()
                    .filter(|session| session.status == "open")
                    .take(40)
                    .map(|session| MenuOption {
                        label: session
                            .title
                            .unwrap_or_else(|| format!("Session {}", short_id(&session.id))),
                        detail: format!(
                            "{} · {} · {}",
                            compact_home(&session.working_directory),
                            session.model,
                            session.interaction_mode
                        ),
                        action: MenuAction::Resume(session.id),
                    })
                    .collect(),
                selected: 0,
            });
        }
        MenuAction::ForkSession => {
            app.notice = Some("Forking session…".to_owned());
            return Ok(Some(
                client.fork_session(&app.session.id, None, None).await?,
            ));
        }
        MenuAction::OpenModels => {
            app.notice = Some("Loading provider models…".to_owned());
            let profiles = client.providers().await?;
            let mut options = Vec::new();
            for profile in profiles {
                match client.probe_provider(&profile.id).await {
                    Ok(probe) if probe.reachable => {
                        for model in probe.models {
                            options.push(MenuOption {
                                label: model.id.clone(),
                                detail: format!(
                                    "{} · {}",
                                    profile.id,
                                    model.parameter_size.unwrap_or_else(|| model.status.clone())
                                ),
                                action: MenuAction::SetModel {
                                    provider: profile.id.clone(),
                                    model: model.id,
                                    reasoning: profile.default_reasoning_effort.clone(),
                                },
                            });
                        }
                    }
                    Ok(_) => options.push(MenuOption {
                        label: profile.id,
                        detail: "provider unavailable".to_owned(),
                        action: MenuAction::OpenModels,
                    }),
                    Err(error) => options.push(MenuOption {
                        label: profile.id,
                        detail: format!("probe failed: {error}"),
                        action: MenuAction::OpenModels,
                    }),
                }
            }
            app.notice = None;
            app.sheet = Some(Sheet {
                kind: SheetKind::Models,
                title: "Provider and model".to_owned(),
                options,
                selected: 0,
            });
        }
        MenuAction::OpenAgents => {
            app.notice = Some("Loading agents…".to_owned());
            let agents = client.agents().await?;
            app.notice = None;
            app.sheet = Some(Sheet {
                kind: SheetKind::Agents,
                title: "Agent capsule".to_owned(),
                options: agents
                    .into_iter()
                    .map(|agent| MenuOption {
                        label: agent.name,
                        detail: agent.authority,
                        action: MenuAction::SetAgent(agent.id),
                    })
                    .collect(),
                selected: 0,
            });
        }
        MenuAction::OpenModes => app.open_modes(),
        MenuAction::ShowSession => app.modal = Some(Modal::Session),
        MenuAction::Help => app.modal = Some(Modal::Help),
        MenuAction::CancelRun => {
            if let Some(run_id) = app.active_run.clone() {
                client.cancel(&run_id).await?;
                app.notice = Some("Cancelling current work…".to_owned());
            } else {
                app.notice = Some("No active work to cancel".to_owned());
            }
        }
        MenuAction::Quit => app.should_quit = true,
        MenuAction::Resume(id) => return Ok(Some(client.session(&id).await?)),
        MenuAction::SetModel {
            provider,
            model,
            reasoning,
        } => {
            app.session = client
                .update_session(&app.session.id, &provider, &model, &reasoning)
                .await?;
            app.context_window = app.session.context_window_tokens;
            app.notice = Some(format!("Using {provider} / {model}"));
        }
        MenuAction::SetAgent(agent) => {
            app.session = client.update_session_agent(&app.session.id, &agent).await?;
            app.notice = Some(format!("Agent changed to {agent}"));
        }
        MenuAction::SetMode(mode) => {
            if !matches!(mode.as_str(), "manual" | "auto" | "plan") {
                bail!("mode must be manual, auto, or plan");
            }
            app.session = client.update_session_mode(&app.session.id, &mode).await?;
            app.notice = Some(match mode.as_str() {
                "manual" => "Manual mode · ask before every edit".to_owned(),
                "plan" => "Plan mode · inspect and test without code writes".to_owned(),
                _ => "Auto mode · ask only for dangerous actions".to_owned(),
            });
        }
    }
    Ok(None)
}

fn ingest_envelope(app: &mut App, envelope: LiveEnvelope) {
    if envelope.durable {
        if let Some(event) = envelope.event {
            app.ingest_durable(event, true);
        }
    } else if let (Some(run_id), Some(event_type), Some(payload)) =
        (envelope.run_id, envelope.event_type, envelope.payload)
    {
        app.ingest_transient(&run_id, &event_type, &payload);
    }
}

struct StreamMessage {
    session_id: String,
    payload: StreamPayload,
}

enum StreamPayload {
    Envelope(Box<LiveEnvelope>),
    Warning(String),
}

fn spawn_event_stream(
    client: GatewayClient,
    session_id: String,
    after: u64,
    tx: mpsc::Sender<StreamMessage>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        if let Err(error) = stream_events(client, session_id.clone(), after, tx.clone()).await {
            let _ = tx
                .send(StreamMessage {
                    session_id,
                    payload: StreamPayload::Warning(format!("Live updates paused: {error:#}")),
                })
                .await;
        }
    })
}

async fn stream_events(
    client: GatewayClient,
    session_id: String,
    mut after: u64,
    tx: mpsc::Sender<StreamMessage>,
) -> Result<()> {
    let mut failures = 0_u8;
    loop {
        let response = match client.event_stream(&session_id, after).await {
            Ok(response) => response,
            Err(error) => {
                failures += 1;
                if failures >= 4 {
                    return Err(error).context("gateway event stream repeatedly failed");
                }
                tokio::time::sleep(Duration::from_millis(250 * u64::from(failures))).await;
                continue;
            }
        };
        let mut bytes = response.bytes_stream();
        let mut decoder = SseDecoder::default();
        while let Some(chunk) = bytes.next().await {
            let chunk = match chunk {
                Ok(chunk) => chunk,
                Err(_) => break,
            };
            failures = 0;
            for data in decoder.push(&chunk) {
                let envelope: LiveEnvelope =
                    serde_json::from_str(&data).context("gateway emitted malformed SSE data")?;
                if let Some(event) = &envelope.event {
                    after = after.max(event.sequence);
                }
                if tx
                    .send(StreamMessage {
                        session_id: session_id.clone(),
                        payload: StreamPayload::Envelope(Box::new(envelope)),
                    })
                    .await
                    .is_err()
                {
                    return Ok(());
                }
            }
        }
        failures += 1;
        if failures >= 4 {
            bail!("gateway event stream repeatedly ended");
        }
        tokio::time::sleep(Duration::from_millis(250 * u64::from(failures))).await;
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

struct TerminalGuard {
    terminal: Terminal<CrosstermBackend<Stdout>>,
}

impl TerminalGuard {
    fn enter() -> Result<Self> {
        enable_raw_mode()?;
        let mut stdout = io::stdout();
        if let Err(error) = execute!(
            stdout,
            EnterAlternateScreen,
            EnableMouseCapture,
            EnableBracketedPaste
        ) {
            let _ = disable_raw_mode();
            return Err(error.into());
        }
        let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
        terminal.clear()?;
        Ok(Self { terminal })
    }

    fn draw(&mut self, app: &mut App) -> Result<()> {
        self.terminal.draw(|frame| view::draw(frame, app))?;
        Ok(())
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = execute!(
            self.terminal.backend_mut(),
            DisableBracketedPaste,
            DisableMouseCapture,
            LeaveAlternateScreen
        );
        let _ = self.terminal.show_cursor();
    }
}

fn short_id(value: &str) -> &str {
    value.get(..8).unwrap_or(value)
}

fn compact_home(value: &str) -> String {
    env::var("HOME")
        .ok()
        .and_then(|home| value.strip_prefix(&home).map(|suffix| format!("~{suffix}")))
        .unwrap_or_else(|| value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::{SseDecoder, parse_command};
    use crate::tui::app::MenuAction;

    #[test]
    fn sse_decoder_handles_fragmented_frames() {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"data: {\"dur").is_empty());
        assert_eq!(
            decoder.push(b"able\":true}\n\n"),
            vec!["{\"durable\":true}"]
        );
    }

    #[test]
    fn core_slash_commands_route_to_tui_actions() {
        assert!(matches!(
            parse_command("/new"),
            Some(MenuAction::NewSession)
        ));
        assert!(
            matches!(parse_command("/mode plan"), Some(MenuAction::SetMode(mode)) if mode == "plan")
        );
        assert!(
            matches!(parse_command("/resume abc"), Some(MenuAction::Resume(id)) if id == "abc")
        );
        assert!(parse_command("/memory list").is_none());
    }
}
