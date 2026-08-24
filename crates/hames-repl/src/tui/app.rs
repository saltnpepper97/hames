use std::collections::HashSet;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;
use unicode_segmentation::UnicodeSegmentation;

use crate::api::{Event, PasteSpan, Session};

pub const LARGE_PASTE_LINES: usize = 4;
pub const LARGE_PASTE_BYTES: usize = 400;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ComposerUnit {
    Text(String),
    Paste(String),
}

#[derive(Default)]
pub struct Composer {
    pub units: Vec<ComposerUnit>,
    pub cursor: usize,
}

impl Composer {
    pub fn is_empty(&self) -> bool {
        self.units.is_empty()
    }

    pub fn clear(&mut self) {
        self.units.clear();
        self.cursor = 0;
    }

    pub fn text(&self) -> String {
        let mut result = String::new();
        for unit in &self.units {
            match unit {
                ComposerUnit::Text(value) | ComposerUnit::Paste(value) => result.push_str(value),
            }
        }
        result
    }

    pub fn insert_text(&mut self, value: &str) {
        for grapheme in value.graphemes(true) {
            self.units
                .insert(self.cursor, ComposerUnit::Text(grapheme.to_owned()));
            self.cursor += 1;
        }
    }

    pub fn insert_paste(&mut self, value: String) {
        let line_count = value
            .as_bytes()
            .iter()
            .filter(|byte| **byte == b'\n')
            .count()
            + 1;
        if line_count >= LARGE_PASTE_LINES || value.len() >= LARGE_PASTE_BYTES {
            self.units.insert(self.cursor, ComposerUnit::Paste(value));
            self.cursor += 1;
        } else {
            self.insert_text(&value);
        }
    }

    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.units.remove(self.cursor);
        }
    }

    pub fn delete(&mut self) {
        if self.cursor < self.units.len() {
            self.units.remove(self.cursor);
        }
    }

    pub fn move_left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    pub fn move_right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.units.len());
    }

    pub fn move_home(&mut self) {
        self.cursor = 0;
    }

    pub fn move_end(&mut self) {
        self.cursor = self.units.len();
    }

    pub fn message(&self) -> (String, Vec<PasteSpan>) {
        let mut content = String::new();
        let mut spans = Vec::new();
        for unit in &self.units {
            let start = content.len();
            match unit {
                ComposerUnit::Text(value) => content.push_str(value),
                ComposerUnit::Paste(value) => {
                    content.push_str(value);
                    spans.push(PasteSpan {
                        start_byte: start,
                        end_byte: content.len(),
                        line_count: value
                            .as_bytes()
                            .iter()
                            .filter(|byte| **byte == b'\n')
                            .count()
                            + 1,
                        byte_count: value.len(),
                    });
                }
            }
        }
        (content, spans)
    }

    pub fn paste_at_cursor(&self) -> Option<&str> {
        self.units
            .get(self.cursor)
            .or_else(|| {
                self.cursor
                    .checked_sub(1)
                    .and_then(|index| self.units.get(index))
            })
            .and_then(|unit| match unit {
                ComposerUnit::Paste(value) => Some(value.as_str()),
                ComposerUnit::Text(_) => None,
            })
    }

    pub fn remove_adjacent_paste(&mut self) -> bool {
        let index = self
            .units
            .get(self.cursor)
            .filter(|unit| matches!(unit, ComposerUnit::Paste(_)))
            .map(|_| self.cursor)
            .or_else(|| {
                self.cursor
                    .checked_sub(1)
                    .filter(|index| matches!(self.units.get(*index), Some(ComposerUnit::Paste(_))))
            });
        if let Some(index) = index {
            self.units.remove(index);
            self.cursor = index.min(self.units.len());
            true
        } else {
            false
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActivityCategory {
    Explore,
    Change,
    Run,
    Delegate,
    Skills,
    Memory,
    Scars,
    Plugin,
}

impl ActivityCategory {
    pub fn label(self) -> &'static str {
        match self {
            Self::Explore => "Explore",
            Self::Change => "Change",
            Self::Run => "Run",
            Self::Delegate => "Delegate",
            Self::Skills => "Skills",
            Self::Memory => "Memory",
            Self::Scars => "Scars",
            Self::Plugin => "Plugin",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActivityPhase {
    Preparing,
    Checking,
    Approval,
    Running,
    Completed,
    Failed,
    Rejected,
    Cancelled,
}

impl ActivityPhase {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Failed | Self::Rejected | Self::Cancelled
        )
    }
}

#[derive(Clone, Debug)]
pub struct ActivityRow {
    pub index: u64,
    pub tool_call_id: Option<String>,
    pub name: String,
    pub arguments: Value,
    pub argument_parts: String,
    pub phase: ActivityPhase,
    pub summary: String,
    pub duration_seconds: f64,
}

impl ActivityRow {
    fn new(index: u64) -> Self {
        Self {
            index,
            tool_call_id: None,
            name: String::new(),
            arguments: Value::Null,
            argument_parts: String::new(),
            phase: ActivityPhase::Preparing,
            summary: String::new(),
            duration_seconds: 0.0,
        }
    }

    pub fn category(&self) -> ActivityCategory {
        category_for_tool(&self.name)
    }

    pub fn target(&self) -> String {
        let path = self
            .arguments
            .get("path")
            .or_else(|| self.arguments.get("command"))
            .or_else(|| self.arguments.get("goal"))
            .or_else(|| self.arguments.get("query"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        if path.is_empty() {
            self.name.replace('_', " ")
        } else {
            display_path(path)
        }
    }

    pub fn verb(&self) -> &'static str {
        let write = matches!(self.name.as_str(), "write_file" | "edit_file");
        let read = matches!(self.name.as_str(), "read_file" | "list_dir");
        match (self.phase, write, read) {
            (ActivityPhase::Preparing, true, _) => "Preparing write",
            (ActivityPhase::Preparing, _, true) => "Preparing explore",
            (ActivityPhase::Preparing, _, _) => "Preparing",
            (ActivityPhase::Checking, true, _) => "Checking write",
            (ActivityPhase::Checking, _, true) => "Checking access",
            (ActivityPhase::Checking, _, _) => "Checking",
            (ActivityPhase::Approval, _, _) => "Awaiting permission",
            (ActivityPhase::Running, true, _) => "Writing",
            (ActivityPhase::Running, _, true) => "Exploring",
            (ActivityPhase::Running, _, _) => "Running",
            (ActivityPhase::Completed, true, _) => "Wrote",
            (ActivityPhase::Completed, _, true) => "Explored",
            (ActivityPhase::Completed, _, _) => "Completed",
            (ActivityPhase::Failed, _, _) => "Failed",
            (ActivityPhase::Rejected, _, _) => "Rejected",
            (ActivityPhase::Cancelled, _, _) => "Cancelled",
        }
    }
}

#[derive(Clone, Debug)]
pub enum TranscriptItem {
    User {
        content: String,
        paste_spans: Vec<PasteSpan>,
    },
    Thought {
        run_id: String,
        content: String,
        duration_seconds: f64,
        live: bool,
        collapsed: bool,
    },
    Assistant {
        run_id: String,
        content: String,
        live: bool,
    },
    Activity {
        run_id: String,
        rows: Vec<ActivityRow>,
    },
    Status {
        text: String,
        error: bool,
    },
}

#[derive(Clone, Debug)]
pub struct ApprovalModal {
    pub approval_id: String,
    pub request_hash: String,
    pub name: String,
    pub reason: String,
    pub arguments: String,
    pub allow_session: bool,
    pub selected: usize,
}

#[derive(Clone, Debug)]
pub enum Modal {
    Trust,
    Approval(ApprovalModal),
    Help,
    Session,
    PastePreview(String),
    Error(String),
    Info { title: String, lines: Vec<String> },
}

#[derive(Clone, Debug)]
pub enum MenuAction {
    NewSession,
    OpenSessions,
    ForkSession,
    OpenModels,
    OpenEfforts,
    OpenAgents,
    OpenModes,
    OpenThemes,
    ShowSession,
    Help,
    CancelRun,
    Status,
    Usage,
    Events,
    Inspect,
    Context,
    Memory,
    Skills,
    Scars,
    Plugins,
    Trust,
    RevokeTrust,
    Export {
        path: String,
        format: String,
    },
    CaptureMemory(String),
    Correct(String),
    Quit,
    Resume(String),
    SetModel {
        provider: String,
        model: String,
        reasoning: String,
    },
    SetAgent(String),
    SetMode(String),
    SetEffort(String),
    SetTitle(String),
    ChooseModel {
        provider: String,
        model: String,
    },
    SetTheme(ThemeKind),
}

#[derive(Clone, Debug)]
pub struct MenuOption {
    pub label: String,
    pub detail: String,
    pub action: MenuAction,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SheetKind {
    Commands,
    Sessions,
    Models,
    Efforts,
    Agents,
    Modes,
    Themes,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ThemeKind {
    Hames,
    Terminal,
}

impl ThemeKind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Hames => "Hames",
            Self::Terminal => "Terminal",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScrollTarget {
    Transcript,
    Composer,
}

#[derive(Clone, Debug)]
pub struct ScrollDrag {
    pub target: ScrollTarget,
    pub y: u16,
    pub height: u16,
    pub max_top: usize,
}

#[derive(Clone, Debug)]
pub struct Sheet {
    pub kind: SheetKind,
    pub title: String,
    pub options: Vec<MenuOption>,
    pub selected: usize,
}

#[derive(Clone, Debug)]
pub enum HitAction {
    ToggleThought(usize),
    SelectSheet(usize),
    Approval(usize),
    CloseModal,
    TrustWorkspace,
    Quit,
    ShowSession,
    FocusComposer,
    Scrollbar {
        target: ScrollTarget,
        content_len: usize,
        viewport_len: usize,
    },
}

#[derive(Clone, Debug)]
pub struct HitRegion {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
    pub action: HitAction,
}

impl HitRegion {
    pub fn contains(&self, x: u16, y: u16) -> bool {
        x >= self.x
            && x < self.x.saturating_add(self.width)
            && y >= self.y
            && y < self.y.saturating_add(self.height)
    }
}

pub struct App {
    pub session: Session,
    pub transcript: Vec<TranscriptItem>,
    pub composer: Composer,
    pub active_run: Option<String>,
    pub run_started_at: Option<Instant>,
    pub trusted: bool,
    pub modal: Option<Modal>,
    pub sheet: Option<Sheet>,
    pub notice: Option<String>,
    pub scroll: usize,
    pub composer_scroll: Option<usize>,
    pub scroll_drag: Option<ScrollDrag>,
    pub last_sequence: u64,
    pub seen_events: HashSet<String>,
    pub context_tokens: u64,
    pub context_window: u64,
    pub tick: u64,
    pub hits: Vec<HitRegion>,
    pub should_quit: bool,
    pub focused_thought: Option<usize>,
    pub theme: ThemeKind,
}

impl App {
    pub fn new(session: Session, events: Vec<Event>, trusted: bool) -> Self {
        let context_window = session.context_window_tokens;
        let mut app = Self {
            session,
            transcript: Vec::new(),
            composer: Composer::default(),
            active_run: None,
            run_started_at: None,
            trusted,
            modal: (!trusted).then_some(Modal::Trust),
            sheet: None,
            notice: None,
            scroll: 0,
            composer_scroll: None,
            scroll_drag: None,
            last_sequence: 0,
            seen_events: HashSet::new(),
            context_tokens: 0,
            context_window,
            tick: 0,
            hits: Vec::new(),
            should_quit: false,
            focused_thought: None,
            theme: ThemeKind::Hames,
        };
        for event in events {
            app.ingest_durable(event, false);
        }
        app
    }

    pub fn animating(&self) -> bool {
        self.active_run.is_some()
            || self.transcript.iter().any(|item| match item {
                TranscriptItem::Thought { live, .. } | TranscriptItem::Assistant { live, .. } => {
                    *live
                }
                TranscriptItem::Activity { rows, .. } => {
                    rows.iter().any(|row| !row.phase.terminal())
                }
                _ => false,
            })
    }

    pub fn command_options(&self) -> Vec<MenuOption> {
        vec![
            option(
                "New session",
                "start a clean conversation",
                MenuAction::NewSession,
            ),
            option("Sessions", "resume recent work", MenuAction::OpenSessions),
            option("Fork", "branch this session", MenuAction::ForkSession),
            option(
                "Model",
                "provider, model, and reasoning",
                MenuAction::OpenModels,
            ),
            option(
                "Effort",
                "change reasoning without changing model",
                MenuAction::OpenEfforts,
            ),
            option("Agent", "change the active capsule", MenuAction::OpenAgents),
            option("Mode", "manual, auto, or plan", MenuAction::OpenModes),
            option("Themes", "Hames or terminal colors", MenuAction::OpenThemes),
            option(
                "Session info",
                "identity and continuity",
                MenuAction::ShowSession,
            ),
            option("Project & trust", "workspace authority", MenuAction::Trust),
            option("Status", "gateway and active work", MenuAction::Status),
            option("Usage", "tokens and model requests", MenuAction::Usage),
            option("Events", "recent durable history", MenuAction::Events),
            option("Inspect run", "latest run timeline", MenuAction::Inspect),
            option("Context", "latest compiled context", MenuAction::Context),
            option("Memory", "active durable memories", MenuAction::Memory),
            option("Skills", "active procedural catalog", MenuAction::Skills),
            option("Scars", "corrections and repair state", MenuAction::Scars),
            option("Plugins", "installed capabilities", MenuAction::Plugins),
            option("Help", "keyboard and mouse guide", MenuAction::Help),
            option("Cancel run", "stop current work", MenuAction::CancelRun),
            option("Quit", "leave the gateway running", MenuAction::Quit),
        ]
    }

    pub fn open_commands(&mut self) {
        self.sheet = Some(Sheet {
            kind: SheetKind::Commands,
            title: "Command palette".to_owned(),
            options: self.command_options(),
            selected: 0,
        });
        self.modal = None;
    }

    pub fn open_modes(&mut self) {
        self.sheet = Some(Sheet {
            kind: SheetKind::Modes,
            title: "Execution mode".to_owned(),
            options: vec![
                option(
                    "Manual",
                    "ask before every edit",
                    MenuAction::SetMode("manual".to_owned()),
                ),
                option(
                    "Auto",
                    "ask only for dangerous actions",
                    MenuAction::SetMode("auto".to_owned()),
                ),
                option(
                    "Plan",
                    "inspect and test, no code writes",
                    MenuAction::SetMode("plan".to_owned()),
                ),
            ],
            selected: match self.session.interaction_mode.as_str() {
                "manual" => 0,
                "plan" => 2,
                _ => 1,
            },
        });
        self.modal = None;
    }

    pub fn open_themes(&mut self) {
        self.sheet = Some(Sheet {
            kind: SheetKind::Themes,
            title: "Color theme".to_owned(),
            options: vec![
                option(
                    "Hames",
                    "calm custom RGB palette",
                    MenuAction::SetTheme(ThemeKind::Hames),
                ),
                option(
                    "Terminal",
                    "terminal-native ANSI colors",
                    MenuAction::SetTheme(ThemeKind::Terminal),
                ),
            ],
            selected: usize::from(self.theme == ThemeKind::Terminal),
        });
        self.modal = None;
    }

    pub fn update_slash_sheet(&mut self) {
        let content = self.composer.text();
        if !content.starts_with('/') || content.contains(char::is_whitespace) {
            if self
                .sheet
                .as_ref()
                .is_some_and(|sheet| sheet.kind == SheetKind::Commands)
            {
                self.sheet = None;
            }
            return;
        }
        let query = content.trim_start_matches('/').to_ascii_lowercase();
        let mut options = self.command_options();
        options.retain(|item| item.label.to_ascii_lowercase().contains(&query));
        self.sheet = Some(Sheet {
            kind: SheetKind::Commands,
            title: "Slash commands".to_owned(),
            options,
            selected: 0,
        });
    }

    pub fn handle_composer_key(&mut self, key: KeyEvent) -> bool {
        self.composer_scroll = None;
        match key.code {
            KeyCode::Backspace => self.composer.backspace(),
            KeyCode::Delete => self.composer.delete(),
            KeyCode::Left => self.composer.move_left(),
            KeyCode::Right => self.composer.move_right(),
            KeyCode::Home => self.composer.move_home(),
            KeyCode::End => self.composer.move_end(),
            KeyCode::Enter
                if key
                    .modifiers
                    .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT) =>
            {
                self.composer.insert_text("\n")
            }
            KeyCode::Char('j') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.composer.insert_text("\n")
            }
            KeyCode::Char(value)
                if !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                self.composer.insert_text(&value.to_string())
            }
            _ => return false,
        }
        self.update_slash_sheet();
        true
    }

    pub fn selected_sheet_action(&self) -> Option<MenuAction> {
        self.sheet
            .as_ref()
            .and_then(|sheet| sheet.options.get(sheet.selected))
            .map(|option| option.action.clone())
    }

    pub fn ingest_transient(&mut self, run_id: &str, event_type: &str, payload: &Value) {
        match event_type {
            "response.reasoning_delta" => {
                let text = payload
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let index = self.ensure_thought(run_id, true);
                if let TranscriptItem::Thought { content, .. } = &mut self.transcript[index] {
                    content.push_str(text);
                }
            }
            "response.text_delta" => {
                self.finish_live_thought(run_id);
                let text = payload
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let index = self.ensure_assistant(run_id, true);
                if let TranscriptItem::Assistant { content, .. } = &mut self.transcript[index] {
                    content.push_str(text);
                }
            }
            "response.tool_call_delta" => {
                self.finish_live_thought(run_id);
                let index = payload.get("index").and_then(Value::as_u64).unwrap_or(0);
                let row = self.ensure_activity_row(run_id, index, None);
                if let Some(name) = payload.get("name").and_then(Value::as_str) {
                    row.name.push_str(name);
                }
                if let Some(arguments) = payload.get("arguments_delta").and_then(Value::as_str) {
                    row.argument_parts.push_str(arguments);
                    if let Ok(value) = serde_json::from_str(&row.argument_parts) {
                        row.arguments = value;
                    }
                }
            }
            _ => {}
        }
        self.scroll = 0;
    }

    pub fn ingest_durable(&mut self, event: Event, live: bool) {
        if !self.seen_events.insert(event.id.clone()) {
            return;
        }
        self.last_sequence = self.last_sequence.max(event.sequence);
        let run_id = event.run_id.clone().unwrap_or_default();
        match event.event_type.as_str() {
            "session.title.changed" => {
                self.session.title = Some(string(&event.payload, "title"));
            }
            "user.message" => {
                self.collapse_completed_thoughts();
                self.transcript.push(TranscriptItem::User {
                    content: string(&event.payload, "content"),
                    paste_spans: paste_spans(&event.payload),
                });
            }
            "run.started" => {
                self.active_run = Some(run_id);
                self.run_started_at.get_or_insert_with(Instant::now);
            }
            "model.requested" if live || self.active_run.as_deref() == Some(run_id.as_str()) => {
                self.ensure_thought(&run_id, true);
            }
            "assistant.reasoning" => {
                let index = self.ensure_thought(&run_id, false);
                if let TranscriptItem::Thought {
                    content,
                    duration_seconds,
                    live,
                    ..
                } = &mut self.transcript[index]
                {
                    *content = string(&event.payload, "content");
                    *duration_seconds = event
                        .payload
                        .get("duration_seconds")
                        .and_then(Value::as_f64)
                        .unwrap_or_default();
                    *live = false;
                }
            }
            "assistant.message" => {
                self.finish_live_thought(&run_id);
                let index = self.ensure_assistant(&run_id, false);
                if let TranscriptItem::Assistant { content, live, .. } = &mut self.transcript[index]
                {
                    *content = string(&event.payload, "content");
                    *live = false;
                }
            }
            "model.tool_call" => {
                self.finish_live_thought(&run_id);
                let index = event
                    .payload
                    .get("index")
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                let call_id = event
                    .payload
                    .get("tool_call_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let row = self.ensure_activity_row(&run_id, index, call_id);
                row.name = string(&event.payload, "name");
                row.arguments = event
                    .payload
                    .get("arguments")
                    .cloned()
                    .unwrap_or(Value::Null);
            }
            "tool.requested" | "policy.requested" | "policy.decided" | "approval.requested"
            | "approval.resolved" | "tool.started" | "tool.completed" | "tool.failed"
            | "tool.rejected" => {
                self.update_activity(&run_id, &event.event_type, &event.payload);
                if event.event_type == "approval.requested" {
                    self.modal = Some(Modal::Approval(approval_from(&event.payload)));
                    self.sheet = None;
                } else if event.event_type == "approval.resolved"
                    && matches!(self.modal, Some(Modal::Approval(_)))
                {
                    self.modal = None;
                }
            }
            "context.compiled" => {
                self.context_tokens = event
                    .payload
                    .get("estimated_input_tokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(self.context_tokens);
                self.context_window = event
                    .payload
                    .get("context_window_tokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(self.context_window);
            }
            "model.response.failed" | "run.failed" => {
                let message = string(&event.payload, "message");
                self.transcript.push(TranscriptItem::Status {
                    text: if message.is_empty() {
                        event.event_type.replace('.', " ")
                    } else {
                        message
                    },
                    error: true,
                });
                if live {
                    self.modal = Some(Modal::Error(
                        self.transcript
                            .last()
                            .and_then(|item| match item {
                                TranscriptItem::Status { text, .. } => Some(text.clone()),
                                _ => None,
                            })
                            .unwrap_or_default(),
                    ));
                }
                if event.event_type == "run.failed" {
                    self.active_run = None;
                    self.run_started_at = None;
                }
            }
            "run.completed" | "run.cancelled" => {
                if self.active_run.as_deref() == Some(run_id.as_str()) {
                    self.active_run = None;
                    self.run_started_at = None;
                }
                self.finish_run(&run_id, event.event_type == "run.cancelled");
            }
            _ => {}
        }
        self.scroll = 0;
    }

    pub fn toggle_thought(&mut self, index: usize) {
        if let Some(TranscriptItem::Thought { collapsed, .. }) = self.transcript.get_mut(index) {
            *collapsed = !*collapsed;
        }
    }

    pub fn focus_thought(&mut self, direction: i8) -> bool {
        let thoughts: Vec<usize> = self
            .transcript
            .iter()
            .enumerate()
            .filter_map(|(index, item)| {
                matches!(item, TranscriptItem::Thought { .. }).then_some(index)
            })
            .collect();
        if thoughts.is_empty() {
            return false;
        }
        let position = self
            .focused_thought
            .and_then(|current| thoughts.iter().position(|index| *index == current));
        let next = if direction < 0 {
            position.unwrap_or(thoughts.len()).saturating_sub(1)
        } else {
            (position.unwrap_or(0) + usize::from(position.is_some())).min(thoughts.len() - 1)
        };
        self.focused_thought = Some(thoughts[next]);
        true
    }

    fn ensure_thought(&mut self, run_id: &str, live: bool) -> usize {
        if let Some(index) = self.transcript.iter().rposition(
            |item| matches!(item, TranscriptItem::Thought { run_id: id, .. } if id == run_id),
        ) {
            return index;
        }
        self.collapse_completed_thoughts();
        self.transcript.push(TranscriptItem::Thought {
            run_id: run_id.to_owned(),
            content: String::new(),
            duration_seconds: 0.0,
            live,
            collapsed: false,
        });
        self.transcript.len() - 1
    }

    fn ensure_assistant(&mut self, run_id: &str, live: bool) -> usize {
        if let Some(index) = self.transcript.iter().rposition(
            |item| matches!(item, TranscriptItem::Assistant { run_id: id, .. } if id == run_id),
        ) {
            return index;
        }
        self.transcript.push(TranscriptItem::Assistant {
            run_id: run_id.to_owned(),
            content: String::new(),
            live,
        });
        self.transcript.len() - 1
    }

    fn ensure_activity(&mut self, run_id: &str) -> usize {
        if let Some(index) = self.transcript.iter().rposition(
            |item| matches!(item, TranscriptItem::Activity { run_id: id, .. } if id == run_id),
        ) {
            return index;
        }
        self.transcript.push(TranscriptItem::Activity {
            run_id: run_id.to_owned(),
            rows: Vec::new(),
        });
        self.transcript.len() - 1
    }

    fn ensure_activity_row(
        &mut self,
        run_id: &str,
        index: u64,
        call_id: Option<String>,
    ) -> &mut ActivityRow {
        let activity_index = self.ensure_activity(run_id);
        let TranscriptItem::Activity { rows, .. } = &mut self.transcript[activity_index] else {
            unreachable!();
        };
        let row_index = rows
            .iter()
            .position(|row| {
                call_id
                    .as_ref()
                    .is_some_and(|id| row.tool_call_id.as_ref() == Some(id))
                    || row.index == index
            })
            .unwrap_or_else(|| {
                rows.push(ActivityRow::new(index));
                rows.len() - 1
            });
        if call_id.is_some() {
            rows[row_index].tool_call_id = call_id;
        }
        &mut rows[row_index]
    }

    fn update_activity(&mut self, run_id: &str, event_type: &str, payload: &Value) {
        let call_id = payload
            .get("tool_call_id")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let index = payload
            .get("index")
            .and_then(Value::as_u64)
            .unwrap_or(u64::MAX);
        let row = self.ensure_activity_row(run_id, index, call_id);
        let name = string(payload, "name");
        if !name.is_empty() {
            row.name = name;
        }
        if let Some(arguments) = payload.get("arguments") {
            row.arguments = arguments.clone();
        }
        row.phase = match event_type {
            "tool.requested" | "policy.requested" => ActivityPhase::Checking,
            "policy.decided" => match payload.get("decision").and_then(Value::as_str) {
                Some("deny") => ActivityPhase::Rejected,
                _ => ActivityPhase::Checking,
            },
            "approval.requested" => ActivityPhase::Approval,
            "approval.resolved" => match payload.get("decision").and_then(Value::as_str) {
                Some("denied" | "cancelled") => ActivityPhase::Rejected,
                _ => ActivityPhase::Checking,
            },
            "tool.started" => ActivityPhase::Running,
            "tool.completed" => ActivityPhase::Completed,
            "tool.failed" => ActivityPhase::Failed,
            "tool.rejected" => ActivityPhase::Rejected,
            _ => row.phase,
        };
        row.summary = string(payload, "summary");
        row.duration_seconds = payload
            .get("duration_seconds")
            .and_then(Value::as_f64)
            .unwrap_or(row.duration_seconds);
    }

    fn collapse_completed_thoughts(&mut self) {
        for item in &mut self.transcript {
            if let TranscriptItem::Thought {
                live, collapsed, ..
            } = item
                && !*live
            {
                *collapsed = true;
            }
        }
    }

    fn finish_live_thought(&mut self, run_id: &str) {
        for item in self.transcript.iter_mut().rev() {
            if let TranscriptItem::Thought {
                run_id: id,
                live,
                collapsed,
                ..
            } = item
                && id == run_id
            {
                *live = false;
                *collapsed = true;
                break;
            }
        }
    }

    fn finish_run(&mut self, run_id: &str, cancelled: bool) {
        for item in &mut self.transcript {
            match item {
                TranscriptItem::Thought {
                    run_id: id, live, ..
                }
                | TranscriptItem::Assistant {
                    run_id: id, live, ..
                } if id == run_id => *live = false,
                TranscriptItem::Activity { run_id: id, rows } if id == run_id => {
                    for row in rows {
                        if !row.phase.terminal() {
                            row.phase = if cancelled {
                                ActivityPhase::Cancelled
                            } else {
                                ActivityPhase::Failed
                            };
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

fn option(label: &str, detail: &str, action: MenuAction) -> MenuOption {
    MenuOption {
        label: label.to_owned(),
        detail: detail.to_owned(),
        action,
    }
}

fn string(payload: &Value, key: &str) -> String {
    payload
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn paste_spans(payload: &Value) -> Vec<PasteSpan> {
    payload
        .get("paste_spans")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|value| {
            Some(PasteSpan {
                start_byte: usize::try_from(value.get("start_byte")?.as_u64()?).ok()?,
                end_byte: usize::try_from(value.get("end_byte")?.as_u64()?).ok()?,
                line_count: usize::try_from(value.get("line_count")?.as_u64()?).ok()?,
                byte_count: usize::try_from(value.get("byte_count")?.as_u64()?).ok()?,
            })
        })
        .collect()
}

fn approval_from(payload: &Value) -> ApprovalModal {
    ApprovalModal {
        approval_id: string(payload, "approval_id"),
        request_hash: string(payload, "request_hash"),
        name: string(payload, "name"),
        reason: string(payload, "reason"),
        arguments: serde_json::to_string_pretty(payload.get("arguments").unwrap_or(&Value::Null))
            .unwrap_or_else(|_| "{}".to_owned()),
        allow_session: payload
            .get("allow_session")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        selected: 0,
    }
}

fn category_for_tool(name: &str) -> ActivityCategory {
    match name {
        "read_file" | "list_dir" => ActivityCategory::Explore,
        "write_file" | "edit_file" | "session_title_set" => ActivityCategory::Change,
        "shell" | "skill_run" => ActivityCategory::Run,
        "spawn_agent" => ActivityCategory::Delegate,
        "skill_load" | "skill_author" | "skill_catalog" | "skill_control" => {
            ActivityCategory::Skills
        }
        "memory_search" | "memory_add" | "memory_edit" | "memory_forget" => {
            ActivityCategory::Memory
        }
        "scar_list" | "scar_record" | "scar_control" => ActivityCategory::Scars,
        value if value.contains('.') => ActivityCategory::Plugin,
        _ => ActivityCategory::Run,
    }
}

fn display_path(value: &str) -> String {
    if value == "~" || value.starts_with("~/") {
        return value.to_owned();
    }
    std::env::var("HOME")
        .ok()
        .and_then(|home| value.strip_prefix(&home).map(|suffix| format!("~{suffix}")))
        .unwrap_or_else(|| value.to_owned())
}

#[cfg(test)]
mod tests {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use serde_json::json;

    use super::{App, Composer, ComposerUnit, TranscriptItem};
    use crate::api::{Event, Session};

    #[test]
    fn large_pastes_are_atomic_and_preserve_exact_message_bytes() {
        let mut composer = Composer::default();
        composer.insert_text("before ");
        composer.insert_paste("one\ntwo\nthree\nfour".to_owned());
        composer.insert_text(" after");
        let (message, spans) = composer.message();
        assert_eq!(message, "before one\ntwo\nthree\nfour after");
        assert_eq!(spans.len(), 1);
        assert_eq!(
            &message.as_bytes()[spans[0].start_byte..spans[0].end_byte],
            b"one\ntwo\nthree\nfour"
        );
        composer.cursor = 8;
        composer.backspace();
        assert!(
            !composer
                .units
                .iter()
                .any(|unit| matches!(unit, ComposerUnit::Paste(_)))
        );
    }

    #[test]
    fn short_pastes_remain_editable_graphemes() {
        let mut composer = Composer::default();
        composer.insert_paste("é界".to_owned());
        assert_eq!(composer.units.len(), 2);
        composer.backspace();
        assert_eq!(composer.text(), "é");
    }

    #[test]
    fn adjacent_paste_removal_works_on_either_side_of_capsule() {
        let mut composer = Composer::default();
        composer.insert_paste("one\ntwo\nthree\nfour".to_owned());
        assert!(composer.remove_adjacent_paste());
        assert!(composer.is_empty());

        composer.insert_paste("one\ntwo\nthree\nfour".to_owned());
        composer.cursor = 0;
        assert!(composer.remove_adjacent_paste());
        assert!(composer.is_empty());
    }

    #[test]
    fn alt_and_shift_enter_insert_composer_newlines() {
        let mut app = App::new(session(), Vec::new(), true);
        assert!(app.handle_composer_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::ALT)));
        assert!(app.handle_composer_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::SHIFT)));
        assert_eq!(app.composer.text(), "\n\n");
    }

    #[test]
    fn durable_title_event_refreshes_session_presentation() {
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_durable(
            event(
                1,
                "session.title.changed",
                "run-title",
                json!({"title": "Palette polish"}),
            ),
            true,
        );
        assert_eq!(app.session.title.as_deref(), Some("Palette polish"));
    }

    #[test]
    fn durable_replay_recovers_active_work_and_collapses_finished_thought() {
        let session = session();
        let run_id = "run-1";
        let events = vec![
            event(1, "run.started", run_id, json!({})),
            event(2, "model.requested", run_id, json!({})),
        ];
        let mut app = App::new(session, events, true);
        assert_eq!(app.active_run.as_deref(), Some(run_id));
        assert!(matches!(
            app.transcript.last(),
            Some(TranscriptItem::Thought { live: true, .. })
        ));

        app.ingest_durable(
            event(
                3,
                "assistant.reasoning",
                run_id,
                json!({"content": "considered", "status": "completed", "duration_seconds": 12.0}),
            ),
            true,
        );
        app.ingest_durable(
            event(
                4,
                "assistant.message",
                run_id,
                json!({"content": "done", "status": "completed"}),
            ),
            true,
        );
        app.ingest_durable(event(5, "run.completed", run_id, json!({})), true);
        assert!(app.active_run.is_none());
        assert!(app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Thought {
                duration_seconds,
                collapsed: true,
                live: false,
                ..
            } if *duration_seconds == 12.0
        )));
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

    fn event(sequence: u64, event_type: &str, run_id: &str, payload: serde_json::Value) -> Event {
        Event {
            id: format!("event-{sequence}"),
            sequence,
            session_id: "session-1".to_owned(),
            run_id: Some(run_id.to_owned()),
            agent_id: Some("default".to_owned()),
            event_type: event_type.to_owned(),
            schema_version: 1,
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            causation_id: None,
            correlation_id: Some(run_id.to_owned()),
            payload,
            blob_hash: None,
            payload_hash: "hash".to_owned(),
            redaction_state: "none".to_owned(),
        }
    }
}
