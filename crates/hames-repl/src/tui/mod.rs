mod app;
mod view;

use std::env;
use std::io::{self, Stdout, Write};
use std::path::Path;
use std::process::Command;
use std::sync::Once;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use app::{
    AgentEditField, AgentEditor, AgentEditorPage, App, ConnectionState, GoalModal, HitAction,
    InlineEditor, InlineEditorKind, MemoryBrowser, MenuAction, MenuOption, Modal,
    QuestionInputKind, ScarBrowser, ScarEditField, ScarEditor, ScrollDrag, ScrollTarget, Sheet,
    SheetKind, ThemeKind, UsageModal,
};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use crossterm::cursor::Show;
use crossterm::event::{
    DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste,
    EnableFocusChange, EnableMouseCapture, Event, EventStream, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers, KeyboardEnhancementFlags, MouseButton, MouseEvent, MouseEventKind,
    PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
};
use crossterm::execute;
use crossterm::terminal::{
    BeginSynchronizedUpdate, EndSynchronizedUpdate, EnterAlternateScreen, LeaveAlternateScreen,
    SetTitle, disable_raw_mode, enable_raw_mode,
};
use futures_util::StreamExt;
use futures_util::future::join_all;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::api::{
    AgentAccessUpdate, GatewayClient, HEAL_SCARS_PROMPT, LiveEnvelope, PROTOCOL_VERSION, PasteSpan,
    ProviderModel, ScarUpdate, Session, SseDecoder, event_reconnect_delay,
};
use crate::local::{LocalPaths, write_private_export};
use crate::repl::ensure_gateway;

static TERMINAL_ACTIVE: AtomicBool = AtomicBool::new(false);
static TERMINAL_PANIC_HOOK: Once = Once::new();

pub async fn run() -> Result<()> {
    crate::style::init();
    let paths = LocalPaths::resolve()?;
    crate::local::ensure_search_setup(&paths, false)?;
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
    let session = create_session(&client, &paths, None).await?;
    match ensure_workspace_trust(&client, &session).await {
        Ok(true) => {}
        Ok(false) => {
            client.close_session(&session.id).await?;
            return Ok(());
        }
        Err(error) => {
            let _ = client.close_session(&session.id).await;
            return Err(error);
        }
    }
    let mut app = load_app(&client, session).await?;
    app.connection_state = ConnectionState::Connecting;
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
    let queue_state = client.resume_queue(&app.session.id).await?;
    app.set_queue(queue_state);
    let mut terminal = TerminalGuard::enter()?;
    let mut shutdown_signals = ShutdownSignals::new()?;
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
        let mut suppress_redraw = false;
        let effect = tokio::select! {
            event = input.next() => {
                match event {
                    Some(Ok(event)) => {
                        let pointer_moved = matches!(&event, Event::Mouse(MouseEvent { kind: MouseEventKind::Moved, .. }));
                        app.error_notice = None;
                        app.dismiss_notice();
                        let effect = handle_terminal_event(&mut app, event);
                        if pointer_moved {
                            suppress_redraw = true;
                        }
                        effect
                    }
                    Some(Err(error)) => {
                        app.modal = Some(Modal::Error(format!("terminal input failed: {error}")));
                        None
                    }
                    None => Some(Effect::Quit),
                }
            }
            _ = shutdown_signals.recv() => Some(Effect::Quit),
            message = stream_rx.recv() => {
                if let Some(message) = message
                    && message.session_id == app.session.id
                {
                    match message.payload {
                        StreamPayload::Envelope(envelope) => {
                            let changed_agent = envelope.event.as_ref().and_then(|event| {
                                (event.event_type == "session.agent.changed")
                                    .then(|| {
                                        event
                                            .payload
                                            .get("agent_id")
                                            .and_then(serde_json::Value::as_str)
                                            .map(str::to_owned)
                                    })
                                    .flatten()
                            });
                            if ingest_envelope(&mut app, *envelope) {
                                let (workspace_name, git_ref) =
                                    workspace_identity(&app.session.working_directory);
                                app.workspace_name = workspace_name;
                                app.git_ref = git_ref;
                            }
                            if let Some(agent_id) = changed_agent
                                && let Ok(agent) = client.agent(&agent_id).await
                            {
                                app.agent_name = agent.agent.name;
                            }
                            if app.diff_details {
                                let failures = load_pending_diff_details(&client, &mut app).await;
                                if failures > 0 {
                                    app.notice = Some(format!(
                                        "Expanded edit details · {failures} unavailable"
                                    ));
                                }
                            }
                        }
                        StreamPayload::Warning(message) => app.notice = Some(message),
                        StreamPayload::State(state) => app.connection_state = state,
                    }
                }
                None
            }
            _ = tokio::time::sleep(tick_delay) => None
        };
        dirty = !suppress_redraw;
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
                app.connection_state = ConnectionState::Connecting;
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
                let queue_state = client.resume_queue(&app.session.id).await?;
                app.set_queue(queue_state);
            }
            Ok(None) => {}
            Err(error) => {
                app.error_notice = Some(action_error_message(&error));
                app.notice = None;
            }
        }
        if app.should_quit {
            break;
        }
    }
    stream_task.abort();
    drop(input);
    drop(terminal);

    let goal_continues = app.goal_keeps_session_alive();
    let queue_pause = if goal_continues {
        None
    } else {
        Some(client.pause_queue(&app.session.id).await)
    };
    let exit_cancellation = if app.active_run_is_goal_step() {
        None
    } else if let Some(run_id) = app.active_run.clone() {
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
    let session_id = app.session.id.clone();
    let discard_empty =
        app.conversation_is_empty() && app.active_run.is_none() && !app.goal_keeps_session_alive();
    let exit_notice = session_exit_notice(&session_id, discard_empty);
    let has_exit_notice = queue_pause.as_ref().is_some_and(Result::is_err)
        || exit_cancellation.is_some()
        || exit_notice.is_some();
    if has_exit_notice {
        println!();
    }
    if let Some(Err(error)) = queue_pause {
        println!("Warning: queued work could not be paused: {error:#}");
    }
    if let Some(result) = exit_cancellation {
        match result {
            Ok(_) => println!("Active turn cancelled"),
            Err(error) => println!("Warning: active turn could not be cancelled: {error:#}"),
        }
    }
    if discard_empty {
        client.close_session(&session_id).await?;
    } else if let Some(notice) = exit_notice {
        println!("{notice}");
    }
    Ok(())
}

async fn ensure_workspace_trust(client: &GatewayClient, session: &Session) -> Result<bool> {
    if client.trust_status(&session.id).await?.trusted {
        return Ok(true);
    }
    let trust_workspace = crate::trust::prompt_workspace_trust(&session.working_directory)?;
    if !trust_workspace {
        println!();
        return Ok(false);
    }
    let trust = client.trust_session(&session.id).await?;
    println!(
        "{}",
        crate::style::success(&format!("Trusted {}", trust.path))
    );
    println!();
    Ok(true)
}

fn session_exit_notice(session_id: &str, discard_empty: bool) -> Option<String> {
    (!discard_empty).then(|| format!("Resume session with\n  /resume {session_id}"))
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
    let provider = paths.configured_provider()?;
    let model = paths.configured_model(&provider)?;
    let created = client
        .create_session(&cwd.to_string_lossy(), "", &provider, &model, "")
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
        .sessions_for_directory(&app.session.working_directory)
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

fn open_queue_sheet(app: &mut App) {
    let total = app.queued_messages.len();
    app.sheet = Some(Sheet {
        kind: SheetKind::Queue,
        title: "Queued turns".to_owned(),
        options: app
            .queued_messages
            .iter()
            .enumerate()
            .map(|(index, item)| MenuOption {
                label: item
                    .content
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" "),
                detail: format!("pending {}/{}", index + 1, total),
                action: MenuAction::EditQueued(item.id.clone()),
            })
            .collect(),
        selected: 0,
        pending_delete: None,
    });
}

async fn refresh_agents_sheet(client: &GatewayClient, app: &mut App) -> Result<()> {
    let selected_id = app.sheet.as_ref().and_then(|sheet| {
        sheet
            .options
            .get(sheet.selected)
            .and_then(|option| match &option.action {
                MenuAction::SetAgent(id) => Some(id.clone()),
                _ => None,
            })
    });
    let agents = client.agents().await?;
    let options = agents
        .into_iter()
        .map(|agent| MenuOption {
            detail: if agent.id == app.session.agent_id {
                format!("{} · current · {}", agent.authority, agent.id)
            } else {
                format!("{} · {}", agent.authority, agent.id)
            },
            label: agent.name,
            action: MenuAction::SetAgent(agent.id),
        })
        .collect::<Vec<_>>();
    let selected = selected_id
        .and_then(|id| {
            options.iter().position(
                |option| matches!(&option.action, MenuAction::SetAgent(value) if value == &id),
            )
        })
        .unwrap_or(0);
    app.sheet = Some(Sheet {
        kind: SheetKind::Agents,
        title: "Agents".to_owned(),
        options,
        selected,
        pending_delete: None,
    });
    Ok(())
}

async fn load_app(client: &GatewayClient, session: Session) -> Result<App> {
    let agent_id = session.agent_id.clone();
    let (events, trust, queue, goals, plan, tasks, skills, terminals) = tokio::try_join!(
        client.history(&session.id),
        client.trust_status(&session.id),
        client.queue_state(&session.id),
        client.goals(&session.id),
        client.current_plan(&session.id),
        client.tasks(&session.id),
        client.skills(&session.id, ""),
        client.background_terminals(&session.id)
    )?;
    let agent = client.agent(&agent_id).await.ok();
    let mut app = App::new(session, events, trust.trusted);
    let (workspace_name, git_ref) = workspace_identity(&app.session.working_directory);
    app.workspace_name = workspace_name;
    app.git_ref = git_ref;
    app.set_queue(queue);
    app.set_background_terminals(terminals);
    app.goal = goals.last().cloned();
    app.set_plan(plan);
    app.set_tasks(tasks);
    app.skill_commands = skills
        .into_iter()
        .filter(|skill| matches!(skill.invocation.as_str(), "user" | "both"))
        .collect();
    if let Some(agent) = agent {
        app.agent_name = agent.agent.name;
    }
    Ok(app)
}

async fn refresh_skill_commands(client: &GatewayClient, app: &mut App) -> Result<()> {
    app.skill_commands = client
        .skills(&app.session.id, "")
        .await?
        .into_iter()
        .filter(|skill| matches!(skill.invocation.as_str(), "user" | "both"))
        .collect();
    Ok(())
}

fn workspace_identity(working_directory: &str) -> (String, Option<String>) {
    let normalized = Path::new(working_directory)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(working_directory).to_path_buf());
    let directory = normalized.to_string_lossy().into_owned();
    let git = |arguments: &[&str]| -> Option<String> {
        let output = Command::new("git")
            .arg("-C")
            .arg(working_directory)
            .args(arguments)
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        let value = String::from_utf8(output.stdout).ok()?.trim().to_owned();
        (!value.is_empty()).then_some(value)
    };
    if git(&["rev-parse", "--is-inside-work-tree"]).as_deref() != Some("true") {
        return (directory, None);
    }
    let reference = git(&["branch", "--show-current"])
        .or_else(|| git(&["rev-parse", "--short", "HEAD"]).map(|value| format!("@{value}")));
    (directory, reference)
}

#[derive(Debug)]
enum Effect {
    Quit,
    ResolveApproval(usize),
    ResolveQuestion {
        selected_option: Option<String>,
        note: String,
        custom_answer: String,
    },
    Send(String, Vec<PasteSpan>),
    SendPlanNote(String, Vec<PasteSpan>),
    ExecutePlanWithNote(String),
    SendNow(String, Vec<PasteSpan>),
    SendQueuedNow(String),
    TakeQueued(String),
    TakeLatestQueued,
    Cancel,
    PauseGoal,
    Copy(String),
    OpenCommands,
    Menu(MenuAction),
    DeleteSession(String),
    DeleteQueued(String),
    DeleteMemory(String),
    DeleteScar(String),
    UpdateScar(ScarUpdate),
    EditAgent(String),
    CreateAgent(String),
    UpdateAgent {
        agent_id: String,
        name: String,
        instructions: String,
        tools: AgentAccessUpdate,
        skills: AgentAccessUpdate,
    },
    DeleteAgent(String),
}

