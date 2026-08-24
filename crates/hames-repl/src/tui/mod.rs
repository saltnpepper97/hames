mod app;
mod view;

use std::env;
use std::io::{self, Stdout, Write};
use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use app::{
    App, HitAction, MenuAction, MenuOption, Modal, ScrollDrag, ScrollTarget, Sheet, SheetKind,
    ThemeKind,
};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use crossterm::event::{
    DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture, Event,
    EventStream, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, KeyboardEnhancementFlags,
    MouseButton, MouseEvent, MouseEventKind, PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
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

use crate::api::{
    GatewayClient, LiveEnvelope, PROTOCOL_VERSION, PasteSpan, ProviderModel, Session,
};
use crate::local::{LocalPaths, write_private_export};
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
                let theme = app.theme;
                app = load_app(&client, session).await?;
                app.theme = theme;
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
    Copy(String),
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
    app.clear_transcript_selection();
    if app.modal.is_some() {
        return handle_modal_key(app, key);
    }
    if key.code == KeyCode::BackTab
        || (key.code == KeyCode::Tab && key.modifiers.contains(KeyModifiers::SHIFT))
    {
        return Some(Effect::Menu(MenuAction::SetMode(
            next_mode(&app.session.interaction_mode).to_owned(),
        )));
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
            if app.active_run.is_some() {
                app.notice = Some("Interrupting current work…".to_owned());
                return Some(Effect::Cancel);
            }
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
        KeyCode::Enter
            if key
                .modifiers
                .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT) =>
        {
            app.handle_composer_key(key);
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
        Modal::Help | Modal::Session | Modal::Error(_) | Modal::Info { .. } => {
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
            app.clear_transcript_selection();
            if mouse_over_composer(app, mouse.column, mouse.row) {
                scroll_composer(app, -3);
            } else {
                app.scroll = app.scroll.saturating_add(3);
            }
            None
        }
        MouseEventKind::ScrollDown => {
            app.clear_transcript_selection();
            if mouse_over_composer(app, mouse.column, mouse.row) {
                scroll_composer(app, 3);
            } else {
                app.scroll = app.scroll.saturating_sub(3);
            }
            None
        }
        MouseEventKind::Down(MouseButton::Left) => {
            let region = app
                .hits
                .iter()
                .rev()
                .find(|region| region.contains(mouse.column, mouse.row))
                .cloned();
            match region.as_ref().map(|item| item.action.clone()) {
                Some(HitAction::Scrollbar {
                    target,
                    content_len,
                    viewport_len,
                }) => {
                    app.clear_transcript_selection();
                    let region = region.expect("scrollbar region");
                    let drag = ScrollDrag {
                        target,
                        y: region.y,
                        height: region.height,
                        max_top: content_len.saturating_sub(viewport_len),
                    };
                    scroll_to_pointer(app, &drag, mouse.row);
                    app.scroll_drag = Some(drag);
                    None
                }
                Some(HitAction::ToggleThought(index)) => {
                    if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                        app.begin_transcript_selection(point);
                        app.pending_thought_toggle = Some(index);
                    }
                    None
                }
                Some(HitAction::SelectSheet(index)) => {
                    app.clear_transcript_selection();
                    let action = app
                        .sheet
                        .as_ref()
                        .and_then(|sheet| sheet.options.get(index))
                        .map(|option| option.action.clone());
                    app.sheet = None;
                    action.map(Effect::Menu)
                }
                Some(HitAction::Approval(index)) => {
                    app.clear_transcript_selection();
                    Some(Effect::ResolveApproval(index))
                }
                Some(HitAction::TrustWorkspace) => {
                    app.clear_transcript_selection();
                    Some(Effect::Trust)
                }
                Some(HitAction::Quit) => {
                    app.clear_transcript_selection();
                    Some(Effect::Quit)
                }
                Some(HitAction::CloseModal) => {
                    app.clear_transcript_selection();
                    app.modal = None;
                    None
                }
                Some(HitAction::ShowSession) => {
                    app.clear_transcript_selection();
                    app.modal = Some(Modal::Session);
                    None
                }
                Some(HitAction::FocusComposer) => {
                    app.clear_transcript_selection();
                    None
                }
                None => {
                    if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                        app.begin_transcript_selection(point);
                    } else {
                        app.clear_transcript_selection();
                    }
                    None
                }
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            if let Some(drag) = app.scroll_drag.clone() {
                scroll_to_pointer(app, &drag, mouse.row);
            } else if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                app.update_transcript_selection(point);
            }
            None
        }
        MouseEventKind::Up(MouseButton::Left) => {
            app.scroll_drag = None;
            if app.selecting_transcript {
                if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                    app.update_transcript_selection(point);
                }
                let pending_thought = app.pending_thought_toggle.take();
                if let Some(text) = app.finish_transcript_selection() {
                    return Some(Effect::Copy(text));
                }
                if let Some(index) = pending_thought {
                    app.focused_thought = Some(index);
                    app.toggle_thought(index);
                }
            }
            None
        }
        _ => None,
    }
}

