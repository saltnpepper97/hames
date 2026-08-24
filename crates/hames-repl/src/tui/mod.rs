mod app;
mod view;

use std::env;
use std::io::{self, Stdout, Write};
use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use app::{
    App, HitAction, MemoryBrowser, MenuAction, MenuOption, Modal, ScarBrowser, ScarEditField,
    ScarEditor, ScrollDrag, ScrollTarget, Sheet, SheetKind, ThemeKind,
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
use futures_util::future::join_all;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::api::{
    GatewayClient, LiveEnvelope, PROTOCOL_VERSION, PasteSpan, ProviderModel, ScarUpdate, Session,
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
    let configured_theme = paths.configured_theme()?;
    app.theme = ThemeKind::from_config(&configured_theme)
        .with_context(|| format!("unknown theme in ui.toml: {configured_theme}"))?;
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
            _ = tokio::time::sleep(tick_delay) => None
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
                let reopen_sessions = app.reopen_sessions_after_switch;
                app = load_app(&client, session).await?;
                app.theme = theme;
                if reopen_sessions {
                    open_sessions_sheet(&client, &mut app).await?;
                    app.notice = Some("Session removed from resumable history".to_owned());
                }
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
    let exit_cancellation = if let Some(run_id) = app.active_run.clone() {
        match client.cancel(&run_id).await {
            Ok(()) => {
                app.active_run = None;
                app.run_started_at = None;
                Some(Ok(run_id))
            }
            Err(error) => Some(Err(error)),
        }
    } else {
        None
    };
    stream_task.abort();
    let session_id = app.session.id.clone();
    let discard_empty = app.conversation_is_empty() && app.active_run.is_none();
    drop(terminal);
    println!();
    if let Some(result) = exit_cancellation {
        match result {
            Ok(_) => println!("Active turn cancelled"),
            Err(error) => println!("Warning: active turn could not be cancelled: {error:#}"),
        }
    }
    if discard_empty {
        client.close_session(&session_id).await?;
        println!("Empty session discarded · nothing to resume");
    } else {
        println!("Session saved · use /resume {session_id} to continue where you left off");
    }
    Ok(())
}

async fn create_session(
    client: &GatewayClient,
    paths: &LocalPaths,
    current: Option<&Session>,
) -> Result<Session> {
    let cwd = env::current_dir()?.canonicalize()?;
    if let Some(current) = current {
        return client
            .create_session_from(&cwd.to_string_lossy(), &current.id)
            .await;
    }
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
    let created = client
        .create_session(&cwd.to_string_lossy(), agent, &provider, &model, &reasoning)
        .await?;
    Ok(created)
}

async fn replace_session(
    client: &GatewayClient,
    paths: &LocalPaths,
    previous: &Session,
) -> Result<Session> {
    let created = create_session(client, paths, Some(previous)).await?;
    if let Err(error) = client.close_session(&previous.id).await {
        let _ = client.close_session(&created.id).await;
        return Err(error);
    }
    Ok(created)
}

async fn open_sessions_sheet(client: &GatewayClient, app: &mut App) -> Result<()> {
    app.notice = Some("Loading sessions…".to_owned());
    let sessions = client
        .sessions()
        .await?
        .into_iter()
        .filter(|session| session.status == "open")
        .take(40)
        .collect::<Vec<_>>();
    let inspected = join_all(sessions.into_iter().map(|session| async move {
        let history = client.history(&session.id).await;
        (session, history)
    }))
    .await;
    let mut visible = Vec::new();
    for (session, history) in inspected {
        let history = history?;
        let has_conversation = history
            .iter()
            .any(|event| event.event_type == "user.message");
        if has_conversation {
            visible.push(session);
        }
    }
    app.notice = None;
    app.sheet = Some(Sheet {
        kind: SheetKind::Sessions,
        title: "Open sessions".to_owned(),
        options: visible
            .into_iter()
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
        pending_delete: None,
    });
    Ok(())
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
    DeleteSession(String),
    DeleteMemory(String),
    DeleteScar(String),
    UpdateScar(ScarUpdate),
}

fn handle_terminal_event(app: &mut App, event: Event) -> Option<Effect> {
    match event {
        Event::Key(key) if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) => {
            handle_key(app, key)
        }
        Event::Paste(value) => {
            if let Some(Modal::ScarEdit(editor)) = &mut app.modal {
                if let Some(input) = editor.active_text_mut() {
                    input.insert_text(&value);
                }
            } else if app.modal.is_none() {
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
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('d') {
        let Some(sheet) = &mut app.sheet else {
            return None;
        };
        if sheet.kind != SheetKind::Sessions || sheet.options.is_empty() {
            return None;
        }
        let selected = sheet.selected.min(sheet.options.len().saturating_sub(1));
        if sheet.pending_delete == Some(selected) {
            sheet.pending_delete = None;
            return match &sheet.options[selected].action {
                MenuAction::Resume(session_id) => Some(Effect::DeleteSession(session_id.clone())),
                _ => None,
            };
        }
        sheet.pending_delete = Some(selected);
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
            if app.sheet.is_some() {
                app.sheet = None;
                return None;
            }
            if app.active_run.is_some() {
                app.notice = Some("Interrupting current work…".to_owned());
                return Some(Effect::Cancel);
            }
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
                sheet.selected = if sheet.options.is_empty() || sheet.selected > 0 {
                    sheet.selected.saturating_sub(1)
                } else {
                    sheet.options.len() - 1
                };
                sheet.pending_delete = None;
            }
            None
        }
        KeyCode::Down if app.sheet.is_some() => {
            if let Some(sheet) = &mut app.sheet {
                sheet.selected =
                    if sheet.options.is_empty() || sheet.selected + 1 >= sheet.options.len() {
                        0
                    } else {
                        sheet.selected + 1
                    };
                sheet.pending_delete = None;
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
        Modal::Memory(browser) => match key.code {
            KeyCode::Char('d') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                if browser.records.is_empty() {
                    return None;
                }
                let selected = browser
                    .selected
                    .min(browser.records.len().saturating_sub(1));
                if browser.pending_delete == Some(selected) {
                    browser.pending_delete = None;
                    Some(Effect::DeleteMemory(browser.records[selected].id.clone()))
                } else {
                    browser.pending_delete = Some(selected);
                    None
                }
            }
            KeyCode::Up => {
                browser.selected = if browser.records.is_empty() || browser.selected > 0 {
                    browser.selected.saturating_sub(1)
                } else {
                    browser.records.len() - 1
                };
                browser.detail_scroll = 0;
                browser.pending_delete = None;
                None
            }
            KeyCode::Down => {
                browser.selected = if browser.records.is_empty()
                    || browser.selected + 1 >= browser.records.len()
                {
                    0
                } else {
                    browser.selected + 1
                };
                browser.detail_scroll = 0;
                browser.pending_delete = None;
                None
            }
            KeyCode::PageUp => {
                browser.detail_scroll = browser.detail_scroll.saturating_sub(5);
                None
            }
            KeyCode::PageDown => {
                browser.detail_scroll = browser.detail_scroll.saturating_add(5);
                None
            }
            KeyCode::Esc | KeyCode::Char('q') => {
                app.modal = None;
                None
            }
            _ => None,
        },
        Modal::Scars(browser) => match key.code {
            KeyCode::Char('d') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                if browser.records.is_empty() {
                    return None;
                }
                let selected = browser
                    .selected
                    .min(browser.records.len().saturating_sub(1));
                if browser.pending_delete == Some(selected) {
                    browser.pending_delete = None;
                    Some(Effect::DeleteScar(browser.records[selected].id.clone()))
                } else {
                    browser.pending_delete = Some(selected);
                    None
                }
            }
            KeyCode::Char('e') => {
                if let Some(editor) = ScarEditor::new(browser.clone()) {
                    app.modal = Some(Modal::ScarEdit(editor));
                }
                None
            }
            KeyCode::Up => {
                browser.selected = if browser.records.is_empty() || browser.selected > 0 {
                    browser.selected.saturating_sub(1)
                } else {
                    browser.records.len() - 1
                };
                browser.detail_scroll = 0;
                browser.pending_delete = None;
                None
            }
            KeyCode::Down => {
                browser.selected = if browser.records.is_empty()
                    || browser.selected + 1 >= browser.records.len()
                {
                    0
                } else {
                    browser.selected + 1
                };
                browser.detail_scroll = 0;
                browser.pending_delete = None;
                None
            }
            KeyCode::PageUp => {
                browser.detail_scroll = browser.detail_scroll.saturating_sub(5);
                None
            }
            KeyCode::PageDown => {
                browser.detail_scroll = browser.detail_scroll.saturating_add(5);
                None
            }
            KeyCode::Esc | KeyCode::Char('q') => {
                app.modal = None;
                None
            }
            _ => None,
        },
        Modal::ScarEdit(editor) => {
            if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('s') {
                let update = ScarUpdate {
                    title: editor.title.text().trim().to_owned(),
                    severity: editor.severity.clone(),
                    description: editor.description.text().trim().to_owned(),
                    expected_behavior: editor.expected_behavior.text().trim().to_owned(),
                };
                if update.title.is_empty()
                    || update.description.is_empty()
                    || update.expected_behavior.is_empty()
                {
                    app.notice = Some(
                        "Scar title, description, and expected behavior cannot be empty".to_owned(),
                    );
                    None
                } else {
                    Some(Effect::UpdateScar(update))
                }
            } else {
                match key.code {
                    KeyCode::Esc => {
                        app.modal = Some(Modal::Scars(editor.browser.clone()));
                    }
                    KeyCode::Tab if key.modifiers.contains(KeyModifiers::SHIFT) => {
                        editor.field = editor.field.previous();
                    }
                    KeyCode::Tab => editor.field = editor.field.next(),
                    KeyCode::BackTab => editor.field = editor.field.previous(),
                    KeyCode::Left | KeyCode::Right | KeyCode::Char(' ')
                        if editor.field == ScarEditField::Severity =>
                    {
                        editor.cycle_severity(key.code == KeyCode::Left);
                    }
                    KeyCode::Enter if editor.field == ScarEditField::Title => {
                        editor.field = editor.field.next();
                    }
                    KeyCode::Enter => {
                        if let Some(input) = editor.active_text_mut() {
                            input.insert_text("\n");
                        }
                    }
                    KeyCode::Backspace => {
                        if let Some(input) = editor.active_text_mut() {
                            input.backspace();
                        }
                    }
                    KeyCode::Delete => {
                        if let Some(input) = editor.active_text_mut() {
                            input.delete();
                        }
                    }
                    KeyCode::Left => {
                        if let Some(input) = editor.active_text_mut() {
                            input.move_left();
                        }
                    }
                    KeyCode::Right => {
                        if let Some(input) = editor.active_text_mut() {
                            input.move_right();
                        }
                    }
                    KeyCode::Home => {
                        if let Some(input) = editor.active_text_mut() {
                            input.move_home();
                        }
                    }
                    KeyCode::End => {
                        if let Some(input) = editor.active_text_mut() {
                            input.move_end();
                        }
                    }
                    KeyCode::Char(value)
                        if !key
                            .modifiers
                            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
                    {
                        if let Some(input) = editor.active_text_mut() {
                            input.insert_text(&value.to_string());
                        }
                    }
                    _ => {}
                }
                None
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
            if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Memory(browser)) = &mut app.modal
            {
                browser.detail_scroll = browser.detail_scroll.saturating_sub(3);
            } else if mouse_over_composer(app, mouse.column, mouse.row) {
                scroll_composer(app, -3);
            } else {
                app.scroll = app.scroll.saturating_add(3);
            }
            None
        }
        MouseEventKind::ScrollDown => {
            app.clear_transcript_selection();
            if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Memory(browser)) = &mut app.modal
            {
                browser.detail_scroll = browser.detail_scroll.saturating_add(3);
            } else if mouse_over_composer(app, mouse.column, mouse.row) {
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
                    let max_top = content_len.saturating_sub(viewport_len);
                    let anchor_top = match target {
                        ScrollTarget::Transcript => max_top.saturating_sub(app.scroll.min(max_top)),
                        ScrollTarget::Composer => {
                            app.composer_scroll.unwrap_or(max_top).min(max_top)
                        }
                    };
                    let drag = ScrollDrag {
                        target,
                        y: region.y,
                        height: region.height,
                        max_top,
                        anchor_y: mouse.row,
                        anchor_top,
                    };
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
                Some(HitAction::SelectMemory(index)) => {
                    app.clear_transcript_selection();
                    app.clear_modal_selection();
                    if let Some(Modal::Memory(browser)) = &mut app.modal
                        && index < browser.records.len()
                    {
                        if browser.selected != index {
                            browser.pending_delete = None;
                        }
                        browser.selected = index;
                        browser.detail_scroll = 0;
                    }
                    if let Some(point) = app.modal_viewport.point(mouse.column, mouse.row) {
                        app.begin_modal_selection(point);
                    }
                    None
                }
                Some(HitAction::SelectScar(index)) => {
                    app.clear_transcript_selection();
                    app.clear_modal_selection();
                    if let Some(Modal::Scars(browser)) = &mut app.modal
                        && index < browser.records.len()
                    {
                        if browser.selected != index {
                            browser.pending_delete = None;
                        }
                        browser.selected = index;
                        browser.detail_scroll = 0;
                    }
                    if let Some(point) = app.modal_viewport.point(mouse.column, mouse.row) {
                        app.begin_modal_selection(point);
                    }
                    None
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
                    if app.modal.is_some() {
                        app.clear_transcript_selection();
                        app.clear_modal_selection();
                        if let Some(point) = app.modal_viewport.point(mouse.column, mouse.row) {
                            app.begin_modal_selection(point);
                        }
                    } else if let Some(point) =
                        app.transcript_viewport.point(mouse.column, mouse.row)
                    {
                        app.clear_modal_selection();
                        app.begin_transcript_selection(point);
                    } else {
                        app.clear_transcript_selection();
                        app.clear_modal_selection();
                    }
                    None
                }
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            if let Some(drag) = app.scroll_drag.clone() {
                scroll_to_pointer(app, &drag, mouse.row);
            } else if app.selecting_modal {
                if let Some(point) = app.modal_viewport.point(mouse.column, mouse.row) {
                    app.update_modal_selection(point);
                }
            } else if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                app.update_transcript_selection(point);
            }
            None
        }
        MouseEventKind::Up(MouseButton::Left) => {
            app.scroll_drag = None;
            if app.selecting_modal {
                if let Some(point) = app.modal_viewport.point(mouse.column, mouse.row) {
                    app.update_modal_selection(point);
                }
                if let Some(text) = app.finish_modal_selection() {
                    return Some(Effect::Copy(text));
                }
            }
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
    let row = row.clamp(drag.y, drag.y.saturating_add(drag.height.saturating_sub(1)));
    let distance = usize::from(row.abs_diff(drag.anchor_y));
    let delta = drag.max_top.saturating_mul(distance) / track;
    if row >= drag.anchor_y {
        drag.anchor_top.saturating_add(delta).min(drag.max_top)
    } else {
        drag.anchor_top.saturating_sub(delta)
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
        "/new" => Some(MenuAction::NewSession),
        "/clear" => Some(MenuAction::ClearSession),
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
        "/resume" => parts
            .next()
            .map(|id| MenuAction::Resume(id.to_owned()))
            .or(Some(MenuAction::OpenSessions)),
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
        Effect::DeleteSession(session_id) => {
            if session_id == app.session.id {
                app.notice = Some("Removing this session and starting fresh…".to_owned());
                let previous = app.session.clone();
                let replacement = replace_session(client, paths, &previous).await?;
                app.reopen_sessions_after_switch = true;
                return Ok(Some(replacement));
            }
            app.notice = Some("Removing session from history…".to_owned());
            client.close_session(&session_id).await?;
            if let Some(sheet) = &mut app.sheet
                && sheet.kind == SheetKind::Sessions
            {
                sheet.options.retain(
                    |option| !matches!(&option.action, MenuAction::Resume(id) if id == &session_id),
                );
                sheet.selected = sheet.selected.min(sheet.options.len().saturating_sub(1));
                sheet.pending_delete = None;
            }
            app.notice = Some("Session removed from resumable history".to_owned());
        }
        Effect::DeleteMemory(memory_id) => {
            client.delete_memory(&app.session.id, &memory_id).await?;
            if let Some(Modal::Memory(browser)) = &mut app.modal {
                browser.records.retain(|memory| memory.id != memory_id);
                browser.selected = browser
                    .selected
                    .min(browser.records.len().saturating_sub(1));
                browser.detail_scroll = 0;
                browser.pending_delete = None;
            }
            app.notice = Some(format!("Memory {} deleted", short_id(&memory_id)));
        }
        Effect::DeleteScar(scar_id) => {
            client.delete_scar(&app.session.id, &scar_id).await?;
            if let Some(Modal::Scars(browser)) = &mut app.modal {
                browser.records.retain(|scar| scar.id != scar_id);
                browser.selected = browser
                    .selected
                    .min(browser.records.len().saturating_sub(1));
                browser.detail_scroll = 0;
                browser.pending_delete = None;
            }
            app.notice = Some(format!("Scar {} deleted", short_id(&scar_id)));
        }
        Effect::UpdateScar(update) => {
            let Some(Modal::ScarEdit(editor)) = app.modal.clone() else {
                return Ok(None);
            };
            let updated = client
                .update_scar(&app.session.id, &editor.scar_id, &update)
                .await?;
            let mut browser = editor.browser;
            if let Some(record) = browser
                .records
                .iter_mut()
                .find(|scar| scar.id == updated.id)
            {
                *record = updated;
            }
            app.modal = Some(Modal::Scars(browser));
            app.notice = Some("Scar changes saved".to_owned());
        }
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
            let previous = app.session.clone();
            let created = if app.conversation_is_empty() && app.active_run.is_none() {
                replace_session(client, paths, &previous).await?
            } else {
                create_session(client, paths, Some(&previous)).await?
            };
            return Ok(Some(created));
        }
        MenuAction::ClearSession => {
            app.notice = Some("Clearing this conversation…".to_owned());
            let previous = app.session.clone();
            return Ok(Some(replace_session(client, paths, &previous).await?));
        }
        MenuAction::OpenSessions => {
            open_sessions_sheet(client, app).await?;
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
                    pending_delete: None,
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
                pending_delete: None,
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
                pending_delete: None,
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
                pending_delete: None,
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
            let mut memories = client.memories(&app.session.id, "active", "").await?;
            memories.retain(|memory| memory.status == "active");
            app.modal = Some(Modal::Memory(MemoryBrowser {
                records: memories,
                selected: 0,
                detail_scroll: 0,
                pending_delete: None,
            }));
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
            app.modal = Some(Modal::Scars(ScarBrowser {
                records: scars,
                selected: 0,
                detail_scroll: 0,
                pending_delete: None,
            }));
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
        MenuAction::Resume(id) => {
            let selected = client.session(&id).await?;
            if id != app.session.id && app.conversation_is_empty() && app.active_run.is_none() {
                client.close_session(&app.session.id).await?;
            }
            return Ok(Some(selected));
        }
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
            paths.write_theme(theme.config_value())?;
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
    use crate::api::{MemoryRecord, ProviderModel, Scar, Session};
    use crate::tui::app::{
        App, HitAction, HitRegion, MemoryBrowser, MenuAction, MenuOption, Modal, ScarBrowser,
        ScarEditField, ScrollDrag, ScrollTarget, Sheet, SheetKind, ThemeKind, TranscriptViewport,
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
        assert!(matches!(
            parse_command("/clear"),
            Some(MenuAction::ClearSession)
        ));
        assert!(matches!(
            parse_command("/resume"),
            Some(MenuAction::OpenSessions)
        ));
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
    fn new_session_command_remains_a_client_action_during_active_work() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.composer.insert_text("/new");

        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::Menu(MenuAction::NewSession))
        ));
    }

    #[test]
    fn sheet_navigation_wraps_at_both_ends() {
        let mut app = App::new(session(), Vec::new(), true);
        app.open_modes();
        app.sheet.as_mut().unwrap().selected = 0;
        assert_eq!(app.sheet.as_ref().unwrap().selected, 0);

        handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        assert_eq!(app.sheet.as_ref().unwrap().selected, 2);
        handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert_eq!(app.sheet.as_ref().unwrap().selected, 0);
    }

    #[test]
    fn session_deletion_requires_two_ctrl_d_presses_and_navigation_cancels_it() {
        let mut app = App::new(session(), Vec::new(), true);
        app.sheet = Some(Sheet {
            kind: SheetKind::Sessions,
            title: "Open sessions".to_owned(),
            options: vec![
                MenuOption {
                    label: "First".to_owned(),
                    detail: "fixture".to_owned(),
                    action: MenuAction::Resume("session-first".to_owned()),
                },
                MenuOption {
                    label: "Second".to_owned(),
                    detail: "fixture".to_owned(),
                    action: MenuAction::Resume("session-second".to_owned()),
                },
            ],
            selected: 0,
            pending_delete: None,
        });
        let ctrl_d = KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL);

        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert_eq!(app.sheet.as_ref().unwrap().pending_delete, Some(0));

        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)).is_none());
        let sheet = app.sheet.as_ref().unwrap();
        assert_eq!(sheet.selected, 1);
        assert_eq!(sheet.pending_delete, None);

        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert!(matches!(
            handle_key(&mut app, ctrl_d),
            Some(Effect::DeleteSession(session_id)) if session_id == "session-second"
        ));
    }

    #[test]
    fn scrollbar_pointer_maps_the_full_track() {
        let drag = ScrollDrag {
            target: ScrollTarget::Transcript,
            y: 4,
            height: 11,
            max_top: 100,
            anchor_y: 9,
            anchor_top: 50,
        };
        assert_eq!(pointer_top(&drag, 4), 0);
        assert_eq!(pointer_top(&drag, 9), 50);
        assert_eq!(pointer_top(&drag, 14), 100);
    }

    #[test]
    fn grabbing_a_scrollbar_does_not_change_its_position() {
        let mut app = App::new(session(), Vec::new(), true);
        app.scroll = 35;
        app.hits.push(HitRegion {
            x: 90,
            y: 4,
            width: 2,
            height: 11,
            action: HitAction::Scrollbar {
                target: ScrollTarget::Transcript,
                content_len: 120,
                viewport_len: 20,
            },
        });

        assert!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Down(MouseButton::Left),
                    column: 90,
                    row: 9,
                    modifiers: KeyModifiers::NONE,
                },
            )
            .is_none()
        );
        assert_eq!(app.scroll, 35);

        handle_mouse(
            &mut app,
            MouseEvent {
                kind: MouseEventKind::Drag(MouseButton::Left),
                column: 90,
                row: 10,
                modifiers: KeyModifiers::NONE,
            },
        );
        assert_eq!(app.scroll, 25);
    }

    #[test]
    fn memory_deletion_requires_confirmation_and_navigation_disarms_it() {
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Memory(MemoryBrowser {
            records: vec![
                memory_record("memory-first"),
                memory_record("memory-second"),
            ],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        }));
        let ctrl_d = KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL);

        assert!(handle_key(&mut app, ctrl_d).is_none());
        let Modal::Memory(browser) = app.modal.as_ref().unwrap() else {
            panic!("memory browser should remain open");
        };
        assert_eq!(browser.pending_delete, Some(0));

        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)).is_none());
        let Modal::Memory(browser) = app.modal.as_ref().unwrap() else {
            panic!("memory browser should remain open");
        };
        assert_eq!(browser.selected, 1);
        assert_eq!(browser.pending_delete, None);

        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert!(matches!(
            handle_key(&mut app, ctrl_d),
            Some(Effect::DeleteMemory(memory_id)) if memory_id == "memory-second"
        ));
    }

    #[test]
    fn scar_browser_supports_confirmed_deletion_and_structured_editing() {
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Scars(ScarBrowser {
            records: vec![scar_record("scar-first")],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        }));
        let ctrl_d = KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL);
        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert!(matches!(
            &app.modal,
            Some(Modal::Scars(browser)) if browser.pending_delete == Some(0)
        ));
        assert!(matches!(
            handle_key(&mut app, ctrl_d),
            Some(Effect::DeleteScar(scar_id)) if scar_id == "scar-first"
        ));

        app.modal = Some(Modal::Scars(ScarBrowser {
            records: vec![scar_record("scar-first")],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        }));
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('e'), KeyModifiers::NONE)
            )
            .is_none()
        );
        let Some(Modal::ScarEdit(editor)) = &app.modal else {
            panic!("E should open the structured Scar editor");
        };
        assert_eq!(editor.field, ScarEditField::Title);
        assert_eq!(editor.title.text(), "Retry loop");

        handle_key(&mut app, KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
        handle_key(&mut app, KeyEvent::new(KeyCode::Right, KeyModifiers::NONE));
        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('s'), KeyModifiers::CONTROL)
            ),
            Some(Effect::UpdateScar(update))
                if update.severity == "low"
                    && update.description == "Retried without inspecting the failure"
        ));
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
    fn modal_text_drag_copies_without_dismissing_the_modal() {
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Info {
            title: "Fixture".to_owned(),
            lines: vec!["Modal content".to_owned()],
        });
        app.modal_viewport = TranscriptViewport {
            x: 10,
            y: 5,
            width: 30,
            height: 1,
            lines: vec!["Modal content".to_owned()],
        };
        assert!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Down(MouseButton::Left),
                    column: 10,
                    row: 5,
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
                    column: 14,
                    row: 5,
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
                    column: 14,
                    row: 5,
                    modifiers: KeyModifiers::NONE,
                },
            ),
            Some(Effect::Copy(text)) if text == "Modal"
        ));
        assert!(app.modal.is_some());
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

    fn memory_record(id: &str) -> MemoryRecord {
        MemoryRecord {
            id: id.to_owned(),
            layer: "relationship".to_owned(),
            status: "active".to_owned(),
            visibility: "global".to_owned(),
            subject: "user:local".to_owned(),
            predicate: "prefers_ui".to_owned(),
            value: serde_json::json!("Subdued and polished"),
            summary: "Keep the interface calm".to_owned(),
            confidence: 0.95,
            importance: 0.9,
            owner_agent_id: None,
            workspace_path: None,
            lineage_root_session_id: None,
            source_session_id: "session-1".to_owned(),
            source_run_id: Some("run-1".to_owned()),
            origin_kind: "explicit".to_owned(),
            valid_from: None,
            valid_until: None,
            superseded_by_id: None,
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            updated_at: "2026-08-24T00:00:00Z".to_owned(),
            anchors: Vec::new(),
            provenance_event_ids: Vec::new(),
        }
    }

    fn scar_record(id: &str) -> Scar {
        Scar {
            id: id.to_owned(),
            title: "Retry loop".to_owned(),
            scope: "workspace".to_owned(),
            status: "open".to_owned(),
            severity: "high".to_owned(),
            failure_signature: "tool:shell:exit-42".to_owned(),
            description: "Retried without inspecting the failure".to_owned(),
            expected_behavior: "Inspect the first failure before retrying".to_owned(),
            detection: "repeated_failure".to_owned(),
            repair_layer: Some("skill".to_owned()),
            repair_reference: Some("repair-123456789".to_owned()),
            evidence_event_ids: vec!["event-1".to_owned()],
            last_triggered_at: "2026-08-24T00:00:00Z".to_owned(),
            successful_guard_count: 2,
            regression_count: 1,
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            updated_at: "2026-08-24T00:00:00Z".to_owned(),
        }
    }
}