fn handle_terminal_event(app: &mut App, event: Event) -> Option<Effect> {
    match event {
        Event::Key(key) if key.kind == KeyEventKind::Press => handle_key(app, key),
        Event::Key(key) if key.kind == KeyEventKind::Repeat && repeat_safe_key(app, key) => {
            handle_key(app, key)
        }
        Event::Paste(value) => {
            if let Some(question) = &mut app.question
                && question.input_kind.is_some()
            {
                question
                    .response_input
                    .insert_text(&value.replace(['\r', '\n'], " "));
            } else if let Some(editor) = &mut app.inline_editor {
                if editor.kind == InlineEditorKind::PlanExecutionNote {
                    editor.input.insert_text(&value.replace(['\r', '\n'], " "));
                } else {
                    editor.input.insert_paste(value);
                }
            } else if let Some(Modal::ScarEdit(editor)) = &mut app.modal {
                if let Some(input) = editor.active_text_mut() {
                    input.insert_text(&value);
                }
            } else if let Some(Modal::AgentEdit(editor)) = &mut app.modal {
                if editor.page == AgentEditorPage::Identity {
                    editor.active_text_mut().insert_text(&value);
                    if editor.field == AgentEditField::Name {
                        editor.sync_slug();
                    } else if editor.field == AgentEditField::Slug {
                        editor.slug_manual = true;
                    }
                }
            } else if app.modal.is_none() {
                app.composer.insert_paste(value);
                app.update_slash_sheet();
            }
            None
        }
        Event::Mouse(mouse) => handle_mouse(app, mouse),
        Event::FocusLost => None,
        Event::Resize(_, _) => None,
        _ => None,
    }
}

fn repeat_safe_key(app: &App, key: KeyEvent) -> bool {
    let navigation = matches!(
        key.code,
        KeyCode::Left
            | KeyCode::Right
            | KeyCode::Up
            | KeyCode::Down
            | KeyCode::Home
            | KeyCode::End
            | KeyCode::PageUp
            | KeyCode::PageDown
    );
    if navigation {
        return true;
    }
    let text_input = match &app.modal {
        Some(Modal::ScarEdit(_) | Modal::AgentEdit(_)) => true,
        Some(_) => false,
        None if app.question.is_some() => app
            .question
            .as_ref()
            .is_some_and(|question| question.input_kind.is_some()),
        None if app.inline_editor.is_some() => true,
        None => app.sheet.is_none() && (app.focused_thought.is_none() || !app.composer.is_empty()),
    };
    text_input
        && (matches!(key.code, KeyCode::Backspace | KeyCode::Delete)
            || matches!(key.code, KeyCode::Char(_))
                && !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER))
}

fn handle_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    app.clear_composer_selection();
    if key.code != KeyCode::Esc {
        app.clear_escape_confirmation();
    }
    if app.modal.is_some() {
        app.clear_escape_confirmation();
        return handle_modal_key(app, key);
    }
    if app.question.is_some() {
        return handle_question_key(app, key);
    }
    if app.inline_editor.is_some() {
        app.clear_escape_confirmation();
        return handle_inline_editor_key(app, key);
    }
    if key.code == KeyCode::BackTab
        || (key.code == KeyCode::Tab && key.modifiers.contains(KeyModifiers::SHIFT))
    {
        return Some(Effect::Menu(MenuAction::SetMode(
            next_mode(&app.session.interaction_mode).to_owned(),
        )));
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('k') {
        return Some(Effect::OpenCommands);
    }
    if key.modifiers.contains(KeyModifiers::CONTROL)
        && key.code == KeyCode::Char('e')
        && let Some(sheet) = &app.sheet
        && sheet.kind == SheetKind::Agents
        && let Some(MenuAction::SetAgent(agent_id)) = sheet
            .options
            .get(sheet.selected)
            .map(|option| &option.action)
    {
        return Some(Effect::EditAgent(agent_id.clone()));
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
        if !matches!(
            sheet.kind,
            SheetKind::Sessions | SheetKind::Agents | SheetKind::Queue
        ) || sheet.options.is_empty()
        {
            return None;
        }
        let selected = sheet.selected.min(sheet.options.len().saturating_sub(1));
        if matches!(&sheet.options[selected].action, MenuAction::SetAgent(id) if id == "default") {
            return None;
        }
        if sheet.pending_delete == Some(selected) {
            sheet.pending_delete = None;
            return match &sheet.options[selected].action {
                MenuAction::Resume(session_id) => Some(Effect::DeleteSession(session_id.clone())),
                MenuAction::SetAgent(agent_id) => Some(Effect::DeleteAgent(agent_id.clone())),
                MenuAction::EditQueued(queue_id) => Some(Effect::DeleteQueued(queue_id.clone())),
                _ => None,
            };
        }
        sheet.pending_delete = Some(selected);
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL)
        && key.code == KeyCode::Char('n')
        && app
            .sheet
            .as_ref()
            .is_some_and(|sheet| sheet.kind == SheetKind::Agents)
    {
        return Some(Effect::Menu(MenuAction::CreateAgent));
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        if app.active_run.is_some() {
            if app.active_run_is_goal_step() {
                app.notice = Some("Pausing autonomous goal…".to_owned());
                return Some(Effect::PauseGoal);
            }
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
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Enter {
        return send_now(app);
    }
    if key.modifiers.contains(KeyModifiers::ALT)
        && app.sheet.is_none()
        && matches!(key.code, KeyCode::Up | KeyCode::Down)
    {
        if !app.queued_messages.is_empty() {
            app.move_queue_selection(if key.code == KeyCode::Up { -1 } else { 1 });
            app.notice = Some("Queued message selected · Ctrl+Enter sends now".to_owned());
        }
        return None;
    }
    match key.code {
        KeyCode::Esc => {
            if app.sheet.is_some() {
                app.clear_escape_confirmation();
                app.sheet = None;
                return None;
            }
            if app.active_run.is_some() {
                if app.active_run_is_goal_step() {
                    if !app.confirm_escape_for_active_run("pause the autonomous goal") {
                        return None;
                    }
                    app.notice = Some("Pausing autonomous goal…".to_owned());
                    return Some(Effect::PauseGoal);
                }
                if !app.confirm_escape_for_active_run("interrupt current work") {
                    return None;
                }
                app.notice = Some("Interrupting current work…".to_owned());
                return Some(Effect::Cancel);
            }
            app.clear_escape_confirmation();
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
        KeyCode::Enter
            if app
                .sheet
                .as_ref()
                .is_some_and(|sheet| sheet.kind == SheetKind::Tasks) =>
        {
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
        KeyCode::Up if app.composer.is_empty() || app.history_index.is_some() => {
            if app.history_index.is_none() && !app.queued_messages.is_empty() {
                Some(Effect::TakeLatestQueued)
            } else {
                app.history_previous();
                None
            }
        }
        KeyCode::Down if app.history_index.is_some() => {
            app.history_next();
            None
        }
        KeyCode::Enter if app.composer.is_empty() && app.plan_ready() => {
            app.focused_thought = None;
            app.open_plan_review();
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

fn handle_question_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    if key.code == KeyCode::BackTab
        || (key.code == KeyCode::Tab && key.modifiers.contains(KeyModifiers::SHIFT))
    {
        return Some(Effect::Menu(MenuAction::SetMode(
            next_mode(&app.session.interaction_mode).to_owned(),
        )));
    }
    let question = app.question.as_mut()?;
    if key.code == KeyCode::Esc {
        if question.input_kind == Some(QuestionInputKind::Note) {
            question.input_kind = None;
            question.response_input.clear();
            app.clear_escape_confirmation();
            return None;
        }
        if !app.confirm_escape_for_active_run("interrupt current work") {
            return None;
        }
        app.notice = Some("Interrupting current work…".to_owned());
        return Some(Effect::Cancel);
    }
    if let Some(input_kind) = question.input_kind {
        match key.code {
            KeyCode::Enter => {
                let response = question.response_input.text();
                if response.trim().is_empty() {
                    app.notice = Some("Type an answer first".to_owned());
                    return None;
                }
                return match input_kind {
                    QuestionInputKind::Note => {
                        question.options.get(question.selected).map(|option| {
                            Effect::ResolveQuestion {
                                selected_option: Some(option.label.clone()),
                                note: response,
                                custom_answer: String::new(),
                            }
                        })
                    }
                    QuestionInputKind::Custom => Some(Effect::ResolveQuestion {
                        selected_option: None,
                        note: String::new(),
                        custom_answer: response,
                    }),
                };
            }
            KeyCode::Backspace => question.response_input.backspace(),
            KeyCode::Delete => question.response_input.delete(),
            KeyCode::Left if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.move_word_left();
            }
            KeyCode::Right if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.move_word_right();
            }
            KeyCode::Left => question.response_input.move_left(),
            KeyCode::Right => question.response_input.move_right(),
            KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.move_buffer_home();
            }
            KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.move_buffer_end();
            }
            KeyCode::Home | KeyCode::Char('a')
                if key.code == KeyCode::Home || key.modifiers.contains(KeyModifiers::CONTROL) =>
            {
                question.response_input.move_home();
            }
            KeyCode::End | KeyCode::Char('e')
                if key.code == KeyCode::End || key.modifiers.contains(KeyModifiers::CONTROL) =>
            {
                question.response_input.move_end();
            }
            KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.delete_to_line_start();
            }
            KeyCode::Char('k') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.delete_to_line_end();
            }
            KeyCode::Char('w') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                question.response_input.delete_previous_word();
            }
            KeyCode::Char(value)
                if !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                question.response_input.insert_text(&value.to_string());
            }
            _ => {}
        }
        return None;
    }
    let choices = question.choice_count();
    match key.code {
        KeyCode::Up | KeyCode::Left => {
            question.selected = if question.selected == 0 {
                choices - 1
            } else {
                question.selected - 1
            };
            None
        }
        KeyCode::Down | KeyCode::Right => {
            question.selected = (question.selected + 1) % choices;
            None
        }
        KeyCode::Char('n' | 'N') => {
            if question.selected < question.custom_index() {
                question.start_note(question.selected);
            }
            None
        }
        KeyCode::Char(value @ '1'..='4') => {
            let index = usize::from(value as u8 - b'1');
            if index < choices {
                question.selected = index;
            }
            None
        }
        KeyCode::Enter if question.selected == question.custom_index() => {
            question.start_custom();
            None
        }
        KeyCode::Enter => {
            question
                .options
                .get(question.selected)
                .map(|option| Effect::ResolveQuestion {
                    selected_option: Some(option.label.clone()),
                    note: String::new(),
                    custom_answer: String::new(),
                })
        }
        _ => None,
    }
}

fn handle_inline_editor_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    if key.code == KeyCode::Esc {
        let kind = app.inline_editor.as_ref().map(|editor| editor.kind);
        app.inline_editor = None;
        if kind == Some(InlineEditorKind::PlanExecutionNote) {
            app.open_plan_review();
        } else {
            app.open_tasks();
        }
        return None;
    }
    if key.code == KeyCode::Enter
        && !key
            .modifiers
            .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT)
    {
        let editor = app.inline_editor.take()?;
        let (content, _pastes) = editor.input.message();
        if content.trim().is_empty() {
            app.inline_editor = Some(editor);
            app.notice = Some("Type a note first".to_owned());
            return None;
        }
        return Some(Effect::ExecutePlanWithNote(content));
    }
    let Some(editor) = &mut app.inline_editor else {
        return None;
    };
    match key.code {
        KeyCode::Backspace => editor.input.backspace(),
        KeyCode::Delete => editor.input.delete(),
        KeyCode::Left => editor.input.move_left(),
        KeyCode::Right => editor.input.move_right(),
        KeyCode::Home => editor.input.move_home(),
        KeyCode::End => editor.input.move_end(),
        KeyCode::Char(value)
            if !key
                .modifiers
                .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
        {
            editor.input.insert_text(&value.to_string());
        }
        _ => {}
    }
    None
}