fn mouse_over_composer(app: &App, x: u16, y: u16) -> bool {
    app.hits.iter().rev().any(|region| {
        region.contains(x, y)
            && matches!(
                region.action,
                HitAction::FocusComposer
                    | HitAction::Scrollbar {
                        target: ScrollTarget::Composer,
                        ..
                    }
            )
    })
}

fn scroll_composer(app: &mut App, delta: isize) {
    let max_top = app.hits.iter().find_map(|region| match region.action {
        HitAction::Scrollbar {
            target: ScrollTarget::Composer,
            content_len,
            viewport_len,
        } => Some(content_len.saturating_sub(viewport_len)),
        _ => None,
    });
    let Some(max_top) = max_top else {
        return;
    };
    let current = app.composer_scroll.unwrap_or(max_top);
    app.composer_scroll = Some(if delta.is_negative() {
        current.saturating_sub(delta.unsigned_abs())
    } else {
        current.saturating_add(delta as usize).min(max_top)
    });
}

fn scroll_to_pointer(app: &mut App, drag: &ScrollDrag, row: u16) {
    let top = pointer_top(drag, row);
    match drag.target {
        ScrollTarget::Transcript => app.scroll = drag.max_top.saturating_sub(top),
        ScrollTarget::Composer => app.composer_scroll = Some(top),
    }
}

fn pointer_top(drag: &ScrollDrag, row: u16) -> usize {
    let track = usize::from(drag.height.saturating_sub(1).max(1));
    let offset = usize::from(
        row.saturating_sub(drag.y)
            .min(drag.height.saturating_sub(1)),
    );
    drag.max_top.saturating_mul(offset) / track
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
        "/new" => Some(MenuAction::NewSession),
        "/sessions" => Some(MenuAction::OpenSessions),
        "/fork" => Some(MenuAction::ForkSession),
        "/model" | "/provider" => Some(MenuAction::OpenModels),
        "/effort" | "/reasoning" => parts
            .next()
            .map(|effort| MenuAction::SetEffort(effort.to_owned()))
            .or(Some(MenuAction::OpenEfforts)),
        "/agent" => Some(MenuAction::OpenAgents),
        "/mode" => parts
            .next()
            .map(|mode| MenuAction::SetMode(mode.to_owned()))
            .or(Some(MenuAction::OpenModes)),
        "/theme" | "/themes" => match parts.next() {
            Some("hames") => Some(MenuAction::SetTheme(ThemeKind::Hames)),
            Some("terminal") => Some(MenuAction::SetTheme(ThemeKind::Terminal)),
            Some(_) => None,
            None => Some(MenuAction::OpenThemes),
        },
        "/session" | "/status" => Some(MenuAction::ShowSession),
        "/title" => {
            let title = parts.collect::<Vec<_>>().join(" ");
            (!title.is_empty()).then_some(MenuAction::SetTitle(title))
        }
        "/project" | "/trust" => match parts.next() {
            Some("revoke") => Some(MenuAction::RevokeTrust),
            _ => Some(MenuAction::Trust),
        },
        "/gateway" => Some(MenuAction::Status),
        "/usage" => Some(MenuAction::Usage),
        "/events" => Some(MenuAction::Events),
        "/inspect" => Some(MenuAction::Inspect),
        "/context" => Some(MenuAction::Context),
        "/memory" => Some(MenuAction::Memory),
        "/skills" => Some(MenuAction::Skills),
        "/evolution" | "/scars" => Some(MenuAction::Scars),
        "/plugins" => Some(MenuAction::Plugins),
        "/export" => parts.next().map(|path| MenuAction::Export {
            path: path.to_owned(),
            format: parts.next().unwrap_or("markdown").to_owned(),
        }),
        "/remember" => {
            let content = parts.collect::<Vec<_>>().join(" ");
            (!content.is_empty()).then_some(MenuAction::CaptureMemory(content))
        }
        "/correct" => {
            let content = parts.collect::<Vec<_>>().join(" ");
            (!content.is_empty()).then_some(MenuAction::Correct(content))
        }
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
            app.run_started_at = Some(std::time::Instant::now());
            app.notice = None;
        }
        Effect::Cancel => {
            if let Some(run_id) = app.active_run.clone() {
                client.cancel(&run_id).await?;
            }
        }
        Effect::Copy(text) => {
            copy_to_clipboard(&text)?;
            app.show_copy_notice(text.chars().count());
        }
        Effect::Menu(action) => return apply_menu_action(client, paths, app, action).await,
    }
    Ok(None)
}

