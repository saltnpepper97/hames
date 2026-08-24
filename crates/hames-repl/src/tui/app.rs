use std::collections::HashSet;
use std::time::{Duration, Instant};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;
use tachyonfx::Effect;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use crate::api::{Event, MemoryRecord, PasteSpan, QueueState, QueuedMessage, Scar, Session};

pub const LARGE_PASTE_LINES: usize = 4;
pub const LARGE_PASTE_BYTES: usize = 400;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ComposerUnit {
    Text(String),
    Paste(String),
}

#[derive(Clone, Debug, Default)]
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

    pub fn load_message(&mut self, content: &str, paste_spans: &[PasteSpan]) {
        self.clear();
        let mut offset = 0;
        for span in paste_spans {
            if span.start_byte < offset
                || span.end_byte > content.len()
                || !content.is_char_boundary(span.start_byte)
                || !content.is_char_boundary(span.end_byte)
            {
                continue;
            }
            self.insert_text(&content[offset..span.start_byte]);
            self.insert_paste(content[span.start_byte..span.end_byte].to_owned());
            offset = span.end_byte;
        }
        self.insert_text(&content[offset..]);
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
    pub content: String,
    pub structured_data: Value,
    pub truncated: bool,
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
            content: String::new(),
            structured_data: Value::Null,
            truncated: false,
            duration_seconds: 0.0,
        }
    }

    pub fn category(&self) -> ActivityCategory {
        category_for_tool(&self.name)
    }

    pub fn target(&self) -> String {
        if let Some(memory_id) = self.arguments.get("memory_id").and_then(Value::as_str) {
            return format!("memory {}", memory_id.get(..8).unwrap_or(memory_id));
        }
        if let Some(scar_id) = self.arguments.get("scar_id").and_then(Value::as_str) {
            return format!("scar {}", scar_id.get(..8).unwrap_or(scar_id));
        }
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
        if self.name == "memory_forget" {
            return match self.phase {
                ActivityPhase::Preparing => "Preparing",
                ActivityPhase::Checking => "Checking",
                ActivityPhase::Approval => "Awaiting permission",
                ActivityPhase::Running => "Forgetting",
                ActivityPhase::Completed => "Forgot",
                ActivityPhase::Failed => "Failed",
                ActivityPhase::Rejected => "Rejected",
                ActivityPhase::Cancelled => "Cancelled",
            };
        }
        if self.name == "memory_add" {
            return match self.phase {
                ActivityPhase::Preparing => "Preparing",
                ActivityPhase::Checking => "Checking",
                ActivityPhase::Approval => "Awaiting permission",
                ActivityPhase::Running => "Remembering",
                ActivityPhase::Completed => "Remembered",
                ActivityPhase::Failed => "Failed",
                ActivityPhase::Rejected => "Rejected",
                ActivityPhase::Cancelled => "Cancelled",
            };
        }
        if self.name == "memory_edit" {
            return match self.phase {
                ActivityPhase::Preparing => "Preparing",
                ActivityPhase::Checking => "Checking",
                ActivityPhase::Approval => "Awaiting permission",
                ActivityPhase::Running => "Updating",
                ActivityPhase::Completed => "Updated",
                ActivityPhase::Failed => "Failed",
                ActivityPhase::Rejected => "Rejected",
                ActivityPhase::Cancelled => "Cancelled",
            };
        }
        if self.name == "scar_control" {
            let action = self
                .arguments
                .get("action")
                .and_then(Value::as_str)
                .unwrap_or_default();
            return match (self.phase, action) {
                (ActivityPhase::Running, "delete") => "Deleting",
                (ActivityPhase::Completed, "delete") => "Deleted",
                (ActivityPhase::Running, "dismiss") => "Dismissing",
                (ActivityPhase::Completed, "dismiss") => "Dismissed",
                (ActivityPhase::Running, "open") => "Opening",
                (ActivityPhase::Completed, "open") => "Opened",
                (ActivityPhase::Preparing, _) => "Preparing",
                (ActivityPhase::Checking, _) => "Checking",
                (ActivityPhase::Approval, _) => "Awaiting permission",
                (ActivityPhase::Running, _) => "Updating",
                (ActivityPhase::Completed, _) => "Updated",
                (ActivityPhase::Failed, _) => "Failed",
                (ActivityPhase::Rejected, _) => "Rejected",
                (ActivityPhase::Cancelled, _) => "Cancelled",
            };
        }
        if self.name == "scar_record" {
            return match self.phase {
                ActivityPhase::Running => "Recording",
                ActivityPhase::Completed => "Recorded",
                ActivityPhase::Preparing => "Preparing",
                ActivityPhase::Checking => "Checking",
                ActivityPhase::Approval => "Awaiting permission",
                ActivityPhase::Failed => "Failed",
                ActivityPhase::Rejected => "Rejected",
                ActivityPhase::Cancelled => "Cancelled",
            };
        }
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
    },
    Thought {
        run_id: String,
        content: String,
        duration_seconds: f64,
        interrupted: bool,
        live: bool,
        collapsed: bool,
    },
    Assistant {
        run_id: String,
        content: String,
        live: bool,
        durable: bool,
    },
    Activity {
        run_id: String,
        rows: Vec<ActivityRow>,
        collapsed: bool,
    },
    Dream {
        job_id: String,
        label: String,
        phase: DreamPhase,
        detail: String,
    },
    Worked {
        duration_seconds: f64,
    },
    Status {
        text: String,
        error: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DreamPhase {
    Queued,
    Running,
    Paused,
    Completed,
    Failed,
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
    pub detail_scroll: usize,
}

#[derive(Clone, Debug)]
pub struct MemoryBrowser {
    pub records: Vec<MemoryRecord>,
    pub selected: usize,
    pub detail_scroll: usize,
    pub pending_delete: Option<usize>,
}

#[derive(Clone, Debug)]
pub struct ScarBrowser {
    pub records: Vec<Scar>,
    pub selected: usize,
    pub detail_scroll: usize,
    pub pending_delete: Option<usize>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScarEditField {
    Title,
    Severity,
    Description,
    ExpectedBehavior,
}

impl ScarEditField {
    pub fn next(self) -> Self {
        match self {
            Self::Title => Self::Severity,
            Self::Severity => Self::Description,
            Self::Description => Self::ExpectedBehavior,
            Self::ExpectedBehavior => Self::Title,
        }
    }

    pub fn previous(self) -> Self {
        match self {
            Self::Title => Self::ExpectedBehavior,
            Self::Severity => Self::Title,
            Self::Description => Self::Severity,
            Self::ExpectedBehavior => Self::Description,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ScarEditor {
    pub browser: ScarBrowser,
    pub scar_id: String,
    pub field: ScarEditField,
    pub title: Composer,
    pub severity: String,
    pub description: Composer,
    pub expected_behavior: Composer,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgentEditorPage {
    Identity,
    Access,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgentEditField {
    Name,
    Slug,
    Instructions,
}

impl AgentEditField {
    pub fn next(self) -> Self {
        match self {
            Self::Name => Self::Slug,
            Self::Slug => Self::Instructions,
            Self::Instructions => Self::Name,
        }
    }

    pub fn previous(self) -> Self {
        match self {
            Self::Name => Self::Instructions,
            Self::Slug => Self::Name,
            Self::Instructions => Self::Slug,
        }
    }
}

#[derive(Clone, Debug)]
pub struct AgentChoice {
    pub id: String,
    pub label: String,
    pub detail: String,
    pub selected: bool,
}

#[derive(Clone, Debug)]
pub struct AgentEditor {
    pub page: AgentEditorPage,
    pub field: AgentEditField,
    pub name: Composer,
    pub slug: Composer,
    pub instructions: Composer,
    pub slug_manual: bool,
    pub tools: Vec<AgentChoice>,
    pub skills: Vec<AgentChoice>,
    pub access_selected: usize,
}

impl AgentEditor {
    pub fn new(tools: Vec<String>, skills: Vec<(String, String, String)>) -> Self {
        Self {
            page: AgentEditorPage::Identity,
            field: AgentEditField::Name,
            name: Composer::default(),
            slug: Composer::default(),
            instructions: Composer::default(),
            slug_manual: false,
            tools: tools
                .into_iter()
                .map(|tool| AgentChoice {
                    label: tool.clone(),
                    id: tool,
                    detail: String::new(),
                    selected: true,
                })
                .collect(),
            skills: skills
                .into_iter()
                .map(|(id, label, detail)| AgentChoice {
                    id,
                    label,
                    detail,
                    selected: true,
                })
                .collect(),
            access_selected: 0,
        }
    }

    pub fn active_text_mut(&mut self) -> &mut Composer {
        match self.field {
            AgentEditField::Name => &mut self.name,
            AgentEditField::Slug => &mut self.slug,
            AgentEditField::Instructions => &mut self.instructions,
        }
    }

    pub fn sync_slug(&mut self) {
        if self.slug_manual {
            return;
        }
        self.slug.clear();
        self.slug.insert_text(&agent_slug(&self.name.text()));
    }

    pub fn access_len(&self) -> usize {
        self.tools.len() + self.skills.len()
    }

    pub fn move_access(&mut self, backwards: bool) {
        let len = self.access_len();
        if len == 0 {
            self.access_selected = 0;
        } else if backwards {
            self.access_selected = if self.access_selected == 0 {
                len - 1
            } else {
                self.access_selected - 1
            };
        } else {
            self.access_selected = (self.access_selected + 1) % len;
        }
    }

    pub fn toggle_access(&mut self) {
        let index = self.access_selected;
        if index < self.tools.len() {
            self.tools[index].selected = !self.tools[index].selected;
        } else if let Some(skill) = self.skills.get_mut(index.saturating_sub(self.tools.len())) {
            skill.selected = !skill.selected;
        }
    }
}

fn agent_slug(value: &str) -> String {
    let mut slug = String::new();
    let mut separator = false;
    for character in value.to_lowercase().chars() {
        if character.is_ascii_alphanumeric() {
            if separator && !slug.is_empty() && slug.len() < 63 {
                slug.push('-');
            }
            separator = false;
            if slug.len() < 63 {
                slug.push(character);
            }
        } else {
            separator = true;
        }
    }
    while slug.ends_with('-') {
        slug.pop();
    }
    slug
}

impl ScarEditor {
    pub fn new(browser: ScarBrowser) -> Option<Self> {
        let scar = browser.records.get(browser.selected)?;
        let mut title = Composer::default();
        title.insert_text(&scar.title);
        let mut description = Composer::default();
        description.insert_text(&scar.description);
        let mut expected_behavior = Composer::default();
        expected_behavior.insert_text(&scar.expected_behavior);
        Some(Self {
            scar_id: scar.id.clone(),
            severity: scar.severity.clone(),
            browser,
            field: ScarEditField::Title,
            title,
            description,
            expected_behavior,
        })
    }

    pub fn active_text_mut(&mut self) -> Option<&mut Composer> {
        match self.field {
            ScarEditField::Title => Some(&mut self.title),
            ScarEditField::Description => Some(&mut self.description),
            ScarEditField::ExpectedBehavior => Some(&mut self.expected_behavior),
            ScarEditField::Severity => None,
        }
    }

    pub fn cycle_severity(&mut self, backwards: bool) {
        self.severity = match (self.severity.as_str(), backwards) {
            ("low", false) | ("high", true) => "medium",
            ("medium", false) => "high",
            ("medium", true) => "low",
            _ if backwards => "medium",
            _ => "low",
        }
        .to_owned();
    }
}

#[derive(Clone, Debug)]
pub enum Modal {
    Trust,
    Approval(ApprovalModal),
    Help,
    Session,
    Memory(MemoryBrowser),
    Scars(ScarBrowser),
    ScarEdit(ScarEditor),
    AgentEdit(AgentEditor),
    PastePreview(String),
    Error(String),
    Info { title: String, lines: Vec<String> },
}

#[derive(Clone, Debug)]
pub enum MenuAction {
    NewSession,
    ClearSession,
    OpenSessions,
    OpenQueue,
    ClearQueue,
    EditQueued(String),
    ForkSession,
    OpenModels,
    OpenEfforts,
    OpenAgents,
    CreateAgent,
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
    Queue,
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

    pub fn config_value(self) -> &'static str {
        match self {
            Self::Hames => "hames",
            Self::Terminal => "terminal",
        }
    }

    pub fn from_config(value: &str) -> Option<Self> {
        match value {
            "hames" => Some(Self::Hames),
            "terminal" => Some(Self::Terminal),
            _ => None,
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
    pub anchor_y: u16,
    pub anchor_top: usize,
}

#[derive(Clone, Debug)]
pub struct Sheet {
    pub kind: SheetKind,
    pub title: String,
    pub options: Vec<MenuOption>,
    pub selected: usize,
    pub pending_delete: Option<usize>,
}

#[derive(Clone, Debug)]
pub enum HitAction {
    ToggleThought(usize),
    ToggleActivity(usize),
    SelectSheet(usize),
    SelectMemory(usize),
    SelectScar(usize),
    Approval(usize),
    QueuedMessage(String),
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TranscriptPoint {
    pub row: usize,
    pub column: usize,
}

#[derive(Clone, Debug, Default)]
pub struct TranscriptViewport {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
    pub lines: Vec<String>,
}

impl TranscriptViewport {
    pub fn point(&self, x: u16, y: u16) -> Option<TranscriptPoint> {
        if self.lines.is_empty()
            || x < self.x
            || x >= self.x.saturating_add(self.width)
            || y < self.y
            || y >= self.y.saturating_add(self.height)
        {
            return None;
        }
        Some(TranscriptPoint {
            row: usize::from(y - self.y).min(self.lines.len().saturating_sub(1)),
            column: usize::from(x - self.x),
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TranscriptSelection {
    pub anchor: TranscriptPoint,
    pub head: TranscriptPoint,
}

impl TranscriptSelection {
    pub fn bounds(self) -> (TranscriptPoint, TranscriptPoint) {
        if (self.anchor.row, self.anchor.column) <= (self.head.row, self.head.column) {
            (self.anchor, self.head)
        } else {
            (self.head, self.anchor)
        }
    }
}

#[derive(Clone, Debug)]
pub struct CopyNotice {
    pub text: String,
    pub expires_at: Instant,
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
    pub agent_name: String,
    pub transcript: Vec<TranscriptItem>,
    pub composer: Composer,
    pub queued_messages: Vec<QueuedMessage>,
    pub queue_paused: bool,
    pub message_history: Vec<String>,
    pub history_index: Option<usize>,
    pub history_draft: Option<String>,
    pub active_run: Option<String>,
    pub run_started_at: Option<Instant>,
    pub trusted: bool,
    pub modal: Option<Modal>,
    pub sheet: Option<Sheet>,
    pub notice: Option<String>,
    pub scroll: usize,
    pub composer_scroll: Option<usize>,
    pub scroll_drag: Option<ScrollDrag>,
    pub transcript_viewport: TranscriptViewport,
    pub transcript_selection: Option<TranscriptSelection>,
    pub selecting_transcript: bool,
    pub modal_viewport: TranscriptViewport,
    pub modal_selection: Option<TranscriptSelection>,
    pub selecting_modal: bool,
    pub pending_thought_toggle: Option<usize>,
    pub pending_activity_toggle: Option<usize>,
    pub copy_notice: Option<CopyNotice>,
    pub last_sequence: u64,
    pub seen_events: HashSet<String>,
    pub context_tokens: u64,
    pub context_window: u64,
    pub hits: Vec<HitRegion>,
    pub should_quit: bool,
    pub focused_thought: Option<usize>,
    pub theme: ThemeKind,
    pub reopen_sessions_after_switch: bool,
    pub activity_bar_effect: Option<Effect>,
    pub transcript_sheen_effect: Option<Effect>,
    pub fx_last_frame: Instant,
}

impl App {
    pub fn new(session: Session, events: Vec<Event>, trusted: bool) -> Self {
        let context_window = session.context_window_tokens;
        let agent_name = if session.agent_id == "default" {
            "Hames".to_owned()
        } else {
            session
                .agent_id
                .split('-')
                .filter(|part| !part.is_empty())
                .map(|part| {
                    let mut characters = part.chars();
                    characters
                        .next()
                        .map(|first| first.to_uppercase().collect::<String>() + characters.as_str())
                        .unwrap_or_default()
                })
                .collect::<Vec<_>>()
                .join(" ")
        };
        let mut app = Self {
            session,
            agent_name,
            transcript: Vec::new(),
            composer: Composer::default(),
            queued_messages: Vec::new(),
            queue_paused: false,
            message_history: Vec::new(),
            history_index: None,
            history_draft: None,
            active_run: None,
            run_started_at: None,
            trusted,
            modal: (!trusted).then_some(Modal::Trust),
            sheet: None,
            notice: None,
            scroll: 0,
            composer_scroll: None,
            scroll_drag: None,
            transcript_viewport: TranscriptViewport::default(),
            transcript_selection: None,
            selecting_transcript: false,
            modal_viewport: TranscriptViewport::default(),
            modal_selection: None,
            selecting_modal: false,
            pending_thought_toggle: None,
            pending_activity_toggle: None,
            copy_notice: None,
            last_sequence: 0,
            seen_events: HashSet::new(),
            context_tokens: 0,
            context_window,
            hits: Vec::new(),
            should_quit: false,
            focused_thought: None,
            theme: ThemeKind::Hames,
            reopen_sessions_after_switch: false,
            activity_bar_effect: None,
            transcript_sheen_effect: None,
            fx_last_frame: Instant::now(),
        };
        for event in events {
            app.ingest_durable(event, false);
        }
        app
    }

    pub fn animating(&self) -> bool {
        self.active_run.is_some()
            || self.copy_notice().is_some()
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

    pub fn set_queue(&mut self, state: QueueState) {
        self.queue_paused = state.paused;
        self.queued_messages = state.items;
    }

    pub fn load_queued_message(&mut self, item: QueuedMessage) {
        self.composer.load_message(&item.content, &item.paste_spans);
        self.history_index = None;
        self.history_draft = None;
        self.notice = Some("Queued message ready to edit".to_owned());
    }

    pub fn history_previous(&mut self) -> bool {
        if self.message_history.is_empty() {
            return false;
        }
        if self.history_index.is_none() {
            self.history_draft = Some(self.composer.text());
        }
        let next = self
            .history_index
            .map_or(self.message_history.len() - 1, |index| {
                index.saturating_sub(1)
            });
        self.history_index = Some(next);
        self.composer
            .load_message(&self.message_history[next].clone(), &[]);
        true
    }

    pub fn history_next(&mut self) -> bool {
        let Some(index) = self.history_index else {
            return false;
        };
        if index + 1 < self.message_history.len() {
            self.history_index = Some(index + 1);
            self.composer
                .load_message(&self.message_history[index + 1].clone(), &[]);
        } else {
            self.history_index = None;
            let draft = self.history_draft.take().unwrap_or_default();
            self.composer.load_message(&draft, &[]);
        }
        true
    }

    pub fn take_effect_delta(&mut self) -> Duration {
        let now = Instant::now();
        let elapsed = now.saturating_duration_since(self.fx_last_frame);
        self.fx_last_frame = now;
        elapsed.min(Duration::from_millis(250))
    }

    pub fn conversation_is_empty(&self) -> bool {
        !self
            .transcript
            .iter()
            .any(|item| matches!(item, TranscriptItem::User { .. }))
    }

    pub fn copy_notice(&self) -> Option<&str> {
        self.copy_notice
            .as_ref()
            .filter(|notice| notice.expires_at > Instant::now())
            .map(|notice| notice.text.as_str())
    }

    pub fn show_copy_notice(&mut self, character_count: usize) {
        self.copy_notice = Some(CopyNotice {
            text: format!("Copied to clipboard · {character_count} characters"),
            expires_at: Instant::now() + Duration::from_secs(2),
        });
    }

    pub fn begin_transcript_selection(&mut self, point: TranscriptPoint) {
        self.transcript_selection = Some(TranscriptSelection {
            anchor: point,
            head: point,
        });
        self.selecting_transcript = true;
    }

    pub fn update_transcript_selection(&mut self, point: TranscriptPoint) {
        if let Some(selection) = &mut self.transcript_selection
            && self.selecting_transcript
        {
            selection.head = point;
            if selection.head != selection.anchor {
                self.pending_thought_toggle = None;
                self.pending_activity_toggle = None;
            }
        }
    }

    pub fn finish_transcript_selection(&mut self) -> Option<String> {
        self.selecting_transcript = false;
        let selection = self.transcript_selection?;
        if selection.anchor == selection.head {
            self.transcript_selection = None;
            return None;
        }
        selected_transcript_text(&self.transcript_viewport.lines, selection)
    }

    pub fn clear_transcript_selection(&mut self) {
        self.transcript_selection = None;
        self.selecting_transcript = false;
        self.pending_thought_toggle = None;
        self.pending_activity_toggle = None;
    }

    pub fn begin_modal_selection(&mut self, point: TranscriptPoint) {
        self.modal_selection = Some(TranscriptSelection {
            anchor: point,
            head: point,
        });
        self.selecting_modal = true;
    }

    pub fn update_modal_selection(&mut self, point: TranscriptPoint) {
        if let Some(selection) = &mut self.modal_selection
            && self.selecting_modal
        {
            selection.head = point;
        }
    }

    pub fn finish_modal_selection(&mut self) -> Option<String> {
        self.selecting_modal = false;
        let selection = self.modal_selection?;
        if selection.anchor == selection.head {
            self.modal_selection = None;
            return None;
        }
        selected_transcript_text(&self.modal_viewport.lines, selection)
    }

    pub fn clear_modal_selection(&mut self) {
        self.modal_selection = None;
        self.selecting_modal = false;
    }

    pub fn modal_selection_range(&self, row: usize) -> Option<(usize, usize)> {
        selection_range(&self.modal_viewport.lines, self.modal_selection, row)
    }

    pub fn transcript_selection_range(&self, row: usize) -> Option<(usize, usize)> {
        selection_range(
            &self.transcript_viewport.lines,
            self.transcript_selection,
            row,
        )
    }

    pub fn command_options(&self) -> Vec<MenuOption> {
        vec![
            option(
                "/new",
                "start fresh and keep this conversation",
                MenuAction::NewSession,
            ),
            option(
                "/clear",
                "discard this conversation and start fresh",
                MenuAction::ClearSession,
            ),
            option("/sessions", "resume recent work", MenuAction::OpenSessions),
            option("/queue", "inspect pending turns", MenuAction::OpenQueue),
            option("/fork", "branch this session", MenuAction::ForkSession),
            option(
                "/model",
                "provider, model, and reasoning",
                MenuAction::OpenModels,
            ),
            option(
                "/effort",
                "change reasoning without changing model",
                MenuAction::OpenEfforts,
            ),
            option(
                "/agent",
                "select, create, or delete agents",
                MenuAction::OpenAgents,
            ),
            option("/mode", "manual, auto, or plan", MenuAction::OpenModes),
            option(
                "/themes",
                "Hames or terminal colors",
                MenuAction::OpenThemes,
            ),
            option(
                "/status",
                "session identity and continuity",
                MenuAction::ShowSession,
            ),
            option("/project", "workspace authority", MenuAction::Trust),
            option(
                "/gateway",
                "gateway health and active work",
                MenuAction::Status,
            ),
            option("/usage", "tokens and model requests", MenuAction::Usage),
            option("/events", "recent durable history", MenuAction::Events),
            option("/inspect", "latest run timeline", MenuAction::Inspect),
            option("/context", "latest compiled context", MenuAction::Context),
            option("/memory", "active durable memories", MenuAction::Memory),
            option("/skills", "active procedural catalog", MenuAction::Skills),
            option("/scars", "corrections and repair state", MenuAction::Scars),
            option("/plugins", "installed capabilities", MenuAction::Plugins),
            option("/help", "keyboard and mouse guide", MenuAction::Help),
            option("/cancel", "stop current work", MenuAction::CancelRun),
            option("/quit", "leave the gateway running", MenuAction::Quit),
        ]
    }

    pub fn open_commands(&mut self) {
        self.sheet = Some(Sheet {
            kind: SheetKind::Commands,
            title: "Command palette".to_owned(),
            options: self.command_options(),
            selected: 0,
            pending_delete: None,
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
            pending_delete: None,
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
            pending_delete: None,
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
            pending_delete: None,
        });
    }

    pub fn handle_composer_key(&mut self, key: KeyEvent) -> bool {
        self.composer_scroll = None;
        match key.code {
            KeyCode::Backspace => {
                self.composer.backspace();
            }
            KeyCode::Delete => {
                self.composer.delete();
            }
            KeyCode::Left => {
                self.composer.move_left();
            }
            KeyCode::Right => {
                self.composer.move_right();
            }
            KeyCode::Home => {
                self.composer.move_home();
            }
            KeyCode::End => {
                self.composer.move_end();
            }
            KeyCode::Enter
                if key
                    .modifiers
                    .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT) =>
            {
                self.composer.insert_text("\n");
            }
            KeyCode::Char('j') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.composer.insert_text("\n");
            }
            KeyCode::Char(value)
                if !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                self.composer.insert_text(&value.to_string());
            }
            _ => return false,
        };
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
                if text.is_empty() {
                    return;
                }
                let index = self.ensure_assistant(run_id, true);
                if let TranscriptItem::Assistant { content, .. } = &mut self.transcript[index] {
                    content.push_str(text);
                }
            }
            "response.tool_call_delta" => {
                self.finish_live_thought(run_id);
                self.finish_live_assistant(run_id);
                let name = payload
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let arguments = payload
                    .get("arguments_delta")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if name.is_empty() && arguments.is_empty() {
                    return;
                }
                let index = payload.get("index").and_then(Value::as_u64).unwrap_or(0);
                let row = self.ensure_activity_row(run_id, index, None);
                if !name.is_empty() {
                    row.name.push_str(name);
                }
                if !arguments.is_empty() {
                    row.argument_parts.push_str(arguments);
                    if let Ok(value) = serde_json::from_str(&row.argument_parts) {
                        row.arguments = value;
                    }
                }
            }
            _ => {}
        }
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
                let user_content = string(&event.payload, "content");
                self.message_history.push(user_content.clone());
                self.transcript.push(TranscriptItem::User {
                    content: user_content,
                });
                if live {
                    self.scroll = 0;
                }
            }
            "run.started" => {
                self.active_run = Some(run_id);
                self.run_started_at.get_or_insert_with(Instant::now);
            }
            "queue.enqueued" => {
                let queue_id = string(&event.payload, "queue_id");
                if !self.queued_messages.iter().any(|item| item.id == queue_id) {
                    let paste_spans = event
                        .payload
                        .get("paste_spans")
                        .cloned()
                        .and_then(|value| serde_json::from_value(value).ok())
                        .unwrap_or_default();
                    self.queued_messages.push(QueuedMessage {
                        id: queue_id,
                        session_id: self.session.id.clone(),
                        content: string(&event.payload, "content"),
                        remember: event
                            .payload
                            .get("remember")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        paste_spans,
                        created_at: event.created_at.clone(),
                        position: self.queued_messages.len() + 1,
                    });
                }
            }
            "queue.removed" | "queue.promoted" => {
                let queue_id = string(&event.payload, "queue_id");
                self.queued_messages.retain(|item| item.id != queue_id);
                for (index, item) in self.queued_messages.iter_mut().enumerate() {
                    item.position = index + 1;
                }
            }
            "queue.paused" | "queue.resumed" => {
                self.queue_paused = event
                    .payload
                    .get("paused")
                    .and_then(Value::as_bool)
                    .unwrap_or(event.event_type == "queue.paused");
            }
            "model.requested" if self.active_run.as_deref() == Some(run_id.as_str()) => {
                self.ensure_thought(&run_id, true);
            }
            "assistant.reasoning" => {
                let index = self.ensure_thought(&run_id, false);
                if let TranscriptItem::Thought {
                    content,
                    duration_seconds,
                    interrupted,
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
                    *interrupted =
                        event.payload.get("status").and_then(Value::as_str) == Some("interrupted");
                    *live = false;
                }
            }
            "assistant.message" => {
                self.finish_live_thought(&run_id);
                let index = self.ensure_assistant(&run_id, false);
                if let TranscriptItem::Assistant {
                    content,
                    live,
                    durable,
                    ..
                } = &mut self.transcript[index]
                {
                    *content = string(&event.payload, "content");
                    *live = false;
                    *durable = true;
                }
            }
            "model.tool_call" => {
                self.finish_live_thought(&run_id);
                self.finish_live_assistant(&run_id);
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
                if event.event_type == "model.response.failed"
                    && self.active_run.as_deref() != Some(run_id.as_str())
                {
                    return;
                }
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
                let cancelled = event.event_type == "run.cancelled";
                if self.active_run.as_deref() == Some(run_id.as_str()) {
                    self.active_run = None;
                    self.run_started_at = None;
                }
                self.finish_run(&run_id, cancelled);
                if cancelled {
                    self.transcript.push(TranscriptItem::Status {
                        text: "Turn interrupted".to_owned(),
                        error: false,
                    });
                } else if let Some(duration_seconds) =
                    event.payload.get("active_seconds").and_then(Value::as_f64)
                {
                    self.transcript
                        .push(TranscriptItem::Worked { duration_seconds });
                }
            }
            "memory.job.queued"
            | "memory.job.started"
            | "memory.job.paused"
            | "memory.job.completed"
            | "memory.job.failed" => {
                let kind = string(&event.payload, "kind");
                let automatic = kind != "explicit_capture";
                let label = if automatic {
                    "Memory consolidation"
                } else {
                    "Requested memory capture"
                };
                let phase = dream_phase(&event.event_type);
                let detail = dream_detail(label, phase, &event.payload);
                self.update_dream(
                    string(&event.payload, "job_id"),
                    label.to_owned(),
                    phase,
                    detail,
                );
            }
            "skill.job.queued"
            | "skill.job.started"
            | "skill.job.paused"
            | "skill.job.completed"
            | "skill.job.failed" => {
                let label = "Skill refinement";
                let phase = dream_phase(&event.event_type);
                let detail = dream_detail(label, phase, &event.payload);
                self.update_dream(
                    string(&event.payload, "job_id"),
                    label.to_owned(),
                    phase,
                    detail,
                );
            }
            _ => {}
        }
    }

    fn update_dream(&mut self, job_id: String, label: String, phase: DreamPhase, detail: String) {
        if let Some(TranscriptItem::Dream {
            label: current_label,
            phase: current_phase,
            detail: current_detail,
            ..
        }) =
            self.transcript.iter_mut().rev().find(
                |item| matches!(item, TranscriptItem::Dream { job_id: id, .. } if id == &job_id),
            )
        {
            *current_label = label;
            *current_phase = phase;
            *current_detail = detail;
            return;
        }
        self.transcript.push(TranscriptItem::Dream {
            job_id,
            label,
            phase,
            detail,
        });
    }

    pub fn toggle_thought(&mut self, index: usize) {
        if let Some(TranscriptItem::Thought {
            content,
            interrupted,
            live,
            collapsed,
            ..
        }) = self.transcript.get_mut(index)
            && !(*interrupted && !*live && content.is_empty())
        {
            *collapsed = !*collapsed;
        }
    }

    pub fn toggle_activity(&mut self, index: usize) {
        let Some(TranscriptItem::Activity { collapsed, .. }) = self.transcript.get_mut(index)
        else {
            return;
        };
        *collapsed = !*collapsed;
    }

    fn ensure_thought(&mut self, run_id: &str, live: bool) -> usize {
        if let Some(index) = self.transcript.iter().rposition(|item| match item {
            TranscriptItem::Thought {
                run_id: id,
                live: existing_live,
                ..
            } => id == run_id && (!live || *existing_live),
            _ => false,
        }) {
            return index;
        }
        self.collapse_completed_thoughts();
        self.transcript.push(TranscriptItem::Thought {
            run_id: run_id.to_owned(),
            content: String::new(),
            duration_seconds: 0.0,
            interrupted: false,
            live,
            collapsed: true,
        });
        self.transcript.len() - 1
    }

    fn ensure_assistant(&mut self, run_id: &str, live: bool) -> usize {
        if let Some(index) = self.transcript.iter().rposition(|item| match item {
            TranscriptItem::Assistant {
                run_id: id,
                live: existing_live,
                durable,
                ..
            } => id == run_id && if live { *existing_live } else { !*durable },
            _ => false,
        }) {
            return index;
        }
        self.transcript.push(TranscriptItem::Assistant {
            run_id: run_id.to_owned(),
            content: String::new(),
            live,
            durable: false,
        });
        self.transcript.len() - 1
    }

    fn ensure_activity(&mut self, run_id: &str) -> usize {
        if let Some(index) = self.transcript.len().checked_sub(1)
            && matches!(
                &self.transcript[index],
                TranscriptItem::Activity { run_id: id, .. } if id == run_id
            )
        {
            return index;
        }
        self.transcript.push(TranscriptItem::Activity {
            run_id: run_id.to_owned(),
            rows: Vec::new(),
            collapsed: false,
        });
        self.transcript.len() - 1
    }

    fn ensure_activity_row(
        &mut self,
        run_id: &str,
        index: u64,
        call_id: Option<String>,
    ) -> &mut ActivityRow {
        let existing =
            self.transcript
                .iter()
                .enumerate()
                .rev()
                .find_map(|(activity_index, item)| {
                    let TranscriptItem::Activity {
                        run_id: id, rows, ..
                    } = item
                    else {
                        return None;
                    };
                    if id != run_id {
                        return None;
                    }
                    rows.iter()
                        .position(|row| {
                            call_id
                                .as_ref()
                                .is_some_and(|id| row.tool_call_id.as_ref() == Some(id))
                        })
                        .or_else(|| {
                            call_id
                                .is_some()
                                .then(|| {
                                    rows.iter().position(|row| {
                                        row.tool_call_id.is_none() && row.index == index
                                    })
                                })
                                .flatten()
                        })
                        .map(|row_index| (activity_index, row_index))
                });
        if let Some((activity_index, row_index)) = existing {
            let TranscriptItem::Activity { rows, .. } = &mut self.transcript[activity_index] else {
                unreachable!();
            };
            if call_id.is_some() {
                rows[row_index].tool_call_id = call_id;
            }
            return &mut rows[row_index];
        }
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
        if let Some(content) = payload.get("content").and_then(Value::as_str) {
            row.content = content.to_owned();
        }
        if let Some(structured_data) = payload.get("structured_data") {
            row.structured_data = structured_data.clone();
        }
        row.truncated = payload
            .get("truncated")
            .and_then(Value::as_bool)
            .unwrap_or(row.truncated);
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

    fn finish_live_assistant(&mut self, run_id: &str) {
        for item in self.transcript.iter_mut().rev() {
            if let TranscriptItem::Assistant {
                run_id: id, live, ..
            } = item
                && id == run_id
                && *live
            {
                *live = false;
                return;
            }
        }
    }

    fn finish_run(&mut self, run_id: &str, cancelled: bool) {
        for item in &mut self.transcript {
            match item {
                TranscriptItem::Thought {
                    run_id: id,
                    live,
                    interrupted,
                    ..
                } if id == run_id => {
                    let was_live = *live;
                    *live = false;
                    if cancelled && was_live {
                        *interrupted = true;
                    }
                }
                TranscriptItem::Assistant {
                    run_id: id, live, ..
                } if id == run_id => *live = false,
                TranscriptItem::Activity {
                    run_id: id,
                    rows,
                    collapsed,
                } if id == run_id => {
                    for row in rows {
                        if !row.phase.terminal() {
                            row.phase = if cancelled {
                                ActivityPhase::Cancelled
                            } else {
                                ActivityPhase::Failed
                            };
                        }
                    }
                    *collapsed = true;
                }
                _ => {}
            }
        }
        for item in &mut self.transcript {
            if let TranscriptItem::Activity {
                run_id: id, rows, ..
            } = item
                && id == run_id
            {
                rows.retain(|row| !row.name.is_empty());
            }
        }
        self.transcript.retain(|item| match item {
            TranscriptItem::Assistant {
                run_id: id,
                content,
                ..
            } if id == run_id => !content.trim().is_empty(),
            TranscriptItem::Activity {
                run_id: id, rows, ..
            } if id == run_id => !rows.is_empty(),
            _ => true,
        });
    }
}

fn selected_transcript_text(lines: &[String], selection: TranscriptSelection) -> Option<String> {
    let (start, end) = selection.bounds();
    if start.row >= lines.len() {
        return None;
    }
    let end_row = end.row.min(lines.len().saturating_sub(1));
    let mut selected = Vec::new();
    for (row, line) in lines.iter().enumerate().take(end_row + 1).skip(start.row) {
        let width = UnicodeWidthStr::width(line.as_str());
        let from = if row == start.row { start.column } else { 0 }.min(width);
        let to = if row == end_row {
            end.column.saturating_add(1)
        } else {
            width
        }
        .min(width);
        selected.push(display_slice(line, from, to));
    }
    let text = selected.join("\n");
    (!text.is_empty()).then_some(text)
}

fn selection_range(
    lines: &[String],
    selection: Option<TranscriptSelection>,
    row: usize,
) -> Option<(usize, usize)> {
    let selection = selection?;
    if selection.anchor == selection.head {
        return None;
    }
    let (start, end) = selection.bounds();
    if row < start.row || row > end.row {
        return None;
    }
    let line_width = lines
        .get(row)
        .map(|line| UnicodeWidthStr::width(line.as_str()))
        .unwrap_or_default();
    let from = if row == start.row { start.column } else { 0 }.min(line_width);
    let to = if row == end.row {
        end.column.saturating_add(1)
    } else {
        line_width
    }
    .min(line_width);
    (from < to).then_some((from, to))
}

fn display_slice(value: &str, start: usize, end: usize) -> String {
    let mut column = 0;
    let mut selected = String::new();
    for grapheme in value.graphemes(true) {
        let next = column + UnicodeWidthStr::width(grapheme);
        if next > start && column < end {
            selected.push_str(grapheme);
        }
        column = next;
    }
    selected
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

fn dream_phase(event_type: &str) -> DreamPhase {
    if event_type.ends_with(".completed") {
        DreamPhase::Completed
    } else if event_type.ends_with(".paused") {
        DreamPhase::Paused
    } else if event_type.ends_with(".failed") {
        DreamPhase::Failed
    } else if event_type.ends_with(".started") {
        DreamPhase::Running
    } else {
        DreamPhase::Queued
    }
}

fn dream_detail(label: &str, phase: DreamPhase, payload: &Value) -> String {
    if phase == DreamPhase::Failed {
        let message = string(payload, "error_message");
        return if message.is_empty() {
            format!("{label} paused")
        } else {
            format!("{label} paused · {message}")
        };
    }
    match phase {
        DreamPhase::Queued => format!("{label} queued"),
        DreamPhase::Running => match label {
            "Memory consolidation" => "Consolidating memory in the background".to_owned(),
            "Requested memory capture" => "Capturing requested memory in the background".to_owned(),
            "Skill refinement" => "Refining a reusable Skill in the background".to_owned(),
            _ => format!("{label} in the background"),
        },
        DreamPhase::Completed => match label {
            "Memory consolidation" => "Memory consolidated".to_owned(),
            "Requested memory capture" => "Requested memory captured".to_owned(),
            "Skill refinement" => "Skill refinement complete".to_owned(),
            _ => format!("{label} complete"),
        },
        DreamPhase::Paused => format!("{label} paused for foreground work"),
        DreamPhase::Failed => unreachable!(),
    }
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
        detail_scroll: 0,
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

    use super::{
        App, Composer, ComposerUnit, DreamPhase, TranscriptItem, TranscriptPoint,
        TranscriptViewport,
    };
    use crate::api::{Event, PasteSpan, Session};

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
    fn queued_message_rehydrates_paste_capsules_exactly() {
        let mut composer = Composer::default();
        let content = "before one\ntwo\nthree\nfour after";
        composer.load_message(
            content,
            &[PasteSpan {
                start_byte: 7,
                end_byte: 25,
                line_count: 4,
                byte_count: 18,
            }],
        );
        assert_eq!(composer.message().0, content);
        assert!(matches!(composer.units[7], ComposerUnit::Paste(_)));
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
            Some(TranscriptItem::Thought {
                live: true,
                collapsed: true,
                ..
            })
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
        app.ingest_durable(
            event(5, "run.completed", run_id, json!({"active_seconds": 251.0})),
            true,
        );
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
        assert!(matches!(
            app.transcript.last(),
            Some(TranscriptItem::Worked { duration_seconds }) if *duration_seconds == 251.0
        ));
    }

    #[test]
    fn durable_replay_preserves_prose_tool_prose_order() {
        let run_id = "run-segmented";
        let events = vec![
            event(1, "run.started", run_id, json!({})),
            event(2, "model.requested", run_id, json!({})),
            event(
                3,
                "assistant.message",
                run_id,
                json!({"content": "I will edit it.", "status": "interrupted"}),
            ),
            event(
                4,
                "model.tool_call",
                run_id,
                json!({
                    "index": 0,
                    "tool_call_id": "tool-1",
                    "name": "edit_file",
                    "arguments": {"path": "src/main.rs", "old_text": "old", "new_text": "new"}
                }),
            ),
            event(
                5,
                "tool.completed",
                run_id,
                json!({
                    "index": 0,
                    "tool_call_id": "tool-1",
                    "name": "edit_file",
                    "status": "completed",
                    "summary": "edited src/main.rs",
                    "content": "--- a/src/main.rs\n+++ b/src/main.rs\n-old\n+new\n"
                }),
            ),
            event(6, "model.requested", run_id, json!({})),
            event(
                7,
                "assistant.message",
                run_id,
                json!({"content": "The edit is done.", "status": "completed"}),
            ),
            event(8, "run.completed", run_id, json!({})),
        ];
        let app = App::new(session(), events, true);
        let positions = app
            .transcript
            .iter()
            .enumerate()
            .filter_map(|(index, item)| match item {
                TranscriptItem::Assistant { content, .. } => Some((index, content.as_str())),
                _ => None,
            })
            .collect::<Vec<_>>();
        let activity = app
            .transcript
            .iter()
            .position(|item| matches!(item, TranscriptItem::Activity { .. }))
            .unwrap();
        assert_eq!(positions.len(), 2);
        assert_eq!(positions[0].1, "I will edit it.");
        assert_eq!(positions[1].1, "The edit is done.");
        assert!(positions[0].0 < activity && activity < positions[1].0);
    }

    #[test]
    fn background_model_requests_do_not_create_phantom_thoughts() {
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_durable(event(1, "run.started", "chat-run", json!({})), true);
        app.ingest_transient("chat-run", "response.text_delta", &json!({"text": "Hello"}));
        app.ingest_durable(event(2, "run.completed", "chat-run", json!({})), true);
        app.ingest_durable(
            event(3, "model.requested", "background-memory-job", json!({})),
            true,
        );

        assert!(!app.animating());
        assert!(!app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Thought { run_id, .. } if run_id == "background-memory-job"
        )));
    }

    #[test]
    fn maintenance_preemption_is_a_neutral_paused_dream_not_a_global_error() {
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_durable(
            event(
                1,
                "model.response.preempted",
                "background-memory-job",
                json!({
                    "code": "maintenance_preempted",
                    "message": "background model work yielded to a foreground request",
                    "retryable": true,
                    "details": {}
                }),
            ),
            true,
        );
        app.ingest_durable(
            event(
                2,
                "memory.job.paused",
                "source-run",
                json!({
                    "job_id": "background-memory-job",
                    "kind": "extraction",
                    "status": "pending",
                    "attempts": 0,
                    "error_code": "maintenance_preempted",
                    "error_message": "background model work yielded to a foreground request"
                }),
            ),
            true,
        );

        assert!(app.modal.is_none());
        assert!(
            !app.transcript
                .iter()
                .any(|item| matches!(item, TranscriptItem::Status { error: true, .. }))
        );
        assert!(app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Dream {
                phase: DreamPhase::Paused,
                detail,
                ..
            } if detail == "Memory consolidation paused for foreground work"
        )));
    }

    #[test]
    fn streamed_output_respects_manual_scroll_until_the_user_returns_to_bottom() {
        let mut app = App::new(session(), Vec::new(), true);
        app.scroll = 12;
        app.ingest_transient(
            "run-scroll",
            "response.text_delta",
            &json!({"text": "first"}),
        );
        assert_eq!(app.scroll, 12);

        app.ingest_durable(
            event(
                1,
                "assistant.message",
                "run-scroll",
                json!({"content": "first", "status": "completed"}),
            ),
            true,
        );
        assert_eq!(app.scroll, 12);

        app.scroll = 0;
        app.ingest_transient(
            "run-scroll",
            "response.text_delta",
            &json!({"text": " second"}),
        );
        assert_eq!(app.scroll, 0);
    }

    #[test]
    fn a_live_user_message_rearms_bottom_following() {
        let mut app = App::new(session(), Vec::new(), true);
        app.scroll = 20;
        app.ingest_durable(
            event(
                1,
                "user.message",
                "run-new",
                json!({"content": "new request"}),
            ),
            true,
        );
        assert_eq!(app.scroll, 0);
    }

    #[test]
    fn background_memory_lifecycle_is_one_dormant_transcript_item() {
        let mut app = App::new(session(), Vec::new(), true);
        let payload = |status: &str| {
            json!({
                "job_id": "memory-job",
                "kind": "extraction",
                "status": status,
                "attempts": 1
            })
        };
        app.ingest_durable(
            event(1, "memory.job.queued", "source-run", payload("pending")),
            true,
        );
        app.ingest_durable(
            event(2, "memory.job.started", "source-run", payload("running")),
            true,
        );
        app.ingest_durable(
            event(
                3,
                "memory.job.completed",
                "source-run",
                payload("completed"),
            ),
            true,
        );

        let dreams = app
            .transcript
            .iter()
            .filter_map(|item| match item {
                TranscriptItem::Dream { phase, detail, .. } => Some((*phase, detail.as_str())),
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(dreams, vec![(DreamPhase::Completed, "Memory consolidated")]);
        assert!(app.active_run.is_none());
        assert!(!app.animating());
    }

    #[test]
    fn assistant_tool_and_followup_keep_chronological_segments() {
        let run_id = "run-late-tool";
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_transient(
            run_id,
            "response.text_delta",
            &json!({"text": "I will create it."}),
        );
        app.ingest_transient(
            run_id,
            "response.tool_call_delta",
            &json!({"index": 0, "name": "write_file", "arguments_delta": "{\"path\":\"game.py\"}"}),
        );
        app.ingest_durable(
            event(
                1,
                "model.tool_call",
                run_id,
                json!({
                    "index": 0,
                    "tool_call_id": "tool-1",
                    "name": "write_file",
                    "arguments": {"path": "game.py", "content": "print('hi')"}
                }),
            ),
            true,
        );
        app.ingest_transient(
            run_id,
            "response.text_delta",
            &json!({"text": "It is ready."}),
        );
        app.ingest_durable(
            event(
                2,
                "tool.completed",
                run_id,
                json!({
                    "index": 0,
                    "tool_call_id": "tool-1",
                    "name": "write_file",
                    "summary": "wrote game.py"
                }),
            ),
            true,
        );

        assert!(matches!(
            &app.transcript[..],
            [
                TranscriptItem::Assistant { content: before, .. },
                TranscriptItem::Activity { .. },
                TranscriptItem::Assistant { content: after, .. }
            ] if before == "I will create it." && after == "It is ready."
        ));
    }

    #[test]
    fn empty_stream_deltas_do_not_create_phantom_output_or_tools() {
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_durable(event(1, "run.started", "run-empty", json!({})), true);
        app.ingest_transient("run-empty", "response.text_delta", &json!({"text": ""}));
        app.ingest_transient(
            "run-empty",
            "response.tool_call_delta",
            &json!({"index": 0, "name": "", "arguments_delta": ""}),
        );
        app.ingest_durable(event(2, "run.completed", "run-empty", json!({})), true);

        assert!(!app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Assistant { .. } | TranscriptItem::Activity { .. }
        )));
    }

    #[test]
    fn only_user_messages_make_a_session_resumable() {
        let mut app = App::new(session(), Vec::new(), true);
        assert!(app.conversation_is_empty());
        app.transcript.push(TranscriptItem::Status {
            text: "Ready".to_owned(),
            error: false,
        });
        assert!(app.conversation_is_empty());
        app.transcript.push(TranscriptItem::User {
            content: "Hello".to_owned(),
        });
        assert!(!app.conversation_is_empty());
    }

    #[test]
    fn transcript_selection_extracts_visible_text_across_lines() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript_viewport = TranscriptViewport {
            x: 10,
            y: 5,
            width: 40,
            height: 2,
            lines: vec!["Hello world".to_owned(), "Second line".to_owned()],
        };
        app.begin_transcript_selection(TranscriptPoint { row: 0, column: 6 });
        app.update_transcript_selection(TranscriptPoint { row: 1, column: 5 });

        assert_eq!(
            app.finish_transcript_selection().as_deref(),
            Some("world\nSecond")
        );
        assert_eq!(app.transcript_selection_range(0), Some((6, 11)));
        assert_eq!(app.transcript_selection_range(1), Some((0, 6)));
    }

    #[test]
    fn cancellation_finalizes_and_marks_live_thought() {
        let run_id = "run-cancelled";
        let mut app = App::new(session(), Vec::new(), true);
        app.ingest_durable(event(1, "run.started", run_id, json!({})), true);
        app.ingest_durable(event(2, "model.requested", run_id, json!({})), true);
        app.ingest_durable(event(3, "run.cancelled", run_id, json!({})), true);

        assert!(app.active_run.is_none());
        assert!(app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Thought {
                interrupted: true,
                live: false,
                ..
            }
        )));
        assert!(app.transcript.iter().any(|item| matches!(
            item,
            TranscriptItem::Status { text, error: false } if text == "Turn interrupted"
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
