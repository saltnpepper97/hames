use serde_json::{Map, Value};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::style::{self, Badge};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActivityCategory {
    Explore,
    Change,
    Run,
    Delegate,
    Skills,
    Plugin,
}

impl ActivityCategory {
    pub fn badge(self) -> Badge {
        match self {
            Self::Explore => Badge::Explore,
            Self::Change => Badge::Change,
            Self::Run => Badge::Run,
            Self::Delegate => Badge::Delegate,
            Self::Skills => Badge::Skills,
            Self::Plugin => Badge::Plugin,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ActivityPhase {
    Preparing,
    Checking,
    AwaitingApproval,
    Running,
    Completed,
    Failed,
    Rejected,
    Cancelled,
}

impl ActivityPhase {
    fn terminal(self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Failed | Self::Rejected | Self::Cancelled
        )
    }

    fn glyph(self) -> &'static str {
        match self {
            Self::Preparing => "●",
            Self::Checking | Self::AwaitingApproval => "○",
            Self::Running => "◐",
            Self::Completed => "✓",
            Self::Rejected => "!",
            Self::Failed | Self::Cancelled => "×",
        }
    }

    fn paint_glyph(self) -> String {
        match self {
            Self::Completed => style::paint("32", self.glyph()),
            Self::Checking | Self::AwaitingApproval | Self::Running | Self::Rejected => {
                style::paint("33", self.glyph())
            }
            Self::Failed | Self::Cancelled => style::paint("31", self.glyph()),
            Self::Preparing => style::dim(self.glyph()),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ToolKind {
    Read,
    List,
    Write,
    Edit,
    Command,
    Delegate,
    SkillLoad,
    SkillAuthor,
    SkillRun,
    Plugin,
    Unknown,
}

impl ToolKind {
    fn from_name(name: &str) -> Self {
        match name {
            "read_file" => Self::Read,
            "list_dir" => Self::List,
            "write_file" => Self::Write,
            "edit_file" => Self::Edit,
            "shell" => Self::Command,
            "spawn_agent" => Self::Delegate,
            "skill_load" => Self::SkillLoad,
            "skill_author" => Self::SkillAuthor,
            "skill_run" => Self::SkillRun,
            value if value.contains('.') => Self::Plugin,
            _ => Self::Unknown,
        }
    }

    fn category(self) -> ActivityCategory {
        match self {
            Self::Read | Self::List => ActivityCategory::Explore,
            Self::Write | Self::Edit => ActivityCategory::Change,
            Self::Command | Self::SkillRun => ActivityCategory::Run,
            Self::Delegate => ActivityCategory::Delegate,
            Self::SkillLoad | Self::SkillAuthor => ActivityCategory::Skills,
            Self::Plugin => ActivityCategory::Plugin,
            Self::Unknown => ActivityCategory::Run,
        }
    }

    fn verb(self, phase: ActivityPhase) -> &'static str {
        match (self, phase) {
            (Self::Read, ActivityPhase::Preparing) => "Preparing read",
            (Self::Read, ActivityPhase::Running) => "Reading",
            (Self::Read, ActivityPhase::Completed) => "Read",
            (Self::List, ActivityPhase::Preparing) => "Preparing list",
            (Self::List, ActivityPhase::Running) => "Listing",
            (Self::List, ActivityPhase::Completed) => "Listed",
            (Self::Write, ActivityPhase::Preparing) => "Preparing write",
            (Self::Write, ActivityPhase::Running) => "Writing",
            (Self::Write, ActivityPhase::Completed) => "Wrote",
            (Self::Edit, ActivityPhase::Preparing) => "Preparing edit",
            (Self::Edit, ActivityPhase::Running) => "Editing",
            (Self::Edit, ActivityPhase::Completed) => "Edited",
            (Self::Command, ActivityPhase::Preparing) => "Preparing command",
            (Self::Command, ActivityPhase::Running) => "Running",
            (Self::Command, ActivityPhase::Completed) => "Ran",
            (Self::Delegate, ActivityPhase::Preparing) => "Preparing delegation",
            (Self::Delegate, ActivityPhase::Running) => "Delegating",
            (Self::Delegate, ActivityPhase::Completed) => "Delegated",
            (Self::SkillLoad, ActivityPhase::Preparing) => "Preparing skill",
            (Self::SkillLoad, ActivityPhase::Running) => "Loading",
            (Self::SkillLoad, ActivityPhase::Completed) => "Loaded",
            (Self::SkillAuthor, ActivityPhase::Preparing) => "Preparing skill",
            (Self::SkillAuthor, ActivityPhase::Running) => "Authoring",
            (Self::SkillAuthor, ActivityPhase::Completed) => "Authored",
            (Self::SkillRun, ActivityPhase::Preparing) => "Preparing script",
            (Self::SkillRun, ActivityPhase::Running) => "Running",
            (Self::SkillRun, ActivityPhase::Completed) => "Ran",
            (Self::Plugin, ActivityPhase::Preparing) => "Preparing plugin",
            (Self::Plugin, ActivityPhase::Running) => "Using plugin",
            (Self::Plugin, ActivityPhase::Completed) => "Used plugin",
            (Self::Unknown, ActivityPhase::Preparing) => "Preparing action",
            (Self::Unknown, ActivityPhase::Running) => "Working",
            (Self::Unknown, ActivityPhase::Completed) => "Completed",
            (_, ActivityPhase::Checking) => "Checking policy",
            (_, ActivityPhase::AwaitingApproval) => "Awaiting approval",
            (_, ActivityPhase::Rejected) => "Rejected",
            (_, ActivityPhase::Failed) => "Failed",
            (_, ActivityPhase::Cancelled) => "Cancelled",
        }
    }
}

#[derive(Clone, Debug)]
struct ToolActivity {
    turn: u64,
    index: u64,
    provider_call_id: Option<String>,
    tool_call_id: Option<String>,
    name: String,
    argument_parts: String,
    arguments: Map<String, Value>,
    phase: ActivityPhase,
    summary: String,
    structured_data: Map<String, Value>,
    duration_seconds: f64,
    truncated: bool,
}

impl ToolActivity {
    fn new(turn: u64, index: u64) -> Self {
        Self {
            turn,
            index,
            provider_call_id: None,
            tool_call_id: None,
            name: String::new(),
            argument_parts: String::new(),
            arguments: Map::new(),
            phase: ActivityPhase::Preparing,
            summary: String::new(),
            structured_data: Map::new(),
            duration_seconds: 0.0,
            truncated: false,
        }
    }

    fn kind(&self) -> ToolKind {
        ToolKind::from_name(&self.name)
    }

    fn category(&self) -> ActivityCategory {
        self.kind().category()
    }

    fn target(&self) -> String {
        let value = match self.kind() {
            ToolKind::Read | ToolKind::List | ToolKind::Write | ToolKind::Edit => {
                self.arguments.get("path").and_then(Value::as_str)
            }
            ToolKind::Command => self.arguments.get("command").and_then(Value::as_str),
            ToolKind::Delegate => self.arguments.get("agent_id").and_then(Value::as_str),
            ToolKind::SkillLoad | ToolKind::SkillRun => {
                self.arguments.get("id").and_then(Value::as_str)
            }
            ToolKind::SkillAuthor => self.arguments.get("goal").and_then(Value::as_str),
            ToolKind::Plugin => Some(self.name.as_str()),
            ToolKind::Unknown => (!self.name.is_empty()).then_some(self.name.as_str()),
        };
        let Some(value) = value else {
            return String::new();
        };
        let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
        let workspace = self.arguments.get("workspace").and_then(Value::as_str);
        match workspace {
            Some("scratch") => format!("scratch:{compact}"),
            Some("home") if compact == "." => "~".to_owned(),
            Some("home") => format!("~/{compact}"),
            _ => compact,
        }
    }

    fn result_detail(&self) -> String {
        if matches!(self.phase, ActivityPhase::Failed | ActivityPhase::Rejected)
            && !self.summary.is_empty()
        {
            return self.summary.clone();
        }
        let mut parts = Vec::new();
        match self.kind() {
            ToolKind::Read => {
                push_number(&mut parts, &self.structured_data, "lines", "lines");
                push_number(&mut parts, &self.structured_data, "bytes", "bytes");
            }
            ToolKind::List => {
                if let Some(count) = self
                    .structured_data
                    .get("entries")
                    .and_then(Value::as_array)
                    .map(Vec::len)
                {
                    parts.push(format!("{count} entries"));
                }
            }
            ToolKind::Write => {
                if let Some(created) = self.structured_data.get("created").and_then(Value::as_bool)
                {
                    parts.push(if created { "created" } else { "replaced" }.to_owned());
                }
                push_number(&mut parts, &self.structured_data, "bytes", "bytes");
            }
            ToolKind::Edit => parts.push("1 replacement".to_owned()),
            ToolKind::Command => {
                if let Some(code) = self
                    .structured_data
                    .get("exit_code")
                    .and_then(Value::as_i64)
                {
                    parts.push(format!("exit {code}"));
                }
            }
            _ if self.phase == ActivityPhase::Completed && !self.summary.is_empty() => {
                parts.push(self.summary.clone());
            }
            _ => {}
        }
        if self.truncated {
            parts.push("truncated".to_owned());
        }
        if self.duration_seconds > 0.0 {
            parts.push(format_duration(self.duration_seconds));
        }
        parts.join(" · ")
    }

    fn line(&self, columns: usize) -> String {
        let verb = self.kind().verb(self.phase);
        let target = self.target();
        let detail = self.result_detail();
        let glyph = self.phase.glyph();
        let mut body = format!("  {glyph} {verb}");
        if !target.is_empty() {
            body.push_str("  ");
            body.push_str(&target);
        }
        if !detail.is_empty() {
            body.push_str(" · ");
            body.push_str(&detail);
        }
        let fitted = fit_visible(&body, columns.saturating_sub(1).max(24));
        fitted.replacen(glyph, &self.phase.paint_glyph(), 1)
    }
}

#[derive(Default)]
pub struct ActivityBoard {
    turn: u64,
    rows: Vec<ToolActivity>,
}

impl ActivityBoard {
    pub fn next_turn(&mut self) {
        self.turn = self.turn.saturating_add(1);
        self.rows.clear();
    }

    pub fn clear(&mut self) {
        self.rows.clear();
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    pub fn has_live_rows(&self) -> bool {
        self.rows.iter().any(|row| !row.phase.terminal())
    }

    pub fn transient_delta(&mut self, payload: &Value) -> Option<usize> {
        let index = payload.get("index")?.as_u64()?;
        let turn = self.turn;
        let row_index = self
            .rows
            .iter()
            .position(|row| row.turn == turn && row.index == index)
            .unwrap_or_else(|| {
                self.rows.push(ToolActivity::new(turn, index));
                self.rows.len() - 1
            });
        let row = &mut self.rows[row_index];
        if let Some(id) = payload.get("provider_call_id").and_then(Value::as_str) {
            row.provider_call_id = Some(id.to_owned());
        }
        if let Some(name) = payload.get("name").and_then(Value::as_str) {
            row.name.push_str(name);
        }
        if let Some(arguments) = payload.get("arguments_delta").and_then(Value::as_str) {
            row.argument_parts.push_str(arguments);
            if let Ok(Value::Object(parsed)) = serde_json::from_str(&row.argument_parts) {
                row.arguments = parsed;
            }
        }
        Some(row_index)
    }

    pub fn durable_event(&mut self, event_type: &str, payload: &Value) -> Option<usize> {
        if event_type == "model.tool_call" {
            let index = payload.get("index")?.as_u64()?;
            let turn = self.turn;
            let row_index = self
                .rows
                .iter()
                .position(|row| row.turn == turn && row.index == index)
                .unwrap_or_else(|| {
                    self.rows.push(ToolActivity::new(turn, index));
                    self.rows.len() - 1
                });
            let row = &mut self.rows[row_index];
            set_identity(row, payload);
            set_arguments(row, payload);
            return Some(row_index);
        }

        let tool_call_id = payload.get("tool_call_id").and_then(Value::as_str)?;
        let row_index = self
            .rows
            .iter()
            .position(|row| row.tool_call_id.as_deref() == Some(tool_call_id))
            .unwrap_or_else(|| {
                let mut row = ToolActivity::new(self.turn, self.rows.len() as u64);
                row.tool_call_id = Some(tool_call_id.to_owned());
                self.rows.push(row);
                self.rows.len() - 1
            });
        let row = &mut self.rows[row_index];
        set_identity(row, payload);
        set_arguments(row, payload);
        row.phase = match event_type {
            "tool.requested" | "policy.requested" => ActivityPhase::Checking,
            "policy.decided" => match payload.get("decision").and_then(Value::as_str) {
                Some("deny") => ActivityPhase::Rejected,
                _ => ActivityPhase::Checking,
            },
            "approval.requested" => ActivityPhase::AwaitingApproval,
            "approval.resolved" => match payload.get("decision").and_then(Value::as_str) {
                Some("denied" | "cancelled") => ActivityPhase::Rejected,
                _ => ActivityPhase::Checking,
            },
            "tool.started" => ActivityPhase::Running,
            "tool.completed" => ActivityPhase::Completed,
            "tool.failed" => ActivityPhase::Failed,
            "tool.rejected" => ActivityPhase::Rejected,
            _ => return None,
        };
        if let Some(summary) = payload.get("summary").and_then(Value::as_str) {
            row.summary = summary.to_owned();
        }
        if let Some(Value::Object(data)) = payload.get("structured_data") {
            row.structured_data.clone_from(data);
        }
        row.duration_seconds = payload
            .get("duration_seconds")
            .and_then(Value::as_f64)
            .unwrap_or(row.duration_seconds);
        row.truncated = payload
            .get("truncated")
            .and_then(Value::as_bool)
            .unwrap_or(row.truncated);
        Some(row_index)
    }

    pub fn cancel_live(&mut self) {
        for row in &mut self.rows {
            if !row.phase.terminal() {
                row.phase = ActivityPhase::Cancelled;
            }
        }
    }

    pub fn fail_live(&mut self, summary: &str) {
        for row in &mut self.rows {
            if !row.phase.terminal() {
                row.phase = ActivityPhase::Failed;
                row.summary = summary.to_owned();
            }
        }
    }

    pub fn row_category(&self, index: usize) -> Option<ActivityCategory> {
        self.rows.get(index).map(ToolActivity::category)
    }

    pub fn row_line(&self, index: usize, columns: usize) -> Option<String> {
        self.rows.get(index).map(|row| row.line(columns))
    }

    pub fn render_lines(&self, columns: usize, live: bool) -> Vec<String> {
        let mut lines = Vec::new();
        let mut start = 0;
        while start < self.rows.len() {
            let category = self.rows[start].category();
            let mut end = start + 1;
            while end < self.rows.len() && self.rows[end].category() == category {
                end += 1;
            }
            let section_live = live
                && self.rows[start..end]
                    .iter()
                    .any(|row| !row.phase.terminal());
            lines.push(style::badge(category.badge(), section_live));
            lines.extend(self.rows[start..end].iter().map(|row| row.line(columns)));
            start = end;
        }
        lines
    }
}

fn set_identity(row: &mut ToolActivity, payload: &Value) {
    if let Some(id) = payload.get("tool_call_id").and_then(Value::as_str) {
        row.tool_call_id = Some(id.to_owned());
    }
    if let Some(id) = payload.get("provider_call_id").and_then(Value::as_str) {
        row.provider_call_id = Some(id.to_owned());
    }
    if let Some(name) = payload.get("name").and_then(Value::as_str) {
        row.name = name.to_owned();
    }
}

fn set_arguments(row: &mut ToolActivity, payload: &Value) {
    if let Some(Value::Object(arguments)) = payload.get("arguments") {
        row.arguments.clone_from(arguments);
    }
}

fn push_number(parts: &mut Vec<String>, data: &Map<String, Value>, key: &str, unit: &str) {
    if let Some(value) = data.get(key).and_then(Value::as_u64) {
        parts.push(format!("{value} {unit}"));
    }
}

fn format_duration(seconds: f64) -> String {
    if seconds < 1.0 {
        format!("{} ms", (seconds * 1000.0).round() as u64)
    } else {
        format!("{seconds:.1} s")
    }
}

fn fit_visible(text: &str, width: usize) -> String {
    if UnicodeWidthStr::width(text) <= width {
        return text.to_owned();
    }
    let target = width.saturating_sub(1);
    let mut out = String::new();
    let mut used = 0;
    for ch in text.chars() {
        let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + char_width > target {
            break;
        }
        out.push(ch);
        used += char_width;
    }
    out.push('…');
    out
}

#[cfg(test)]
mod tests {
    use serde_json::json;
    use unicode_width::UnicodeWidthStr;

    use super::ActivityBoard;

    #[test]
    fn streamed_edit_reconciles_with_durable_lifecycle() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        board.transient_delta(&json!({
            "index": 0,
            "provider_call_id": "provider-1",
            "name": "edit_file",
            "arguments_delta": "{\"path\":\"src/main.rs\"}"
        }));
        assert!(board.row_line(0, 80).unwrap().contains("Preparing edit"));
        assert!(board.row_line(0, 80).unwrap().contains("src/main.rs"));

        board.durable_event(
            "model.tool_call",
            &json!({
                "index": 0,
                "tool_call_id": "tool-1",
                "provider_call_id": "provider-1",
                "name": "edit_file",
                "arguments": {"path": "src/main.rs"}
            }),
        );
        board.durable_event(
            "tool.started",
            &json!({"tool_call_id": "tool-1", "name": "edit_file"}),
        );
        assert!(board.row_line(0, 80).unwrap().contains("Editing"));

        board.durable_event(
            "tool.completed",
            &json!({
                "tool_call_id": "tool-1",
                "name": "edit_file",
                "status": "completed",
                "summary": "edited src/main.rs",
                "structured_data": {"path": "src/main.rs"},
                "duration_seconds": 0.018
            }),
        );
        let settled = board.row_line(0, 80).unwrap();
        assert!(settled.contains("Edited"));
        assert!(settled.contains("1 replacement"));
        assert!(settled.contains("18 ms"));
    }

    #[test]
    fn missed_transient_is_rebuilt_from_durable_events() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        board.durable_event(
            "model.tool_call",
            &json!({
                "index": 0,
                "tool_call_id": "tool-1",
                "name": "read_file",
                "arguments": {"path": "README.md"}
            }),
        );
        board.durable_event(
            "tool.completed",
            &json!({
                "tool_call_id": "tool-1",
                "name": "read_file",
                "status": "completed",
                "summary": "read README.md",
                "structured_data": {"lines": 20, "bytes": 400},
                "duration_seconds": 0.002
            }),
        );
        let settled = board.row_line(0, 80).unwrap();
        assert!(settled.contains("Read"));
        assert!(settled.contains("20 lines"));
    }

    #[test]
    fn partial_name_and_arguments_only_reveal_truthful_target() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        board.transient_delta(&json!({
            "index": 0,
            "name": "edit_",
            "arguments_delta": "{\"path\":\"src/"
        }));
        let partial = board.row_line(0, 80).unwrap();
        assert!(partial.contains("Preparing action"));
        assert!(!partial.contains("src/"));

        board.transient_delta(&json!({
            "index": 0,
            "name": "file",
            "arguments_delta": "lib.rs\"}"
        }));
        let complete = board.row_line(0, 80).unwrap();
        assert!(complete.contains("Preparing edit"));
        assert!(complete.contains("src/lib.rs"));
    }

    #[test]
    fn categories_group_adjacent_calls_without_reordering() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        for (index, name, path) in [
            (0, "read_file", "one"),
            (1, "list_dir", "two"),
            (2, "edit_file", "three"),
        ] {
            board.transient_delta(&json!({
                "index": index,
                "name": name,
                "arguments_delta": json!({"path": path}).to_string()
            }));
        }
        let lines = board.render_lines(80, false);
        assert_eq!(
            lines.iter().filter(|line| line.contains("Explore")).count(),
            1
        );
        assert_eq!(
            lines.iter().filter(|line| line.contains("Change")).count(),
            1
        );
        assert!(lines[1].contains("one"));
        assert!(lines[2].contains("two"));
        assert!(lines[4].contains("three"));
    }

    #[test]
    fn narrow_activity_rows_are_single_line_and_bounded() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        board.transient_delta(&json!({
            "index": 0,
            "name": "shell",
            "arguments_delta": "{\"command\":\"cargo test --locked --all-targets --a-very-long-flag\"}"
        }));
        for width in [32, 40, 80, 120] {
            let line = board.row_line(0, width).unwrap();
            assert!(!line.contains('\n'));
            assert!(UnicodeWidthStr::width(line.as_str()) < width);
        }
        assert!(board.row_line(0, 32).unwrap().ends_with('…'));
    }

    #[test]
    fn home_workspace_keeps_tilde_in_the_transcript() {
        let mut board = ActivityBoard::default();
        board.next_turn();
        board.transient_delta(&json!({
            "index": 0,
            "name": "read_file",
            "arguments_delta": "{\"workspace\":\"home\",\"path\":\".zshrc\"}"
        }));
        let line = board.row_line(0, 80).unwrap();
        assert!(line.contains("~/.zshrc"));
        assert!(!line.contains("home:.zshrc"));
    }
}