fn copy_to_clipboard(text: &str) -> Result<()> {
    let encoded = BASE64_STANDARD.encode(text.as_bytes());
    let mut stdout = io::stdout();
    write!(stdout, "\x1b]52;c;{encoded}\x07")?;
    stdout.flush()?;
    Ok(())
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
                                action: MenuAction::ChooseModel {
                                    provider: profile.id.clone(),
                                    model: model.id,
                                },
                            });
                        }
                    }
                    Ok(_) | Err(_) => {}
                }
            }
            if options.is_empty() {
                app.notice = Some("No reachable configured models".to_owned());
                app.sheet = None;
            } else {
                app.notice = None;
                app.sheet = Some(Sheet {
                    kind: SheetKind::Models,
                    title: "Provider and model".to_owned(),
                    options,
                    selected: 0,
                });
            }
        }
        MenuAction::ChooseModel { provider, model } => {
            app.notice = Some("Checking model capabilities…".to_owned());
            let probe = client.probe_provider(&provider).await?;
            let selected = probe
                .models
                .into_iter()
                .find(|item| item.id == model)
                .with_context(|| format!("model {model} is no longer available"))?;
            let efforts = model_efforts(&selected);
            if efforts.is_empty() {
                app.session = client
                    .update_session(&app.session.id, &provider, &model, "off")
                    .await?;
                app.context_window = app.session.context_window_tokens;
                app.sheet = None;
                app.notice = Some(format!("Using {provider} / {model} · reasoning off"));
                return Ok(None);
            }
            app.notice = None;
            app.sheet = Some(Sheet {
                kind: SheetKind::Efforts,
                title: format!("Reasoning effort · {model}"),
                options: efforts
                    .into_iter()
                    .map(|effort| MenuOption {
                        label: effort.clone(),
                        detail: "select to finish".to_owned(),
                        action: MenuAction::SetModel {
                            provider: provider.clone(),
                            model: model.clone(),
                            reasoning: if effort == "default" {
                                String::new()
                            } else {
                                effort
                            },
                        },
                    })
                    .collect(),
                selected: 0,
            });
        }
        MenuAction::OpenEfforts => {
            app.notice = Some("Checking model capabilities…".to_owned());
            let probe = client.probe_provider(&app.session.provider).await?;
            let selected = probe
                .models
                .into_iter()
                .find(|model| model.id == app.session.model)
                .with_context(|| format!("model {} is no longer available", app.session.model))?;
            let efforts = model_efforts(&selected);
            if efforts.is_empty() {
                app.sheet = None;
                app.notice = Some(format!(
                    "{} does not offer reasoning levels",
                    app.session.model
                ));
                return Ok(None);
            }
            app.notice = None;
            app.sheet = Some(Sheet {
                kind: SheetKind::Efforts,
                title: format!("Reasoning effort · {}", app.session.model),
                options: efforts
                    .into_iter()
                    .map(|effort| MenuOption {
                        label: effort.clone(),
                        detail: if effort_label(&app.session.reasoning_effort) == effort {
                            "current".to_owned()
                        } else {
                            "".to_owned()
                        },
                        action: MenuAction::SetEffort(effort),
                    })
                    .collect(),
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
        MenuAction::OpenThemes => app.open_themes(),
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
        MenuAction::Status => {
            let health = client.health().await?;
            app.modal = Some(info(
                "Gateway status",
                vec![
                    format!("Status       {}", health.status),
                    format!("Core         {}", health.version),
                    format!("Protocol     {}", health.protocol_version),
                    format!(
                        "Database     {}",
                        if health.database_ready {
                            "ready"
                        } else {
                            "not ready"
                        }
                    ),
                    format!("Active runs  {}", health.active_runs),
                    format!("Provider     {}", health.default_provider),
                ],
            ));
        }
        MenuAction::Usage => {
            let usage = client.usage(&app.session.id).await?;
            app.modal = Some(info(
                "Session usage",
                vec![
                    format!("Estimated input  {}", usage.estimated_input_tokens),
                    format!("Provider input   {}", usage.input_tokens),
                    format!("Output           {}", usage.output_tokens),
                    format!("Reasoning        {}", usage.reasoning_tokens),
                    format!("Cached input     {}", usage.cached_input_tokens),
                    format!("Model requests   {}", usage.model_requests),
                ],
            ));
        }
        MenuAction::Events => {
            let events = client.events(&app.session.id).await?;
            let lines = events
                .into_iter()
                .rev()
                .take(18)
                .map(|event| {
                    format!(
                        "{:>6}  {:<26}  {}",
                        event.sequence,
                        event.event_type,
                        short_id(&event.id)
                    )
                })
                .collect();
            app.modal = Some(info("Recent durable events", lines));
        }
        MenuAction::Inspect => {
            let run = client
                .runs(&app.session.id)
                .await?
                .into_iter()
                .next()
                .context("this session has no runs to inspect")?;
            let inspection = client.inspect_run(&run.run_id).await?;
            let mut lines = vec![
                format!("Run     {}", inspection.run_id),
                format!("Status  {}", inspection.status),
                format!(
                    "Model requests  {} · tool calls  {}",
                    inspection.model_requests, inspection.tool_calls
                ),
                String::new(),
            ];
            lines.extend(inspection.timeline.into_iter().rev().take(14).map(|item| {
                format!(
                    "{:>6}  {:<10}  {}",
                    item.sequence,
                    item.channel,
                    item.summary.replace('\n', " ")
                )
            }));
            app.modal = Some(info("Latest run", lines));
        }
        MenuAction::Context => {
            let event = client
                .events(&app.session.id)
                .await?
                .into_iter()
                .rev()
                .find(|event| event.event_type == "context.compiled")
                .context("this session has no compiled context yet")?;
            let context = client.inspect_context(&event.id).await?;
            let manifest = context.manifest;
            app.modal = Some(info(
                "Latest context",
                vec![
                    format!("Model       {} / {}", manifest.provider, manifest.model),
                    format!("Effort      {}", effort_label(&manifest.reasoning_effort)),
                    format!(
                        "Window      {} ({})",
                        manifest.context_window_tokens, manifest.context_window_source
                    ),
                    format!("Input       {} estimated", manifest.estimated_input_tokens),
                    format!("Selected    {} sources", manifest.selected_sources.len()),
                    format!("Omitted     {} sources", manifest.omitted_sources.len()),
                    format!("Request     {}", manifest.request_hash),
                ],
            ));
        }
        MenuAction::Memory => {
            let memories = client.memories(&app.session.id, "active", "").await?;
            let mut lines = vec![format!("{} active records", memories.len()), String::new()];
            lines.extend(memories.into_iter().take(16).map(|memory| {
                format!(
                    "{:<12} {:<12} {}",
                    memory.layer, memory.visibility, memory.summary
                )
            }));
            app.modal = Some(info("Memory", lines));
        }
        MenuAction::Skills => {
            let skills = client.skills(&app.session.id, "").await?;
            let mut lines = vec![format!("{} catalog entries", skills.len()), String::new()];
            lines.extend(
                skills.into_iter().take(16).map(|skill| {
                    format!("{:<10} v{:<3} {}", skill.status, skill.version, skill.name)
                }),
            );
            app.modal = Some(info("Skills", lines));
        }
        MenuAction::Scars => {
            let scars = client.scars(&app.session.id).await?;
            let mut lines = vec![format!("{} visible Scars", scars.len()), String::new()];
            lines.extend(
                scars
                    .into_iter()
                    .take(16)
                    .map(|scar| format!("{:<10} {:<8} {}", scar.status, scar.severity, scar.title)),
            );
            app.modal = Some(info("Scars and evolution", lines));
        }
        MenuAction::Plugins => {
            let plugins = client.plugins().await?;
            let mut lines = vec![
                format!("{} installed plugins", plugins.len()),
                String::new(),
            ];
            lines.extend(plugins.into_iter().take(16).map(|plugin| {
                format!(
                    "{:<10} {:<10} {}",
                    if plugin.enabled {
                        "enabled"
                    } else {
                        "disabled"
                    },
                    if plugin.running { "running" } else { "stopped" },
                    plugin.name
                )
            }));
            app.modal = Some(info("Plugins", lines));
        }
        MenuAction::Trust => {
            let trust = client.trust_status(&app.session.id).await?;
            app.modal = Some(info(
                "Project and trust",
                vec![
                    format!("Workspace  {}", compact_home(&trust.path)),
                    format!("Trusted    {}", trust.trusted),
                    format!(
                        "Grant      {}",
                        trust.grant_id.unwrap_or_else(|| "—".to_owned())
                    ),
                    String::new(),
                    "Use /trust revoke to remove this exact canonical grant.".to_owned(),
                ],
            ));
        }
        MenuAction::RevokeTrust => {
            client.revoke_trust(&app.session.id).await?;
            app.trusted = false;
            app.modal = Some(Modal::Trust);
        }
        MenuAction::Export { path, format } => {
            if !matches!(format.as_str(), "markdown" | "jsonl") {
                bail!("export format must be markdown or jsonl");
            }
            let transcript = client.transcript(&app.session.id, &format).await?;
            write_private_export(Path::new(&path), &transcript, false)?;
            app.notice = Some(format!("Exported {format} transcript to {path}"));
        }
        MenuAction::CaptureMemory(content) => {
            let job = client.capture_memory(&app.session.id, &content).await?;
            app.notice = Some(format!("Memory capture queued · {}", job.id));
        }
        MenuAction::Correct(content) => {
            let scar = client.submit_correction(&app.session.id, &content).await?;
            app.notice = Some(format!("Correction recorded · {}", scar.title));
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
        MenuAction::SetEffort(effort) => {
            let effort = if effort == "default" { "" } else { &effort };
            app.session = client
                .update_session(
                    &app.session.id,
                    &app.session.provider,
                    &app.session.model,
                    effort,
                )
                .await?;
            app.notice = Some(format!(
                "Reasoning effort · {}",
                effort_label(&app.session.reasoning_effort)
            ));
        }
        MenuAction::SetTitle(title) => {
            app.session = client.update_session_title(&app.session.id, &title).await?;
            app.notice = Some(format!(
                "Session titled · {}",
                app.session.title.as_deref().unwrap_or("New session")
            ));
        }
        MenuAction::SetTheme(theme) => {
            app.theme = theme;
            app.sheet = None;
            app.notice = Some(format!("Theme · {}", theme.label()));
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
            EnableBracketedPaste,
            PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
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
            PopKeyboardEnhancementFlags,
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

fn effort_label(value: &str) -> &str {
    if value.is_empty() { "default" } else { value }
}

fn model_efforts(model: &ProviderModel) -> Vec<String> {
    if model.reasoning_supported != Some(true) {
        return Vec::new();
    }
    if model.reasoning_efforts.is_empty() || model.reasoning_efforts == ["on"] {
        return vec!["on".to_owned(), "off".to_owned()];
    }
    let mut efforts = model.reasoning_efforts.clone();
    if !efforts.iter().any(|effort| effort == "default") {
        efforts.insert(0, "default".to_owned());
    }
    if !efforts.iter().any(|effort| effort == "off") {
        efforts.insert(1, "off".to_owned());
    }
    efforts
}

fn next_mode(mode: &str) -> &str {
    match mode {
        "manual" => "auto",
        "auto" => "plan",
        _ => "manual",
    }
}

fn info(title: &str, lines: Vec<String>) -> Modal {
    Modal::Info {
        title: title.to_owned(),
        lines,
    }
}

#[cfg(test)]
mod tests {
    use crossterm::event::{
        KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
    };

    use super::{
        Effect, SseDecoder, handle_key, handle_mouse, model_efforts, next_mode, parse_command,
        pointer_top,
    };
    use crate::api::{ProviderModel, Session};
    use crate::tui::app::{
        App, MenuAction, ScrollDrag, ScrollTarget, ThemeKind, TranscriptViewport,
    };

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
        assert!(parse_command("/clear").is_none());
        assert!(matches!(
            parse_command("/status"),
            Some(MenuAction::ShowSession)
        ));
        assert!(matches!(
            parse_command("/gateway"),
            Some(MenuAction::Status)
        ));
        assert!(
            matches!(parse_command("/mode plan"), Some(MenuAction::SetMode(mode)) if mode == "plan")
        );
        assert!(
            matches!(parse_command("/resume abc"), Some(MenuAction::Resume(id)) if id == "abc")
        );
        assert!(matches!(
            parse_command("/memory list"),
            Some(MenuAction::Memory)
        ));
        assert!(matches!(
            parse_command("/effort xhigh"),
            Some(MenuAction::SetEffort(effort)) if effort == "xhigh"
        ));
        assert!(matches!(
            parse_command("/themes terminal"),
            Some(MenuAction::SetTheme(ThemeKind::Terminal))
        ));
        assert!(matches!(
            parse_command("/title Refine the TUI"),
            Some(MenuAction::SetTitle(title)) if title == "Refine the TUI"
        ));
    }

    #[test]
    fn shift_tab_mode_cycle_is_stable() {
        assert_eq!(next_mode("manual"), "auto");
        assert_eq!(next_mode("auto"), "plan");
        assert_eq!(next_mode("plan"), "manual");
    }

    #[test]
    fn scrollbar_pointer_maps_the_full_track() {
        let drag = ScrollDrag {
            target: ScrollTarget::Transcript,
            y: 4,
            height: 11,
            max_top: 100,
        };
        assert_eq!(pointer_top(&drag, 4), 0);
        assert_eq!(pointer_top(&drag, 9), 50);
        assert_eq!(pointer_top(&drag, 14), 100);
    }

    #[test]
    fn escape_interrupts_an_active_run() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)),
            Some(Effect::Cancel)
        ));
    }

    #[test]
    fn mouse_drag_selects_and_copies_transcript_text() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript_viewport = TranscriptViewport {
            x: 2,
            y: 3,
            width: 30,
            height: 1,
            lines: vec!["Hames transcript".to_owned()],
        };
        assert!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Down(MouseButton::Left),
                    column: 2,
                    row: 3,
                    modifiers: KeyModifiers::NONE,
                },
            )
            .is_none()
        );
        assert!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Drag(MouseButton::Left),
                    column: 6,
                    row: 3,
                    modifiers: KeyModifiers::NONE,
                },
            )
            .is_none()
        );
        assert!(matches!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Up(MouseButton::Left),
                    column: 6,
                    row: 3,
                    modifiers: KeyModifiers::NONE,
                },
            ),
            Some(Effect::Copy(text)) if text == "Hames"
        ));
    }

    #[test]
    fn effort_options_follow_model_capabilities() {
        let gemma = ProviderModel {
            id: "gemma-4-26b-a4b-it".to_owned(),
            status: "unloaded".to_owned(),
            context_length: None,
            parameter_size: None,
            quantization: None,
            reasoning_supported: Some(false),
            reasoning_efforts: Vec::new(),
        };
        assert!(model_efforts(&gemma).is_empty());

        let gemma = ProviderModel {
            reasoning_supported: Some(true),
            reasoning_efforts: vec!["on".to_owned()],
            ..gemma
        };
        assert_eq!(model_efforts(&gemma), ["on", "off"]);

        let qwen = ProviderModel {
            reasoning_supported: Some(true),
            reasoning_efforts: vec!["low".to_owned(), "medium".to_owned()],
            ..gemma
        };
        assert_eq!(model_efforts(&qwen), ["default", "off", "low", "medium"]);
    }

    fn session() -> Session {
        Session {
            id: "session-1".to_owned(),
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            status: "open".to_owned(),
            title: None,
            working_directory: "/tmp/project".to_owned(),
            agent_id: "default".to_owned(),
            provider: "fake".to_owned(),
            model: "fixture".to_owned(),
            reasoning_effort: "medium".to_owned(),
            context_window_tokens: 32_768,
            context_window_source: "provider".to_owned(),
            parent_session_id: None,
            fork_event_id: None,
            lineage_kind: "root".to_owned(),
            delegation_depth: 0,
            interaction_mode: "auto".to_owned(),
        }
    }
}