fn handle_modal_key(app: &mut App, key: KeyEvent) -> Option<Effect> {
    let Some(modal) = &mut app.modal else {
        return None;
    };
    match modal {
        Modal::Approval(approval) => {
            let choices = if approval.allow_session { 3 } else { 2 };
            match key.code {
                KeyCode::PageUp => {
                    approval.detail_scroll = approval.detail_scroll.saturating_sub(3);
                    None
                }
                KeyCode::PageDown => {
                    approval.detail_scroll = approval.detail_scroll.saturating_add(3);
                    None
                }
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
        Modal::Goal(goal_modal) => {
            let status = goal_modal
                .goal
                .as_ref()
                .map(|goal| goal.status.clone())
                .unwrap_or_else(|| "none".to_owned());
            let can_control = !matches!(status.as_str(), "none" | "achieved" | "cancelled");
            match key.code {
                KeyCode::Esc if goal_modal.confirm_cancel => {
                    goal_modal.confirm_cancel = false;
                    None
                }
                KeyCode::Esc | KeyCode::Char('q') => {
                    app.modal = None;
                    None
                }
                KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down if can_control => {
                    goal_modal.selected = usize::from(goal_modal.selected == 0);
                    goal_modal.confirm_cancel = false;
                    None
                }
                KeyCode::Char('p') if matches!(status.as_str(), "running" | "yielded") => {
                    Some(Effect::Menu(MenuAction::PauseGoal))
                }
                KeyCode::Char('r')
                    if matches!(status.as_str(), "paused" | "blocked" | "yielded") =>
                {
                    Some(Effect::Menu(MenuAction::ResumeGoal))
                }
                KeyCode::Enter if can_control && goal_modal.selected == 0 => {
                    if matches!(status.as_str(), "running" | "yielded") {
                        Some(Effect::Menu(MenuAction::PauseGoal))
                    } else {
                        Some(Effect::Menu(MenuAction::ResumeGoal))
                    }
                }
                KeyCode::Enter if can_control => {
                    if goal_modal.confirm_cancel {
                        Some(Effect::Menu(MenuAction::CancelGoal))
                    } else {
                        goal_modal.confirm_cancel = true;
                        None
                    }
                }
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
        Modal::AgentEdit(editor) => {
            if key.code == KeyCode::Esc {
                app.modal = None;
                return None;
            }
            if key.modifiers.contains(KeyModifiers::CONTROL)
                && matches!(key.code, KeyCode::Enter | KeyCode::Char('j'))
            {
                if let Some(agent_id) = &editor.editing_agent_id {
                    return match agent_customization(editor) {
                        Ok((name, instructions, tools, skills)) => Some(Effect::UpdateAgent {
                            agent_id: agent_id.clone(),
                            name,
                            instructions,
                            tools,
                            skills,
                        }),
                        Err(message) => {
                            app.notice = Some(message);
                            None
                        }
                    };
                }
                return match agent_source(editor) {
                    Ok(source) => Some(Effect::CreateAgent(source)),
                    Err(message) => {
                        app.notice = Some(message);
                        None
                    }
                };
            }
            if editor.page == AgentEditorPage::Access {
                match key.code {
                    KeyCode::Left | KeyCode::Right
                        if key.modifiers.contains(KeyModifiers::CONTROL) =>
                    {
                        editor.page = AgentEditorPage::Identity;
                        None
                    }
                    KeyCode::Up | KeyCode::BackTab => {
                        editor.move_access(true);
                        None
                    }
                    KeyCode::Down | KeyCode::Tab => {
                        editor.move_access(false);
                        None
                    }
                    KeyCode::Char(' ') | KeyCode::Enter => {
                        editor.toggle_access();
                        None
                    }
                    _ => None,
                }
            } else {
                let name_field = editor.field == AgentEditField::Name;
                let slug_field = editor.field == AgentEditField::Slug;
                let text_edited = matches!(key.code, KeyCode::Backspace | KeyCode::Delete)
                    || matches!(key.code, KeyCode::Char(_))
                        && !key
                            .modifiers
                            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT);
                match key.code {
                    KeyCode::Left | KeyCode::Right
                        if key.modifiers.contains(KeyModifiers::CONTROL) =>
                    {
                        editor.page = AgentEditorPage::Access;
                        editor.access_selected = 0;
                    }
                    KeyCode::Left => editor.active_text_mut().move_left(),
                    KeyCode::Right => editor.active_text_mut().move_right(),
                    KeyCode::Up => editor.active_text_mut().move_up(),
                    KeyCode::Down => editor.active_text_mut().move_down(),
                    KeyCode::PageUp => {
                        for _ in 0..8 {
                            editor.active_text_mut().move_up();
                        }
                    }
                    KeyCode::PageDown => {
                        for _ in 0..8 {
                            editor.active_text_mut().move_down();
                        }
                    }
                    KeyCode::Tab if key.modifiers.contains(KeyModifiers::SHIFT) => {
                        editor.move_field(true);
                    }
                    KeyCode::Tab => editor.move_field(false),
                    KeyCode::BackTab => editor.move_field(true),
                    KeyCode::Enter if editor.field != AgentEditField::Instructions => {
                        editor.move_field(false);
                    }
                    KeyCode::Enter => editor.instructions.insert_text("\n"),
                    KeyCode::Backspace => editor.active_text_mut().backspace(),
                    KeyCode::Delete => editor.active_text_mut().delete(),
                    KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.active_text_mut().move_buffer_home();
                    }
                    KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.active_text_mut().move_buffer_end();
                    }
                    KeyCode::Home => editor.active_text_mut().move_home(),
                    KeyCode::End => editor.active_text_mut().move_end(),
                    KeyCode::Char(value)
                        if !key
                            .modifiers
                            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
                    {
                        editor.active_text_mut().insert_text(&value.to_string());
                    }
                    _ => return None,
                }
                if slug_field && text_edited {
                    editor.slug_manual = true;
                } else if name_field && text_edited {
                    editor.sync_slug();
                }
                None
            }
        }
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
        Modal::Help | Modal::Usage(_) | Modal::Session | Modal::Error(_) | Modal::Info { .. } => {
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
            if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Memory(browser)) = &mut app.modal
            {
                browser.detail_scroll = browser.detail_scroll.saturating_sub(3);
            } else if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Approval(approval)) = &mut app.modal
            {
                approval.detail_scroll = approval.detail_scroll.saturating_sub(3);
            } else if mouse_over_composer(app, mouse.column, mouse.row) {
                scroll_composer(app, -3);
            } else {
                app.scroll = app.scroll.saturating_add(3);
            }
            None
        }
        MouseEventKind::ScrollDown => {
            if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Memory(browser)) = &mut app.modal
            {
                browser.detail_scroll = browser.detail_scroll.saturating_add(3);
            } else if app.modal_viewport.point(mouse.column, mouse.row).is_some()
                && let Some(Modal::Approval(approval)) = &mut app.modal
            {
                approval.detail_scroll = approval.detail_scroll.saturating_add(3);
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
            if !matches!(
                region.as_ref().map(|item| &item.action),
                Some(HitAction::FocusComposer)
            ) {
                app.clear_composer_selection();
            }
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
                Some(HitAction::ToggleActivity(transcript_index)) => {
                    if let Some(point) = app.transcript_viewport.point(mouse.column, mouse.row) {
                        app.begin_transcript_selection(point);
                        app.pending_activity_toggle = Some(transcript_index);
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
                Some(HitAction::Question(index)) => {
                    app.clear_transcript_selection();
                    let question = app.question.as_mut()?;
                    if index == question.custom_index() {
                        question.start_custom();
                        None
                    } else {
                        question
                            .options
                            .get(index)
                            .map(|option| Effect::ResolveQuestion {
                                selected_option: Some(option.label.clone()),
                                note: String::new(),
                                custom_answer: String::new(),
                            })
                    }
                }
                Some(HitAction::QuestionNote(index)) => {
                    app.clear_transcript_selection();
                    if let Some(question) = &mut app.question {
                        question.start_note(index);
                    }
                    None
                }
                Some(HitAction::QueuedMessage(queue_id)) => {
                    app.clear_transcript_selection();
                    Some(Effect::TakeQueued(queue_id))
                }
                Some(HitAction::ShowSession) => {
                    app.clear_transcript_selection();
                    app.modal = Some(Modal::Session);
                    None
                }
                Some(HitAction::FocusComposer) => {
                    app.clear_transcript_selection();
                    app.clear_modal_selection();
                    if let Some(cursor) =
                        app.composer_viewport
                            .cursor_at(mouse.column, mouse.row, false)
                    {
                        app.begin_composer_selection(cursor);
                    } else {
                        app.clear_composer_selection();
                    }
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
            } else if app.selecting_composer {
                if let Some(cursor) = app
                    .composer_viewport
                    .cursor_at(mouse.column, mouse.row, true)
                {
                    app.update_composer_selection(cursor);
                }
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
            if app.selecting_composer {
                let anchor = app.composer_selection.map(|selection| selection.anchor);
                let leading = app
                    .composer_viewport
                    .cursor_at(mouse.column, mouse.row, false);
                let had_range = app
                    .composer_selection
                    .is_some_and(|selection| selection.anchor != selection.head);
                let cursor = if had_range || leading.is_some_and(|cursor| Some(cursor) != anchor) {
                    app.composer_viewport
                        .cursor_at(mouse.column, mouse.row, true)
                } else {
                    leading
                };
                if let Some(cursor) = cursor {
                    app.update_composer_selection(cursor);
                }
                if let Some(text) = app.finish_composer_selection() {
                    return Some(Effect::Copy(text));
                }
            }
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
                let pending_activity = app.pending_activity_toggle.take();
                if let Some(text) = app.finish_transcript_selection() {
                    return Some(Effect::Copy(text));
                }
                if let Some(index) = pending_thought {
                    app.focused_thought = Some(index);
                    app.toggle_thought(index);
                }
                if let Some(index) = pending_activity {
                    app.toggle_activity(index);
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
        if app.plan_ready() {
            app.open_plan_review();
        }
        return None;
    }
    if let Some(action) = parse_command(trimmed) {
        app.composer.clear();
        app.sheet = None;
        return Some(Effect::Menu(action));
    }
    let user_skill = trimmed
        .split_whitespace()
        .next()
        .and_then(|command| command.strip_prefix('/'))
        .is_some_and(|slug| app.skill_commands.iter().any(|skill| skill.slug == slug));
    if trimmed.starts_with('/') {
        if user_skill {
            return send_message(app, content, pastes, true);
        }
        app.notice = Some(
            "That advanced command remains in `hames repl`; press Ctrl+K for TUI actions"
                .to_owned(),
        );
        return None;
    }
    send_message(app, content, pastes, false)
}

fn send_message(
    app: &mut App,
    content: String,
    pastes: Vec<PasteSpan>,
    force_turn: bool,
) -> Option<Effect> {
    if app.active_run.is_some() && app.queued_messages.len() >= 2 {
        app.notice = Some("Queue full · edit or remove a queued message first".to_owned());
        return None;
    }
    app.sheet = None;
    app.scroll = 0;
    app.notice = Some(if app.active_run.is_some() {
        if app.session.interaction_mode == "plan" {
            "Queuing plan note…".to_owned()
        } else {
            "Queuing next turn…".to_owned()
        }
    } else {
        if app.plan_ready() {
            "Revising plan…".to_owned()
        } else {
            "Sending…".to_owned()
        }
    });
    let revising_plan = app.active_run.is_some()
        || app
            .plan
            .current
            .as_ref()
            .is_some_and(|plan| matches!(plan.status.as_str(), "ready" | "failed"));
    if app.session.interaction_mode == "plan" && revising_plan && !force_turn {
        Some(Effect::SendPlanNote(content, pastes))
    } else {
        Some(Effect::Send(content, pastes))
    }
}

fn send_now(app: &mut App) -> Option<Effect> {
    if app.composer.is_empty()
        && let Some(queue_id) = app.selected_queued_message().map(|item| item.id.clone())
    {
        app.notice = Some("Promoting queued message · interrupting current work…".to_owned());
        return Some(Effect::SendQueuedNow(queue_id));
    }
    if app.composer.is_empty() && app.queued_messages.len() > 1 {
        app.notice = Some("Select a queued message with Alt+↑/↓ first".to_owned());
        return None;
    }
    if app.session.interaction_mode == "plan" {
        return send_or_command(app);
    }
    if app.active_run.is_none() {
        app.notice = Some("Nothing to interrupt · Enter sends normally".to_owned());
        return None;
    }
    let (content, pastes) = app.composer.message();
    if content.trim().is_empty() {
        app.notice = Some("Type a message before using Ctrl+Enter send now".to_owned());
        return None;
    }
    if app.queued_messages.len() >= 2 {
        app.notice = Some("Queue full · edit or remove a queued message first".to_owned());
        return None;
    }
    app.sheet = None;
    app.scroll = 0;
    app.notice = Some("Interrupting · this message will run next…".to_owned());
    Some(Effect::SendNow(content, pastes))
}

fn action_error_message(error: &anyhow::Error) -> String {
    let message = format!("{error:#}");
    if let Some(body) = message.strip_prefix("gateway returned ")
        && let Some((_, json)) = body.split_once(": ")
        && let Ok(value) = serde_json::from_str::<serde_json::Value>(json)
        && let Some(detail) = value
            .get("error")
            .and_then(|error| error.get("message"))
            .and_then(serde_json::Value::as_str)
    {
        return detail.to_owned();
    }
    message.lines().next().unwrap_or("Action failed").to_owned()
}

fn parse_command(value: &str) -> Option<MenuAction> {
    let mut parts = value.split_whitespace();
    match parts.next()? {
        "/new" => Some(MenuAction::NewSession),
        "/clear" => Some(MenuAction::ClearSession),
        "/sessions" => Some(MenuAction::OpenSessions),
        "/queue" => match parts.next() {
            Some("clear") => Some(MenuAction::ClearQueue),
            Some(_) => None,
            None => Some(MenuAction::OpenQueue),
        },
        "/tasks" => Some(MenuAction::OpenTasks),
        "/plan" => match parts.next() {
            Some("proceed") => Some(MenuAction::ExecutePlan("keep".to_owned())),
            Some("compact") => Some(MenuAction::ExecutePlan("compact".to_owned())),
            Some("note") => {
                let note = parts.collect::<Vec<_>>().join(" ");
                if note.is_empty() {
                    Some(MenuAction::OpenPlanNote)
                } else {
                    Some(MenuAction::ExecutePlanWithNote(note))
                }
            }
            Some(_) => None,
            None => Some(MenuAction::OpenPlanReview),
        },
        "/compact" => Some(MenuAction::Compact),
        "/goal" => {
            let rest = parts.collect::<Vec<_>>();
            match rest.as_slice() {
                [] => Some(MenuAction::ShowGoal),
                [command] if *command == "pause" => Some(MenuAction::PauseGoal),
                [command] if *command == "resume" => Some(MenuAction::ResumeGoal),
                [command] if *command == "cancel" => Some(MenuAction::CancelGoal),
                _ => Some(MenuAction::StartGoal(rest.join(" "))),
            }
        }
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
        "/details" => Some(MenuAction::Details),
        "/memory" => Some(MenuAction::Memory),
        "/skills" => Some(MenuAction::Skills),
        "/evolution" | "/scars" => Some(MenuAction::Scars),
        "/heal" => Some(MenuAction::Heal),
        "/plugins" => Some(MenuAction::Plugins),
        "/mcp" => Some(MenuAction::Mcp),
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
        "/stop" => Some(MenuAction::StopTerminals),
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
        Effect::ResolveQuestion {
            selected_option,
            note,
            custom_answer,
        } => {
            let Some(question) = app.question.as_ref() else {
                return Ok(None);
            };
            let resolved = client
                .resolve_question(
                    &question.question_id,
                    selected_option.as_deref(),
                    &note,
                    &custom_answer,
                )
                .await?;
            debug_assert_eq!(resolved.question_id, question.question_id);
            debug_assert_eq!(resolved.selected_option, selected_option);
            debug_assert_eq!(resolved.note, note.trim());
            app.notice = Some(if resolved.custom {
                "Custom answer sent".to_owned()
            } else if !resolved.note.is_empty() {
                "Answer and note sent".to_owned()
            } else {
                "Answer sent".to_owned()
            });
        }
        Effect::Send(content, pastes) => {
            let submission_id = app.submission_id_for("turn", &content, &pastes);
            let accepted = client
                .send_message_with_pastes_id(
                    &app.session.id,
                    &submission_id,
                    &content,
                    false,
                    &pastes,
                )
                .await?;
            app.confirm_submission(&submission_id);
            app.composer.clear();
            app.history_index = None;
            app.history_draft = None;
            if accepted.disposition == "started" {
                app.begin_submitted_turn(accepted.run_id, content, pastes);
            } else if let Some(item) = accepted.queued {
                app.insert_queued_message(item);
            }
            app.notice = accepted
                .replayed
                .then(|| "Message was already accepted · state restored".to_owned());
        }
        Effect::SendPlanNote(content, pastes) => {
            let submission_id = app.submission_id_for("plan_note", &content, &pastes);
            let accepted = client
                .send_plan_note_with_id(&app.session.id, &submission_id, &content, &pastes)
                .await?;
            app.confirm_submission(&submission_id);
            app.composer.clear();
            app.inline_editor = None;
            app.sheet = None;
            app.history_index = None;
            app.history_draft = None;
            if accepted.disposition == "started" {
                app.begin_submitted_turn(accepted.run_id, content, pastes);
            } else if let Some(item) = accepted.queued {
                app.insert_queued_message(item);
            }
            app.notice = accepted
                .replayed
                .then(|| "Plan note was already accepted · state restored".to_owned());
        }
        Effect::ExecutePlanWithNote(content) => {
            let accepted = client
                .execute_plan(&app.session.id, "keep", Some(&content))
                .await?;
            app.set_plan(accepted.plan);
            app.set_tasks(accepted.tasks);
            app.session.interaction_mode = "auto".to_owned();
            app.begin_foreground_run(Some(accepted.run_id));
            app.composer.clear();
            app.inline_editor = None;
            app.sheet = None;
            app.notice = None;
        }
        Effect::SendNow(content, pastes) => {
            let submission_id = app.submission_id_for("send_now", &content, &pastes);
            let accepted = client
                .send_message_now_with_pastes_id(
                    &app.session.id,
                    &submission_id,
                    &content,
                    false,
                    &pastes,
                )
                .await?;
            app.confirm_submission(&submission_id);
            let started_immediately = accepted.disposition == "started";
            app.composer.clear();
            app.history_index = None;
            app.history_draft = None;
            if accepted.disposition == "started" {
                app.begin_submitted_turn(accepted.run_id, content, pastes);
            } else if let Some(item) = accepted.queued {
                app.insert_queued_message(item);
            }
            app.notice = Some(if accepted.replayed {
                "Priority turn was already accepted · state restored".to_owned()
            } else if started_immediately {
                "Priority turn started".to_owned()
            } else {
                "Current work interrupted · priority turn queued".to_owned()
            });
        }
        Effect::SendQueuedNow(queue_id) => {
            let accepted = client.send_queued_now(&app.session.id, &queue_id).await?;
            app.queued_messages.retain(|queued| queued.id != queue_id);
            app.reconcile_queue_selection();
            if let Some(item) = accepted.queued {
                app.insert_queued_message(item);
            }
            if accepted.disposition == "started" {
                app.begin_foreground_run(accepted.run_id);
                app.notice = Some("Queued message started now".to_owned());
            } else {
                app.notice = Some("Queued message promoted · current work interrupted".to_owned());
            }
        }
        Effect::TakeQueued(queue_id) => {
            let item = client.take_queued(&app.session.id, &queue_id).await?;
            app.queued_messages.retain(|queued| queued.id != queue_id);
            app.reconcile_queue_selection();
            app.load_queued_message(item);
        }
        Effect::TakeLatestQueued => {
            let item = client.take_latest_queued(&app.session.id).await?;
            app.queued_messages.retain(|queued| queued.id != item.id);
            app.reconcile_queue_selection();
            app.load_queued_message(item);
        }
        Effect::Cancel => {
            if let Some(run_id) = app.active_run.clone() {
                client.cancel(&run_id).await?;
                if app.restore_waiting_turn(&run_id) {
                    app.notice = Some("Interrupted message restored · edit and resend".to_owned());
                }
            }
        }
        Effect::PauseGoal => {
            let goal = client.pause_goal(&app.session.id).await?;
            app.goal = Some(goal.clone());
            app.active_run = None;
            app.run_started_at = None;
            app.notice = Some("Goal paused · use /goal resume to continue".to_owned());
            if let Some(Modal::Goal(modal)) = &mut app.modal {
                modal.goal = Some(goal);
                modal.selected = 0;
                modal.confirm_cancel = false;
            }
        }
        Effect::Copy(text) => {
            copy_to_clipboard(&text)?;
            app.show_copy_notice(text.chars().count());
        }
        Effect::OpenCommands => {
            refresh_skill_commands(client, app).await?;
            app.open_commands();
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
        Effect::DeleteQueued(queue_id) => {
            let state = client.delete_queued(&app.session.id, &queue_id).await?;
            app.set_queue(state);
            open_queue_sheet(app);
            app.notice = Some("Queued turn removed".to_owned());
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
        Effect::EditAgent(agent_id) => {
            app.notice = Some("Loading agent capsule…".to_owned());
            let (agent, tools, skills) = tokio::try_join!(
                client.agent(&agent_id),
                client.tools(),
                client.available_skills(&app.session.id)
            )?;
            app.modal = Some(Modal::AgentEdit(AgentEditor::edit(
                agent.agent.id,
                &agent.agent.name,
                &agent.instructions,
                tools,
                skills
                    .into_iter()
                    .map(|skill| (skill.slug, skill.name, skill.description))
                    .collect(),
                &agent.tools_allow,
                &agent.tools_deny,
                &agent.skills_allow,
                &agent.skills_deny,
                agent.skills_pin,
            )));
            app.notice = None;
        }
        Effect::CreateAgent(source) => {
            app.notice = Some("Creating agent capsule…".to_owned());
            let created = client.create_agent(None, "standard", Some(&source)).await?;
            app.modal = None;
            refresh_agents_sheet(client, app).await?;
            if let Some(sheet) = &mut app.sheet {
                sheet.selected = sheet
                    .options
                    .iter()
                    .position(|option| matches!(&option.action, MenuAction::SetAgent(id) if id == &created.agent.id))
                    .unwrap_or(0);
            }
            app.notice = Some(format!("Agent {} created", created.agent.name));
        }
        Effect::UpdateAgent {
            agent_id,
            name,
            instructions,
            tools,
            skills,
        } => {
            app.notice = Some("Saving agent capsule…".to_owned());
            let updated = client
                .update_agent(
                    &agent_id,
                    Some(&name),
                    Some(&instructions),
                    None,
                    Some(&tools),
                    Some(&skills),
                )
                .await?;
            app.modal = None;
            refresh_agents_sheet(client, app).await?;
            if let Some(sheet) = &mut app.sheet {
                sheet.selected = sheet
                    .options
                    .iter()
                    .position(|option| matches!(&option.action, MenuAction::SetAgent(id) if id == &agent_id))
                    .unwrap_or(0);
            }
            if app.session.agent_id == agent_id {
                app.agent_name = updated.agent.name.clone();
                app.context_usage = None;
            }
            app.notice = Some(format!("Agent {} updated", updated.agent.name));
        }
        Effect::DeleteAgent(agent_id) => {
            if agent_id == "default" {
                app.notice = Some("The default Hames agent cannot be deleted".to_owned());
                return Ok(None);
            }
            if app.session.agent_id == agent_id {
                app.session = client
                    .update_session_agent(&app.session.id, "default")
                    .await?;
                app.agent_name = client.agent("default").await?.agent.name;
                app.context_usage = None;
            }
            client.retire_agent(&agent_id).await?;
            refresh_agents_sheet(client, app).await?;
            app.notice = Some(format!("Agent {agent_id} removed"));
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
        MenuAction::PrepareSkill {
            slug,
            argument_hint,
        } => {
            app.composer.clear();
            app.composer.insert_text(&format!("/{slug} "));
            app.sheet = None;
            app.notice =
                (!argument_hint.is_empty()).then(|| format!("Arguments · {argument_hint}"));
        }
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
            if app.goal_keeps_session_alive() {
                client.cancel_goal(&app.session.id).await?;
            }
            let previous = app.session.clone();
            return Ok(Some(replace_session(client, paths, &previous).await?));
        }
        MenuAction::OpenSessions => {
            open_sessions_sheet(client, app).await?;
        }
        MenuAction::OpenQueue => open_queue_sheet(app),
        MenuAction::OpenTasks => app.open_tasks(),
        MenuAction::OpenPlanReview => {
            if app.plan_ready() {
                app.open_plan_review();
            } else {
                app.notice = Some("No plan is ready for review".to_owned());
            }
        }
        MenuAction::OpenPlanNote => {
            app.open_plan_review();
            if let Some(sheet) = &mut app.sheet {
                sheet.selected = 2;
            }
            app.inline_editor = Some(InlineEditor {
                kind: InlineEditorKind::PlanExecutionNote,
                input: Default::default(),
            });
        }
        MenuAction::ExecutePlanWithNote(content) => {
            let accepted = client
                .execute_plan(&app.session.id, "keep", Some(&content))
                .await?;
            app.composer.clear();
            app.set_plan(accepted.plan);
            app.set_tasks(accepted.tasks);
            app.session.interaction_mode = "auto".to_owned();
            app.begin_foreground_run(Some(accepted.run_id));
            app.sheet = None;
            app.inline_editor = None;
        }
        MenuAction::ExecutePlan(strategy) => {
            let accepted = client
                .execute_plan(&app.session.id, &strategy, None)
                .await?;
            app.set_plan(accepted.plan);
            app.set_tasks(accepted.tasks);
            app.session.interaction_mode = "auto".to_owned();
            app.begin_foreground_run(Some(accepted.run_id));
            app.sheet = None;
            app.inline_editor = None;
        }
        MenuAction::Compact => {
            let accepted = client.compact_session(&app.session.id).await?;
            app.active_run = Some(accepted.run_id);
            app.run_started_at = Some(std::time::Instant::now());
            app.notice = Some("Compacting older conversation…".to_owned());
        }
        MenuAction::ShowGoal => {
            app.modal = Some(Modal::Goal(GoalModal {
                goal: app.goal.clone(),
                selected: 0,
                confirm_cancel: false,
            }));
            app.sheet = None;
        }
        MenuAction::StartGoal(objective) => {
            let goal = client.start_goal(&app.session.id, &objective).await?;
            app.active_run.clone_from(&goal.current_run_id);
            app.run_started_at = goal.current_run_id.as_ref().map(|_| Instant::now());
            app.goal = Some(goal);
            app.sheet = None;
            app.notice = Some("Autonomous goal started".to_owned());
        }
        MenuAction::PauseGoal => {
            let goal = client.pause_goal(&app.session.id).await?;
            app.active_run = None;
            app.run_started_at = None;
            app.goal = Some(goal.clone());
            app.modal = Some(Modal::Goal(GoalModal {
                goal: Some(goal),
                selected: 0,
                confirm_cancel: false,
            }));
            app.notice = Some("Goal paused".to_owned());
        }
        MenuAction::ResumeGoal => {
            let goal = client.resume_goal(&app.session.id).await?;
            app.active_run.clone_from(&goal.current_run_id);
            app.run_started_at = goal.current_run_id.as_ref().map(|_| Instant::now());
            app.goal = Some(goal.clone());
            app.modal = Some(Modal::Goal(GoalModal {
                goal: Some(goal),
                selected: 0,
                confirm_cancel: false,
            }));
            app.notice = Some("Goal resumed".to_owned());
        }
        MenuAction::CancelGoal => {
            let goal = client.cancel_goal(&app.session.id).await?;
            app.active_run = None;
            app.run_started_at = None;
            app.goal = Some(goal.clone());
            app.modal = Some(Modal::Goal(GoalModal {
                goal: Some(goal),
                selected: 0,
                confirm_cancel: false,
            }));
            app.notice = Some("Goal cancelled".to_owned());
        }
        MenuAction::ClearQueue => {
            let state = client.clear_queue(&app.session.id).await?;
            app.set_queue(state);
            app.sheet = None;
            app.notice = Some("Queue cleared".to_owned());
        }
        MenuAction::EditQueued(queue_id) => {
            let item = client.take_queued(&app.session.id, &queue_id).await?;
            app.queued_messages.retain(|queued| queued.id != queue_id);
            app.reconcile_queue_selection();
            app.load_queued_message(item);
            app.sheet = None;
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
                        let provider_label = provider_menu_label(&profile);
                        for model in probe.models {
                            options.push(MenuOption {
                                label: model.id.clone(),
                                detail: model
                                    .parameter_size
                                    .unwrap_or_else(|| model.status.clone()),
                                action: MenuAction::ChooseModel {
                                    provider: profile.id.clone(),
                                    provider_label: provider_label.clone(),
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
                    title: "Models".to_owned(),
                    options,
                    selected: 0,
                    pending_delete: None,
                });
            }
        }
        MenuAction::ChooseModel {
            provider,
            provider_label: _,
            model,
        } => {
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
                app.remember_current_reasoning_effort();
                app.context_usage = None;
                app.sheet = None;
                app.notice = Some(format!("Using {provider} / {model} · reasoning off"));
                return Ok(None);
            }
            let selected_effort = model_effort_selection(
                &efforts,
                app.remembered_reasoning_effort(&provider, &model),
            );
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
                            reasoning: effort,
                        },
                    })
                    .collect(),
                selected: selected_effort,
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
            let current_index = efforts
                .iter()
                .position(|effort| effort == &app.session.reasoning_effort)
                .unwrap_or(0);
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
                selected: current_index,
                pending_delete: None,
            });
        }
        MenuAction::OpenAgents => {
            app.notice = Some("Loading agents…".to_owned());
            refresh_agents_sheet(client, app).await?;
            app.notice = None;
        }
        MenuAction::CreateAgent => {
            app.notice = Some("Loading tools and Skills…".to_owned());
            let (tools, skills) =
                tokio::try_join!(client.tools(), client.available_skills(&app.session.id))?;
            app.modal = Some(Modal::AgentEdit(AgentEditor::new(
                tools,
                skills
                    .into_iter()
                    .map(|skill| (skill.slug, skill.name, skill.description))
                    .collect(),
            )));
            app.notice = None;
        }
        MenuAction::OpenModes => app.open_modes(),
        MenuAction::OpenThemes => app.open_themes(),
        MenuAction::ShowSession => app.modal = Some(Modal::Session),
        MenuAction::Help => app.modal = Some(Modal::Help),
        MenuAction::CancelRun => {
            if app.active_run_is_goal_step() {
                let goal = client.pause_goal(&app.session.id).await?;
                app.active_run = None;
                app.run_started_at = None;
                app.goal = Some(goal);
                app.notice = Some("Goal paused".to_owned());
            } else if let Some(run_id) = app.active_run.clone() {
                client.cancel(&run_id).await?;
                app.notice = Some(if app.restore_waiting_turn(&run_id) {
                    "Interrupted message restored · edit and resend".to_owned()
                } else {
                    "Cancelling current work…".to_owned()
                });
            } else {
                app.notice = Some("No active work to cancel".to_owned());
            }
        }
        MenuAction::Status => {
            let health = client.health().await?;
            let mut lines = vec![
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
                format!("Terminals    {}", health.active_terminals),
                format!(
                    "External MCP {} configured · {} ready · {} degraded",
                    health.mcp_servers, health.mcp_ready, health.mcp_degraded
                ),
                format!("Provider     {}", health.default_provider),
            ];
            if let Some(search) = health.search {
                lines.push(format!("Web search   {}", search.mcp_status));
                if !search.protocol_version.is_empty() {
                    lines.push(format!("Search MCP   {}", search.protocol_version));
                }
                lines.push(format!("SearXNG      {}", search.service.status));
                if !search.service.runtime.is_empty() {
                    lines.push(format!("Runtime      {}", search.service.runtime));
                }
                if !search.error.is_empty() {
                    lines.push(format!("Search issue {}", search.error));
                }
            }
            app.modal = Some(info("Gateway status", lines));
        }
        MenuAction::Usage => {
            let usage = client.usage(&app.session.id).await?;
            app.context_usage = usage.latest_context.clone();
            app.modal = Some(Modal::Usage(UsageModal { usage }));
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
        MenuAction::Details => {
            app.diff_details = !app.diff_details;
            app.sheet = None;
            if app.diff_details {
                let failures = load_pending_diff_details(client, app).await;
                app.notice = Some(if failures == 0 {
                    "Expanded edit details inline".to_owned()
                } else {
                    format!("Expanded edit details · {failures} unavailable")
                });
            } else {
                app.notice = Some("Compacted edit details".to_owned());
            }
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
        MenuAction::Heal => {
            let accepted = client
                .heal_scars(&app.session.id, HEAL_SCARS_PROMPT)
                .await?;
            if accepted.disposition == "started" {
                app.begin_foreground_run(accepted.run_id);
                app.notice = Some("Healing scars…".to_owned());
            } else if let Some(item) = accepted.queued {
                app.insert_queued_message(item);
                app.notice = Some("Scar healing queued".to_owned());
            }
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
        MenuAction::Mcp => {
            let servers = client.mcp_servers().await?;
            let mut lines = vec![
                format!("{} external MCP servers", servers.len()),
                String::new(),
            ];
            lines.extend(servers.into_iter().take(16).map(|server| {
                let detail = if server.error.is_empty() {
                    format!(
                        "{} tools · {} resources",
                        server.tools.len(),
                        server.resources.len()
                    )
                } else {
                    server.error
                };
                format!(
                    "{:<16} {:<10} {:<6} {}",
                    server.id, server.status, server.transport, detail
                )
            }));
            app.modal = Some(info("MCP servers", lines));
        }
        MenuAction::StopTerminals => {
            let stopped = client.stop_background_terminals(&app.session.id).await?;
            if stopped.closed == 0 {
                app.notice = Some("No background terminals are running".to_owned());
            } else {
                app.notice = None;
            }
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
            app.modal = None;
            app.notice =
                Some("Workspace trust revoked; restart Hames to grant it again".to_owned());
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
            app.remember_current_reasoning_effort();
            app.context_usage = None;
            app.notice = Some(format!("Using {provider} / {model}"));
        }
        MenuAction::SetAgent(agent) => {
            app.session = client.update_session_agent(&app.session.id, &agent).await?;
            app.context_usage = None;
            app.agent_name = client.agent(&agent).await?.agent.name;
            refresh_skill_commands(client, app).await?;
            app.notice = Some(format!("Agent changed to {agent}"));
        }
        MenuAction::SetMode(mode) => {
            if !matches!(mode.as_str(), "manual" | "auto" | "plan") {
                bail!("mode must be manual, auto, or plan");
            }
            let approving_plan = mode == "auto" && app.plan_ready();
            app.session = client.update_session_mode(&app.session.id, &mode).await?;
            app.context_usage = None;
            app.notice = Some(if approving_plan {
                "Plan approved · implementing now…".to_owned()
            } else {
                match mode.as_str() {
                    "manual" => "Manual mode · ask before every edit".to_owned(),
                    "plan" => "Plan mode · inspect and test without code writes".to_owned(),
                    _ => "Auto mode · ask only for dangerous actions".to_owned(),
                }
            });
        }
        MenuAction::SetEffort(effort) => {
            app.session = client
                .update_session(
                    &app.session.id,
                    &app.session.provider,
                    &app.session.model,
                    &effort,
                )
                .await?;
            app.remember_current_reasoning_effort();
            app.context_usage = None;
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

async fn load_pending_diff_details(client: &GatewayClient, app: &mut App) -> usize {
    let event_ids = app.pending_diff_detail_events();
    let mut failures = 0usize;
    for event_id in event_ids {
        match client.tool_result_details(&event_id).await {
            Ok(details) => app.apply_tool_result_details(details),
            Err(error) => {
                failures += 1;
                app.mark_tool_result_details_failed(&event_id, action_error_message(&error));
            }
        }
    }
    failures
}

fn ingest_envelope(app: &mut App, envelope: LiveEnvelope) -> bool {
    if envelope.durable {
        if let Some(event) = envelope.event {
            let terminal = matches!(
                event.event_type.as_str(),
                "run.completed" | "run.cancelled" | "run.failed"
            );
            app.ingest_durable(event, true);
            return terminal;
        }
    } else if let (Some(run_id), Some(event_type), Some(payload)) =
        (envelope.run_id, envelope.event_type, envelope.payload)
    {
        app.ingest_transient(&run_id, &event_type, &payload);
    }
    false
}

struct StreamMessage {
    session_id: String,
    payload: StreamPayload,
}

enum StreamPayload {
    Envelope(Box<LiveEnvelope>),
    State(ConnectionState),
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
            let reason = format!("{error:#}");
            let _ = tx
                .send(StreamMessage {
                    session_id: session_id.clone(),
                    payload: StreamPayload::State(ConnectionState::Offline {
                        reason: reason.clone(),
                    }),
                })
                .await;
            let _ = tx
                .send(StreamMessage {
                    session_id,
                    payload: StreamPayload::Warning(format!("Live updates paused: {reason}")),
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
    let mut failures = 0_u32;
    if tx
        .send(StreamMessage {
            session_id: session_id.clone(),
            payload: StreamPayload::State(ConnectionState::Connecting),
        })
        .await
        .is_err()
    {
        return Ok(());
    }
    loop {
        let response = match client.event_stream(&session_id, after).await {
            Ok(response) => response,
            Err(_) => {
                failures += 1;
                if !send_reconnecting(&tx, &session_id, failures).await {
                    return Ok(());
                }
                tokio::time::sleep(event_reconnect_delay(failures)).await;
                continue;
            }
        };
        if tx
            .send(StreamMessage {
                session_id: session_id.clone(),
                payload: StreamPayload::State(ConnectionState::Connected),
            })
            .await
            .is_err()
        {
            return Ok(());
        }
        let mut bytes = response.bytes_stream();
        let mut decoder = SseDecoder::default();
        while let Some(chunk) = bytes.next().await {
            let chunk = match chunk {
                Ok(chunk) => chunk,
                Err(_) => break,
            };
            failures = 0;
            for data in decoder.push(&chunk)? {
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
        if !send_reconnecting(&tx, &session_id, failures).await {
            return Ok(());
        }
        tokio::time::sleep(event_reconnect_delay(failures)).await;
    }
}

async fn send_reconnecting(
    tx: &mpsc::Sender<StreamMessage>,
    session_id: &str,
    attempt: u32,
) -> bool {
    tx.send(StreamMessage {
        session_id: session_id.to_owned(),
        payload: StreamPayload::State(ConnectionState::Reconnecting { attempt }),
    })
    .await
    .is_ok()
}

#[cfg(unix)]
struct ShutdownSignals {
    terminate: tokio::signal::unix::Signal,
    hangup: tokio::signal::unix::Signal,
}

#[cfg(unix)]
impl ShutdownSignals {
    fn new() -> Result<Self> {
        use tokio::signal::unix::{SignalKind, signal};

        Ok(Self {
            terminate: signal(SignalKind::terminate())?,
            hangup: signal(SignalKind::hangup())?,
        })
    }

    async fn recv(&mut self) {
        tokio::select! {
            _ = self.terminate.recv() => {}
            _ = self.hangup.recv() => {}
        }
    }
}

#[cfg(not(unix))]
struct ShutdownSignals;

#[cfg(not(unix))]
impl ShutdownSignals {
    fn new() -> Result<Self> {
        Ok(Self)
    }

    async fn recv(&mut self) {
        std::future::pending::<()>().await;
    }
}

#[derive(Default)]
struct TerminalModes {
    raw: bool,
    alternate_screen: bool,
    mouse_capture: bool,
    focus_change: bool,
    bracketed_paste: bool,
    keyboard_enhancement: bool,
}

impl TerminalModes {
    fn enter() -> Result<Self> {
        let mut modes = Self::default();
        enable_raw_mode()?;
        modes.raw = true;
        TERMINAL_ACTIVE.store(true, Ordering::SeqCst);
        let mut stdout = io::stdout();
        execute!(stdout, EnterAlternateScreen)?;
        modes.alternate_screen = true;
        execute!(stdout, EnableMouseCapture)?;
        modes.mouse_capture = true;
        execute!(stdout, EnableFocusChange)?;
        modes.focus_change = true;
        execute!(stdout, EnableBracketedPaste)?;
        modes.bracketed_paste = true;
        execute!(
            stdout,
            PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
        )?;
        modes.keyboard_enhancement = true;
        Ok(modes)
    }

    fn restore<W: Write>(&mut self, output: &mut W) {
        if self.keyboard_enhancement {
            let _ = execute!(output, PopKeyboardEnhancementFlags);
            self.keyboard_enhancement = false;
        }
        if self.bracketed_paste {
            let _ = execute!(output, DisableBracketedPaste);
            self.bracketed_paste = false;
        }
        if self.focus_change {
            let _ = execute!(output, DisableFocusChange);
            self.focus_change = false;
        }
        if self.mouse_capture {
            let _ = execute!(output, DisableMouseCapture);
            self.mouse_capture = false;
        }
        if self.alternate_screen {
            let _ = execute!(output, LeaveAlternateScreen);
            self.alternate_screen = false;
        }
        let _ = execute!(output, Show);
        if self.raw {
            let _ = disable_raw_mode();
            self.raw = false;
        }
        TERMINAL_ACTIVE.store(false, Ordering::SeqCst);
    }
}

impl Drop for TerminalModes {
    fn drop(&mut self) {
        self.restore(&mut io::stdout());
    }
}

fn install_terminal_panic_hook() {
    TERMINAL_PANIC_HOOK.call_once(|| {
        let previous = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            if TERMINAL_ACTIVE.swap(false, Ordering::SeqCst) {
                let mut stdout = io::stdout();
                let _ = execute!(stdout, PopKeyboardEnhancementFlags);
                let _ = execute!(stdout, DisableBracketedPaste);
                let _ = execute!(stdout, DisableFocusChange);
                let _ = execute!(stdout, DisableMouseCapture);
                let _ = execute!(stdout, LeaveAlternateScreen);
                let _ = execute!(stdout, Show);
                let _ = disable_raw_mode();
            }
            previous(info);
        }));
    });
}

struct TerminalGuard {
    terminal: Terminal<CrosstermBackend<Stdout>>,
    modes: TerminalModes,
    last_title: String,
    title_frame: usize,
    title_frame_at: Instant,
    title_working: bool,
    was_animating: bool,
}

impl TerminalGuard {
    fn enter() -> Result<Self> {
        install_terminal_panic_hook();
        let modes = TerminalModes::enter()?;
        let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
        terminal.clear()?;
        Ok(Self {
            terminal,
            modes,
            last_title: String::new(),
            title_frame: 0,
            title_frame_at: Instant::now(),
            title_working: false,
            was_animating: false,
        })
    }

    fn draw(&mut self, app: &mut App) -> Result<()> {
        let area = self.terminal.size()?;
        if area.width == 0 || area.height == 0 {
            return Ok(());
        }
        let working = app.active_run.is_some();
        if working != self.title_working {
            self.title_working = working;
            self.title_frame = 0;
            self.title_frame_at = Instant::now();
        } else if working && self.title_frame_at.elapsed() >= Duration::from_millis(360) {
            self.title_frame = (self.title_frame + 1) % 4;
            self.title_frame_at = Instant::now();
        }
        let title = terminal_tab_title(app, self.title_frame);
        if self.last_title != title {
            execute!(self.terminal.backend_mut(), SetTitle(&title))?;
            self.last_title = title;
        }

        let animating = app.animating();
        let force_full_repaint = should_force_full_repaint(self.was_animating, animating);
        execute!(self.terminal.backend_mut(), BeginSynchronizedUpdate)?;
        let draw_result = (|| -> io::Result<()> {
            if force_full_repaint {
                self.terminal.clear()?;
            }
            self.terminal.draw(|frame| view::draw(frame, app))?;
            Ok(())
        })();
        let end_result = execute!(self.terminal.backend_mut(), EndSynchronizedUpdate);
        draw_result?;
        end_result?;
        self.was_animating = animating;
        Ok(())
    }
}

fn should_force_full_repaint(was_animating: bool, animating: bool) -> bool {
    was_animating && !animating
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = self.terminal.show_cursor();
        self.modes.restore(self.terminal.backend_mut());
    }
}

fn short_id(value: &str) -> &str {
    value.get(..8).unwrap_or(value)
}

fn terminal_tab_title(app: &App, frame: usize) -> String {
    let icon = if app.active_run.is_some() {
        ["◇", "◈", "◆", "◈"][frame % 4]
    } else {
        "◇"
    };
    let status = if matches!(app.modal, Some(Modal::Approval(_))) {
        "Permission"
    } else {
        view::current_activity(app)
    };
    format!(
        "{icon} {} · {status}",
        app.session
            .title
            .as_deref()
            .unwrap_or("New session")
            .replace(['\n', '\r', '\t'], " ")
    )
}

fn agent_source(editor: &AgentEditor) -> std::result::Result<String, String> {
    let name = editor.name.text().trim().to_owned();
    let slug = editor.slug.text().trim().to_owned();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("Agent name must be between 1 and 80 characters".to_owned());
    }
    let valid_slug = slug.len() <= 63
        && slug
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_lowercase())
        && slug.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        });
    if !valid_slug {
        return Err(
            "Slug must start with a-z and contain only lowercase letters, digits, or -".to_owned(),
        );
    }

    let tools = agent_access_update(&editor.tools, &[]);
    let skills = agent_access_update(&editor.skills, &editor.skill_pins);
    let metadata = serde_json::json!({
        "id": slug,
        "name": name,
        "authority": "standard",
        "tools": tools,
        "skills": skills,
    });
    let frontmatter = serde_json::to_string_pretty(&metadata)
        .map_err(|error| format!("Could not format AGENT.md: {error}"))?;
    Ok(format!(
        "---\n{frontmatter}\n---\n{}\n",
        editor.instructions.text().trim()
    ))
}

fn agent_customization(
    editor: &AgentEditor,
) -> std::result::Result<(String, String, AgentAccessUpdate, AgentAccessUpdate), String> {
    let name = editor.name.text().trim().to_owned();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("Agent name must be between 1 and 80 characters".to_owned());
    }
    let instructions = editor.instructions.text().trim().to_owned();
    if instructions.is_empty() {
        return Err("AGENT.md instructions cannot be empty".to_owned());
    }
    Ok((
        name,
        instructions,
        agent_access_update(&editor.tools, &[]),
        agent_access_update(&editor.skills, &editor.skill_pins),
    ))
}

fn agent_access_update(choices: &[app::AgentChoice], pinned: &[String]) -> AgentAccessUpdate {
    let mut allow = choices
        .iter()
        .filter(|choice| choice.selected)
        .map(|choice| choice.id.clone())
        .collect::<Vec<_>>();
    let deny = choices
        .iter()
        .filter(|choice| !choice.selected)
        .map(|choice| choice.id.clone())
        .collect::<Vec<_>>();
    if deny.is_empty() {
        allow.clear();
    }
    let pin = pinned
        .iter()
        .filter(|id| {
            choices
                .iter()
                .any(|choice| choice.id == **id && choice.selected)
        })
        .cloned()
        .collect();
    AgentAccessUpdate { allow, deny, pin }
}

fn compact_home(value: &str) -> String {
    env::var("HOME")
        .ok()
        .and_then(|home| value.strip_prefix(&home).map(|suffix| format!("~{suffix}")))
        .unwrap_or_else(|| value.to_owned())
}

fn effort_label(value: &str) -> &str {
    if value.is_empty() { "not set" } else { value }
}

fn model_efforts(model: &ProviderModel) -> Vec<String> {
    if model.reasoning_supported != Some(true) {
        return Vec::new();
    }
    if model.reasoning_efforts.is_empty() || model.reasoning_efforts == ["on"] {
        return vec!["on".to_owned(), "off".to_owned()];
    }
    let mut efforts = model.reasoning_efforts.clone();
    efforts.retain(|effort| effort != "default");
    if !efforts.iter().any(|effort| effort == "off") {
        efforts.push("off".to_owned());
    }
    efforts
}

fn model_effort_selection(efforts: &[String], remembered: Option<&str>) -> usize {
    remembered
        .and_then(|remembered| efforts.iter().position(|effort| effort == remembered))
        .unwrap_or(0)
}

fn provider_menu_label(profile: &crate::api::ProviderProfile) -> String {
    match profile.adapter.as_str() {
        "llama_cpp" => "llama.cpp".to_owned(),
        "ollama" => "Ollama".to_owned(),
        "openai" => "OpenAI API".to_owned(),
        "codex" => "Codex / ChatGPT".to_owned(),
        _ => profile.id.clone(),
    }
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
        Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent,
        MouseEventKind,
    };

    use super::{
        Effect, action_error_message, agent_source, handle_key, handle_mouse,
        handle_terminal_event, model_effort_selection, model_efforts, next_mode, parse_command,
        pointer_top, repeat_safe_key, session_exit_notice, should_force_full_repaint,
        terminal_tab_title, workspace_identity,
    };
    use crate::api::{
        Goal, MemoryRecord, PlanRevision, PlanState, ProviderModel, QueuedMessage, Scar, Session,
        SessionTask, SessionTaskList, SkillSummary, SseDecoder,
    };
    use crate::tui::app::{
        AgentEditor, App, ComposerCell, ComposerRowMap, ComposerViewport, HitAction, HitRegion,
        InlineEditor, InlineEditorKind, MemoryBrowser, MenuAction, MenuOption, Modal,
        QuestionInputKind, QuestionOption, QuestionTray, ScarBrowser, ScarEditField, ScrollDrag,
        ScrollTarget, Sheet, SheetKind, ThemeKind, TranscriptItem, TranscriptViewport,
    };

    #[test]
    fn settling_animation_forces_one_full_terminal_repaint() {
        assert!(!should_force_full_repaint(false, false));
        assert!(!should_force_full_repaint(false, true));
        assert!(!should_force_full_repaint(true, true));
        assert!(should_force_full_repaint(true, false));
    }

    #[test]
    fn sse_decoder_handles_fragmented_frames() {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"data: {\"dur").unwrap().is_empty());
        assert_eq!(
            decoder.push(b"able\":true}\n\n").unwrap(),
            vec!["{\"durable\":true}"]
        );
    }

    #[test]
    fn key_repeat_is_limited_to_editing_and_navigation() {
        let mut app = App::new(session(), Vec::new(), true);
        let repeated =
            |code, modifiers| KeyEvent::new_with_kind(code, modifiers, KeyEventKind::Repeat);
        assert!(repeat_safe_key(
            &app,
            repeated(KeyCode::Char('a'), KeyModifiers::NONE)
        ));
        assert!(repeat_safe_key(
            &app,
            repeated(KeyCode::Backspace, KeyModifiers::NONE)
        ));
        assert!(repeat_safe_key(
            &app,
            repeated(KeyCode::Down, KeyModifiers::NONE)
        ));
        assert!(!repeat_safe_key(
            &app,
            repeated(KeyCode::Enter, KeyModifiers::NONE)
        ));
        assert!(!repeat_safe_key(
            &app,
            repeated(KeyCode::Esc, KeyModifiers::NONE)
        ));
        assert!(!repeat_safe_key(
            &app,
            repeated(KeyCode::Tab, KeyModifiers::NONE)
        ));
        assert!(!repeat_safe_key(
            &app,
            repeated(KeyCode::Char('k'), KeyModifiers::CONTROL)
        ));
        app.composer.insert_text("do not submit twice");
        assert!(
            handle_terminal_event(
                &mut app,
                Event::Key(repeated(KeyCode::Enter, KeyModifiers::NONE))
            )
            .is_none()
        );
        assert_eq!(app.composer.text(), "do not submit twice");
        app.sheet = Some(Sheet {
            kind: SheetKind::Modes,
            title: "Mode".to_owned(),
            options: Vec::new(),
            selected: 0,
            pending_delete: None,
        });
        assert!(!repeat_safe_key(
            &app,
            repeated(KeyCode::Char('q'), KeyModifiers::NONE)
        ));
    }

    #[test]
    fn empty_session_exit_has_no_notice() {
        assert_eq!(session_exit_notice("empty", true), None);
        assert_eq!(
            session_exit_notice("kept", false).as_deref(),
            Some("Resume session with\n  /resume kept")
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
        assert!(matches!(parse_command("/mcp"), Some(MenuAction::Mcp)));
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
        assert!(matches!(
            parse_command("/queue"),
            Some(MenuAction::OpenQueue)
        ));
        assert!(matches!(
            parse_command("/queue clear"),
            Some(MenuAction::ClearQueue)
        ));
        assert!(matches!(
            parse_command("/compact"),
            Some(MenuAction::Compact)
        ));
        assert!(matches!(
            parse_command("/tasks"),
            Some(MenuAction::OpenTasks)
        ));
        assert!(matches!(
            parse_command("/plan"),
            Some(MenuAction::OpenPlanReview)
        ));
        assert!(matches!(
            parse_command("/plan proceed"),
            Some(MenuAction::ExecutePlan(strategy)) if strategy == "keep"
        ));
        assert!(matches!(
            parse_command("/plan compact"),
            Some(MenuAction::ExecutePlan(strategy)) if strategy == "compact"
        ));
        assert!(matches!(
            parse_command("/plan note keep the API small"),
            Some(MenuAction::ExecutePlanWithNote(note)) if note == "keep the API small"
        ));
        assert!(matches!(parse_command("/goal"), Some(MenuAction::ShowGoal)));
        assert!(matches!(
            parse_command("/goal pause"),
            Some(MenuAction::PauseGoal)
        ));
        assert!(matches!(
            parse_command("/goal ship the release"),
            Some(MenuAction::StartGoal(objective)) if objective == "ship the release"
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
        assert!(matches!(parse_command("/heal"), Some(MenuAction::Heal)));
        assert!(matches!(
            parse_command("/details"),
            Some(MenuAction::Details)
        ));
        assert!(matches!(
            parse_command("/stop"),
            Some(MenuAction::StopTerminals)
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
    fn resume_command_opens_sessions_on_first_enter() {
        let mut app = App::new(session(), Vec::new(), true);
        app.composer.insert_text("/resume");
        app.update_slash_sheet();

        let effect = handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

        assert!(matches!(
            effect,
            Some(Effect::Menu(MenuAction::OpenSessions))
        ));
        assert!(app.composer.is_empty());
    }

    #[test]
    fn user_invocable_skills_join_the_palette_and_send_as_turns_in_plan_mode() {
        let mut app = App::new(session(), Vec::new(), true);
        app.session.interaction_mode = "plan".to_owned();
        app.skill_commands = vec![SkillSummary {
            id: "external:user:teach".to_owned(),
            slug: "teach".to_owned(),
            version_id: "teach-v1".to_owned(),
            version: 1,
            name: "Teach".to_owned(),
            description: "Teach a topic".to_owned(),
            scope: "global".to_owned(),
            scope_key: None,
            status: "active".to_owned(),
            content_hash: "hash".to_owned(),
            triggers: Vec::new(),
            tools: Vec::new(),
            scripts: Vec::new(),
            score: 0.0,
            pinned: false,
            invocation: "user".to_owned(),
            argument_hint: "[topic]".to_owned(),
        }];
        assert!(
            app.command_options()
                .iter()
                .any(|option| option.label == "/teach")
        );
        app.composer.insert_text("/teach state machines");
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::Send(content, _)) if content == "/teach state machines"
        ));
    }

    #[test]
    fn workspace_identity_uses_directory_and_current_git_branch() {
        let root = env!("CARGO_MANIFEST_DIR").to_owned() + "/../..";
        let (directory, reference) = workspace_identity(&root);
        assert_eq!(
            directory,
            std::path::Path::new(&root)
                .canonicalize()
                .unwrap()
                .to_string_lossy()
        );
        assert!(reference.is_some_and(|value| !value.is_empty()));

        let (directory, reference) = workspace_identity("/tmp/hames-not-a-repository");
        assert_eq!(directory, "/tmp/hames-not-a-repository");
        assert!(reference.is_none());
    }

    #[test]
    fn active_enter_queues_without_clearing_and_up_edits_the_newest_pending_turn() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.composer.insert_text("follow this up");
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::Send(content, _)) if content == "follow this up"
        ));
        assert_eq!(app.composer.text(), "follow this up");

        app.composer.clear();
        app.queued_messages.push(queued("queue-1", "first"));
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE)),
            Some(Effect::TakeLatestQueued)
        ));
    }

    #[test]
    fn plan_ready_empty_enter_opens_review_and_live_input_becomes_a_note() {
        let mut plan_session = session();
        plan_session.interaction_mode = "plan".to_owned();
        let mut app = App::new(plan_session, Vec::new(), true);
        app.set_plan(ready_plan());
        app.focused_thought = Some(0);

        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)).is_none());
        assert!(
            app.sheet
                .as_ref()
                .is_some_and(|sheet| sheet.kind == SheetKind::PlanReview)
        );

        app.sheet = None;
        app.active_run = Some("run-draft".to_owned());
        app.composer.insert_text("Prefer the smaller API");
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::SendPlanNote(content, _)) if content == "Prefer the smaller API"
        ));
        assert_eq!(app.composer.text(), "Prefer the smaller API");
    }

    #[test]
    fn continue_with_note_is_an_execution_action_not_another_plan_turn() {
        let mut plan_session = session();
        plan_session.interaction_mode = "plan".to_owned();
        let mut app = App::new(plan_session, Vec::new(), true);
        app.set_plan(ready_plan());
        app.inline_editor = Some(InlineEditor {
            kind: InlineEditorKind::PlanExecutionNote,
            input: Default::default(),
        });
        app.inline_editor
            .as_mut()
            .unwrap()
            .input
            .insert_text("Preserve compatibility");

        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::ExecutePlanWithNote(note)) if note == "Preserve compatibility"
        ));
    }

    #[test]
    fn task_sheet_is_read_only_for_the_user() {
        let mut app = App::new(session(), Vec::new(), true);
        app.set_tasks(SessionTaskList {
            session_id: "session-1".to_owned(),
            title: "Approved plan".to_owned(),
            revision: 1,
            items: vec![
                SessionTask {
                    id: "task-1".to_owned(),
                    text: "Inspect it".to_owned(),
                    status: "completed".to_owned(),
                    position: 0,
                    created_by: "plan".to_owned(),
                },
                SessionTask {
                    id: "task-2".to_owned(),
                    text: "Implement it".to_owned(),
                    status: "in_progress".to_owned(),
                    position: 1,
                    created_by: "plan".to_owned(),
                },
                SessionTask {
                    id: "task-3".to_owned(),
                    text: "Verify it".to_owned(),
                    status: "pending".to_owned(),
                    position: 2,
                    created_by: "plan".to_owned(),
                },
            ],
            updated_at: "now".to_owned(),
        });
        app.open_tasks();
        assert_eq!(app.sheet.as_ref().unwrap().options[0].label, "[✓]");
        assert_eq!(app.sheet.as_ref().unwrap().selected, 1);
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)).is_none());
        assert!(app.sheet.is_some());
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('n'), KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert!(app.inline_editor.is_none());

        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert_eq!(
            app.sheet.as_ref().and_then(|sheet| sheet.pending_delete),
            None
        );
        assert!(app.sheet.is_some());
    }

    #[test]
    fn ctrl_enter_sends_the_composer_next_without_losing_its_text_early() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.queued_messages = vec![queued("queue-1", "existing")];
        app.selected_queue_id = Some("queue-1".to_owned());
        app.composer.insert_text("urgent correction");

        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            ),
            Some(Effect::SendNow(content, _)) if content == "urgent correction"
        ));
        assert_eq!(app.composer.text(), "urgent correction");
        assert!(app.notice.as_deref().unwrap().contains("will run next"));
    }

    #[test]
    fn ctrl_enter_sends_the_only_queued_message_without_explicit_selection() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.queued_messages = vec![queued("queue-1", "only")];

        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            ),
            Some(Effect::SendQueuedNow(queue_id)) if queue_id == "queue-1"
        ));
        assert!(
            app.notice
                .as_deref()
                .unwrap()
                .contains("Promoting queued message")
        );
    }

    #[test]
    fn alt_arrows_select_queued_messages_for_ctrl_enter() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.queued_messages = vec![queued("queue-1", "older"), queued("queue-2", "latest")];

        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert!(app.notice.as_deref().unwrap().contains("Select a queued"));
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::ALT)).is_none());
        assert_eq!(app.selected_queue_id.as_deref(), Some("queue-2"));
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::ALT)).is_none());
        assert_eq!(app.selected_queue_id.as_deref(), Some("queue-1"));
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::ALT)).is_none());
        assert_eq!(app.selected_queue_id.as_deref(), Some("queue-2"));
        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            ),
            Some(Effect::SendQueuedNow(queue_id)) if queue_id == "queue-2"
        ));
    }

    #[test]
    fn gateway_conflict_errors_are_reduced_to_notice_text() {
        let error = anyhow::Error::msg(
            r#"gateway returned 409 Conflict: {"error":{"code":"session_run_active","message":"cannot clear a session during an active run"}}"#,
        );
        assert_eq!(
            action_error_message(&error),
            "cannot clear a session during an active run"
        );
    }

    #[test]
    fn full_queue_preserves_the_unsent_composer_and_history_follows_the_queue() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-active".to_owned());
        app.queued_messages = vec![queued("queue-1", "first"), queued("queue-2", "second")];
        app.composer.insert_text("third");
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)).is_none());
        assert_eq!(app.composer.text(), "third");
        assert!(app.notice.as_deref().unwrap().contains("Queue full"));

        app.queued_messages.clear();
        app.composer.clear();
        app.message_history = vec!["older".to_owned(), "newer".to_owned()];
        handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        assert_eq!(app.composer.text(), "newer");
        app.composer.clear();
        handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        assert_eq!(app.composer.text(), "older");
    }

    #[test]
    fn repeated_up_walks_deep_input_history() {
        let mut app = App::new(session(), Vec::new(), true);
        app.message_history = (0..50).map(|index| format!("message {index}")).collect();

        for expected in (25..50).rev() {
            assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE)).is_none());
            assert_eq!(app.composer.text(), format!("message {expected}"));
        }
    }

    #[test]
    fn shift_tab_mode_cycle_is_stable() {
        assert_eq!(next_mode("manual"), "auto");
        assert_eq!(next_mode("auto"), "plan");
        assert_eq!(next_mode("plan"), "manual");
    }

    #[test]
    fn question_note_and_custom_answer_are_distinct_paths() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-question".to_owned());
        app.question = Some(QuestionTray {
            question_id: "question-1".to_owned(),
            run_id: "run-question".to_owned(),
            question: "Which direction?".to_owned(),
            options: vec![
                QuestionOption {
                    label: "Subdued".to_owned(),
                    description: "Calm and restrained.".to_owned(),
                },
                QuestionOption {
                    label: "Bright".to_owned(),
                    description: String::new(),
                },
            ],
            selected: 0,
            input_kind: None,
            response_input: Default::default(),
        });
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE),
        );
        assert_eq!(
            app.question.as_ref().unwrap().input_kind,
            Some(QuestionInputKind::Note)
        );
        assert_eq!(app.question.as_ref().unwrap().selected, 0);
        for value in "Keep it calm".chars() {
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char(value), KeyModifiers::NONE),
            );
        }
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        let question = app.question.as_ref().unwrap();
        assert_eq!(question.input_kind, None);
        assert!(question.response_input.is_empty());
        assert_eq!(question.selected, 0);
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)),
            Some(Effect::Cancel)
        ));

        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE),
        );
        for value in "Keep it calm".chars() {
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char(value), KeyModifiers::NONE),
            );
        }
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
            Some(Effect::ResolveQuestion { selected_option: Some(option), note, custom_answer })
                if option == "Subdued" && note == "Keep it calm" && custom_answer.is_empty()
        ));

        let question = app.question.as_mut().unwrap();
        question.input_kind = None;
        question.response_input.clear();
        question.selected = question.custom_index();
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE),
        );
        assert_eq!(app.question.as_ref().unwrap().input_kind, None);
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('2'), KeyModifiers::NONE),
        );
        assert_eq!(app.question.as_ref().unwrap().selected, 1);
        let question = app.question.as_mut().unwrap();
        question.selected = question.custom_index();
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)).is_none());
        assert_eq!(
            app.question.as_ref().unwrap().input_kind,
            Some(QuestionInputKind::Custom)
        );
    }

    #[test]
    fn terminal_tab_title_shows_session_name_and_live_state() {
        let mut app = App::new(session(), Vec::new(), true);
        app.agent_name = "Careful Reviewer".to_owned();
        app.session.title = Some("Transcript polish".to_owned());

        assert_eq!(terminal_tab_title(&app, 0), "◇ Transcript polish · Ready");
        app.active_run = Some("run-title".to_owned());
        app.transcript.push(TranscriptItem::Thought {
            run_id: "run-title".to_owned(),
            content: String::new(),
            duration_seconds: 0.0,
            interrupted: false,
            live: true,
            collapsed: true,
        });
        assert_eq!(
            terminal_tab_title(&app, 0),
            "◇ Transcript polish · Thinking"
        );
        assert_eq!(
            terminal_tab_title(&app, 1),
            "◈ Transcript polish · Thinking"
        );
        assert_eq!(
            terminal_tab_title(&app, 2),
            "◆ Transcript polish · Thinking"
        );
        assert_eq!(
            terminal_tab_title(&app, 3),
            "◈ Transcript polish · Thinking"
        );
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
    fn agent_management_protects_default_and_confirms_custom_deletion() {
        let mut app = App::new(session(), Vec::new(), true);
        app.sheet = Some(Sheet {
            kind: SheetKind::Agents,
            title: "Agents".to_owned(),
            options: vec![
                MenuOption {
                    label: "Hames".to_owned(),
                    detail: "standard · default".to_owned(),
                    action: MenuAction::SetAgent("default".to_owned()),
                },
                MenuOption {
                    label: "Reviewer".to_owned(),
                    detail: "standard · reviewer".to_owned(),
                    action: MenuAction::SetAgent("reviewer".to_owned()),
                },
            ],
            selected: 0,
            pending_delete: None,
        });
        let ctrl_d = KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL);

        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert_eq!(app.sheet.as_ref().unwrap().pending_delete, None);
        app.sheet.as_mut().unwrap().selected = 1;
        assert!(handle_key(&mut app, ctrl_d).is_none());
        assert_eq!(app.sheet.as_ref().unwrap().pending_delete, Some(1));
        assert!(matches!(
            handle_key(&mut app, ctrl_d),
            Some(Effect::DeleteAgent(agent_id)) if agent_id == "reviewer"
        ));
    }

    #[test]
    fn agent_sheet_ctrl_n_opens_creation_flow() {
        let mut app = App::new(session(), Vec::new(), true);
        app.sheet = Some(Sheet {
            kind: SheetKind::Agents,
            title: "Agents".to_owned(),
            options: Vec::new(),
            selected: 0,
            pending_delete: None,
        });

        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('n'), KeyModifiers::CONTROL)
            ),
            Some(Effect::Menu(MenuAction::CreateAgent))
        ));
    }

    #[test]
    fn agent_sheet_ctrl_e_opens_editing_for_default() {
        let mut app = App::new(session(), Vec::new(), true);
        app.sheet = Some(Sheet {
            kind: SheetKind::Agents,
            title: "Agents".to_owned(),
            options: vec![MenuOption {
                label: "Hames".to_owned(),
                detail: "standard · default".to_owned(),
                action: MenuAction::SetAgent("default".to_owned()),
            }],
            selected: 0,
            pending_delete: None,
        });

        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL)
            ),
            Some(Effect::EditAgent(agent_id)) if agent_id == "default"
        ));
    }

    #[test]
    fn new_agent_source_explicitly_records_selected_and_denied_capabilities() {
        let mut editor = AgentEditor::new(
            vec!["read_file".to_owned(), "write_file".to_owned()],
            vec![(
                "testing".to_owned(),
                "Testing".to_owned(),
                "Run the focused suite".to_owned(),
            )],
        );
        editor.name.insert_text("Code Reviewer");
        editor.sync_slug();
        editor.instructions.insert_text("# Role\nReview carefully.");
        editor.tools[1].selected = false;

        let source = agent_source(&editor).unwrap();
        assert!(source.contains("\"id\": \"code-reviewer\""));
        assert!(source.contains("\"allow\": [\n      \"read_file\""));
        assert!(source.contains("\"deny\": [\n      \"write_file\""));
        assert!(source.ends_with("# Role\nReview carefully.\n"));
    }

    #[test]
    fn agent_editor_uses_tabs_for_fields_arrows_for_text_and_ctrl_arrows_for_pages() {
        let mut editor = AgentEditor::new(vec!["read_file".to_owned()], Vec::new());
        editor.name.insert_text("Reviewer");
        editor.sync_slug();
        editor.instructions.insert_text("one\ntwo\nthree");
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::AgentEdit(editor));

        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE)).is_none());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            &app.modal,
            Some(Modal::AgentEdit(editor))
                if editor.field == crate::tui::app::AgentEditField::Instructions
        ));
        let cursor_before = match &app.modal {
            Some(Modal::AgentEdit(editor)) => editor.instructions.cursor,
            _ => unreachable!(),
        };
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            &app.modal,
            Some(Modal::AgentEdit(editor)) if editor.instructions.cursor < cursor_before
        ));
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Left, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            &app.modal,
            Some(Modal::AgentEdit(editor)) if editor.instructions.cursor < cursor_before - 1
        ));
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Right, KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert!(matches!(
            &app.modal,
            Some(Modal::AgentEdit(editor))
                if editor.page == crate::tui::app::AgentEditorPage::Access
        ));
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Left, KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            ),
            Some(Effect::CreateAgent(source)) if source.contains("\"id\": \"reviewer\"")
        ));
    }

    #[test]
    fn existing_agent_editor_keeps_id_fixed_and_updates_capabilities() {
        let editor = AgentEditor::edit(
            "default".to_owned(),
            "Navigator",
            "# Role\nGuide carefully.",
            vec!["read_file".to_owned(), "write_file".to_owned()],
            vec![(
                "testing".to_owned(),
                "Testing".to_owned(),
                "Run tests".to_owned(),
            )],
            &[],
            &["write_file".to_owned()],
            &[],
            &["testing".to_owned()],
            Vec::new(),
        );
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::AgentEdit(editor));

        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Right, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            &app.modal,
            Some(Modal::AgentEdit(editor))
                if editor.page == crate::tui::app::AgentEditorPage::Identity
                    && editor.slug.text() == "default"
        ));
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Right, KeyModifiers::CONTROL)
            )
            .is_none()
        );
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)).is_none());
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char(' '), KeyModifiers::NONE)
            )
            .is_none()
        );
        assert!(matches!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Enter, KeyModifiers::CONTROL)
            ),
            Some(Effect::UpdateAgent { agent_id, name, instructions, tools, skills })
                if agent_id == "default"
                    && name == "Navigator"
                    && instructions == "# Role\nGuide carefully."
                    && tools.allow.is_empty()
                    && tools.deny.is_empty()
                    && skills.deny == ["testing"]
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
    fn two_escapes_interrupt_an_active_run() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert_eq!(
            app.notice.as_deref(),
            Some("Press Esc again to interrupt current work")
        );
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)),
            Some(Effect::Cancel)
        ));
    }

    #[test]
    fn another_key_disarms_escape_confirmation() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert!(
            handle_key(
                &mut app,
                KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE)
            )
            .is_none()
        );
        assert!(app.notice.is_none());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
    }

    #[test]
    fn escape_confirmation_and_notice_expire_together() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert_eq!(
            app.notice.as_deref(),
            Some("Press Esc again to interrupt current work")
        );

        app.expire_escape_confirmation_for_test();
        assert!(app.transient_notice().is_none());
        assert!(app.notice.is_none());
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert_eq!(
            app.notice.as_deref(),
            Some("Press Esc again to interrupt current work")
        );
    }

    #[test]
    fn two_escapes_pause_an_active_goal_step() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-goal".to_owned());
        app.goal = Some(Goal {
            id: "goal-1".to_owned(),
            session_id: "session-1".to_owned(),
            objective: "Finish the release".to_owned(),
            status: "running".to_owned(),
            step_count: 1,
            current_run_id: Some("run-goal".to_owned()),
            latest_summary: String::new(),
            latest_evidence: Vec::new(),
            repeated_no_progress: 0,
            active_seconds: 0.0,
            active_since: Some("2026-08-24T00:00:00Z".to_owned()),
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            updated_at: "2026-08-24T00:00:00Z".to_owned(),
        });
        assert!(handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)).is_none());
        assert!(matches!(
            handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)),
            Some(Effect::PauseGoal)
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
            line_offset: 0,
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
    fn mouse_drag_selects_and_copies_composer_text() {
        let mut app = App::new(session(), Vec::new(), true);
        app.composer.insert_text("Hames input");
        app.composer_viewport = ComposerViewport {
            x: 2,
            y: 3,
            width: 30,
            height: 1,
            line_offset: 0,
            rows: vec![ComposerRowMap {
                start_cursor: 0,
                end_cursor: 11,
                cells: (0..11)
                    .map(|index| ComposerCell {
                        start_column: index + 2,
                        end_column: index + 3,
                        unit_index: index,
                    })
                    .collect(),
            }],
            cursor_row: 0,
            cursor_column: 13,
        };
        app.hits.push(HitRegion {
            x: 1,
            y: 2,
            width: 32,
            height: 3,
            action: HitAction::FocusComposer,
        });

        for (kind, column) in [
            (MouseEventKind::Down(MouseButton::Left), 6),
            (MouseEventKind::Drag(MouseButton::Left), 10),
        ] {
            assert!(
                handle_mouse(
                    &mut app,
                    MouseEvent {
                        kind,
                        column,
                        row: 3,
                        modifiers: KeyModifiers::NONE,
                    },
                )
                .is_none()
            );
        }
        assert!(matches!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Up(MouseButton::Left),
                    column: 10,
                    row: 3,
                    modifiers: KeyModifiers::NONE,
                },
            ),
            Some(Effect::Copy(text)) if text == "mes i"
        ));

        assert!(
            handle_mouse(
                &mut app,
                MouseEvent {
                    kind: MouseEventKind::Down(MouseButton::Left),
                    column: 7,
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
                    kind: MouseEventKind::Up(MouseButton::Left),
                    column: 7,
                    row: 3,
                    modifiers: KeyModifiers::NONE,
                },
            )
            .is_none()
        );
        assert_eq!(app.composer.cursor, 3);
    }

    #[test]
    fn clicking_an_activity_heading_toggles_its_complete_history() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Activity {
            run_id: "run-1".to_owned(),
            rows: Vec::new(),
            collapsed: false,
            created_at: None,
        });
        app.transcript_viewport = TranscriptViewport {
            x: 2,
            y: 3,
            width: 30,
            height: 1,
            line_offset: 0,
            lines: vec!["◆ Run · 2 actions · complete  ▾".to_owned()],
        };
        app.hits.push(HitRegion {
            x: 2,
            y: 3,
            width: 30,
            height: 1,
            action: HitAction::ToggleActivity(0),
        });

        for kind in [
            MouseEventKind::Down(MouseButton::Left),
            MouseEventKind::Up(MouseButton::Left),
        ] {
            assert!(
                handle_mouse(
                    &mut app,
                    MouseEvent {
                        kind,
                        column: 4,
                        row: 3,
                        modifiers: KeyModifiers::NONE,
                    },
                )
                .is_none()
            );
        }

        let TranscriptItem::Activity { collapsed, .. } = &app.transcript[0] else {
            panic!("fixture should remain an activity item");
        };
        assert!(*collapsed);
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
            line_offset: 0,
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
        assert_eq!(model_efforts(&qwen), ["low", "medium", "off"]);

        let legacy_default = ProviderModel {
            reasoning_efforts: vec!["default".to_owned(), "high".to_owned()],
            ..qwen
        };
        assert_eq!(model_efforts(&legacy_default), ["high", "off"]);
    }

    #[test]
    fn remembered_model_effort_is_highlighted_for_confirmation() {
        let efforts = vec![
            "low".to_owned(),
            "medium".to_owned(),
            "xhigh".to_owned(),
            "off".to_owned(),
        ];

        assert_eq!(model_effort_selection(&efforts, Some("medium")), 1);
        assert_eq!(model_effort_selection(&efforts, Some("unsupported")), 0);
        assert_eq!(model_effort_selection(&efforts, None), 0);
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

    fn ready_plan() -> PlanState {
        PlanState {
            session_id: "session-1".to_owned(),
            current: Some(PlanRevision {
                id: "plan-1".to_owned(),
                session_id: "session-1".to_owned(),
                revision: 1,
                title: "Implementation plan".to_owned(),
                markdown: "# Implementation plan\n\n## Tasks\n- [ ] Implement it".to_owned(),
                tasks: vec!["Implement it".to_owned()],
                source_run_id: "run-plan".to_owned(),
                supersedes_plan_id: None,
                status: "ready".to_owned(),
                strategy: None,
                execution_run_id: None,
                execution_note: String::new(),
                error: String::new(),
                created_at: "2026-08-24T00:00:00Z".to_owned(),
                updated_at: "2026-08-24T00:00:00Z".to_owned(),
            }),
            revisions: Vec::new(),
        }
    }

    fn queued(id: &str, content: &str) -> QueuedMessage {
        QueuedMessage {
            id: id.to_owned(),
            session_id: "session-1".to_owned(),
            content: content.to_owned(),
            remember: false,
            paste_spans: Vec::new(),
            purpose: "turn".to_owned(),
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            position: 1,
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
