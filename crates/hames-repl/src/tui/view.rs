use std::time::Duration;

use ratatui::Frame;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::prelude::Stylize;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Borders, Clear, Padding, Paragraph, Scrollbar, ScrollbarOrientation,
    ScrollbarState,
};
use tachyonfx::{Effect, EffectRenderer, EffectTimer, Interpolation, fx};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::app::{
    ActivityCategory, ActivityPhase, AgentChoice, AgentEditField, AgentEditor, AgentEditorPage,
    App, ApprovalModal, Composer, ComposerUnit, DreamPhase, HitAction, HitRegion, MemoryBrowser,
    Modal, ScarBrowser, ScarEditField, ScarEditor, ScrollTarget, SheetKind, ThemeKind,
    TranscriptItem, TranscriptViewport,
};

const MINT: Color = Color::Rgb(116, 226, 192);
const MINT_LIGHT: Color = Color::Rgb(164, 239, 218);
const SKY: Color = Color::Rgb(112, 177, 255);
const SKY_LIGHT: Color = Color::Rgb(166, 207, 255);
const CYAN: Color = Color::Rgb(91, 211, 224);
const CYAN_LIGHT: Color = Color::Rgb(154, 232, 239);
const LILAC: Color = Color::Rgb(193, 154, 255);
const LILAC_LIGHT: Color = Color::Rgb(220, 194, 255);
const CORAL: Color = Color::Rgb(255, 139, 116);
const CORAL_LIGHT: Color = Color::Rgb(255, 188, 172);
const GOLD: Color = Color::Rgb(240, 190, 92);
const GOLD_LIGHT: Color = Color::Rgb(249, 218, 153);
const MUTED: Color = Color::Rgb(86, 94, 108);
const MUTED_LIGHT: Color = Color::Rgb(126, 134, 149);
const INPUT: Color = Color::Rgb(156, 164, 178);
const INPUT_LIGHT: Color = Color::Rgb(190, 197, 208);
const RULE: Color = Color::Rgb(49, 56, 69);
const RULE_LIGHT: Color = Color::Rgb(82, 90, 105);
const DELETE_BG: Color = Color::Rgb(78, 31, 39);
const ADDITION_BG: Color = Color::Rgb(20, 50, 40);
const REMOVAL_BG: Color = Color::Rgb(58, 31, 36);
const PANEL: Color = Color::Rgb(19, 23, 31);
const PANEL_BRIGHT: Color = Color::Rgb(29, 35, 46);
const TEXT_IDLE: Duration = Duration::from_millis(350);
const TEXT_SWEEP: Duration = Duration::from_millis(2_200);
const ACTIVITY_IDLE: Duration = Duration::from_millis(3_500);
const ACTIVITY_SWEEP: Duration = Duration::from_millis(1_600);

pub fn draw(frame: &mut Frame<'_>, app: &mut App) {
    app.hits.clear();
    if app.modal.is_none() {
        app.modal_viewport = TranscriptViewport::default();
        app.clear_modal_selection();
    }
    let area = frame.area();
    frame.render_widget(
        Block::default().style(Style::default().bg(Color::Reset)),
        area,
    );
    if area.width < 56 || area.height < 10 {
        frame.render_widget(
            Paragraph::new(vec![
                Line::from(Span::styled("◈ Hames", Style::default().fg(MINT).bold())),
                Line::from(""),
                Line::from(Span::styled(
                    "Needs at least 56 × 10",
                    Style::default().fg(GOLD),
                )),
                Line::from(Span::styled(
                    "Resize the terminal to continue",
                    Style::default().fg(MUTED),
                )),
            ])
            .alignment(Alignment::Center),
            centered(area, 38, 7),
        );
        apply_theme(frame, area, app.theme);
        return;
    }

    let fx_delta = app.take_effect_delta();
    let header_height = 2;
    let composer_width = area.width.saturating_sub(5).max(1);
    let composer_height = composer_rows(app, composer_width).clamp(1, 8) + 2;
    let notice = app
        .copy_notice()
        .map(|message| (message.to_owned(), false))
        .or_else(|| app.error_notice.clone().map(|message| (message, true)))
        .or_else(|| app.notice.clone().map(|message| (message, false)));
    let notice_height = u16::from(notice.is_some());
    let queue_height = u16::try_from(app.queued_messages.len()).unwrap_or(2).min(2);
    let sheet_height = app
        .sheet
        .as_ref()
        .map(|sheet| (sheet.options.len() as u16 + 2).clamp(3, 9))
        .unwrap_or(0);
    let approval_height = if let Some(Modal::Approval(approval)) = &app.modal {
        let required = u16::try_from(
            approval_detail_lines(approval, usize::from(area.width.saturating_sub(1))).len() + 3,
        )
        .unwrap_or(u16::MAX);
        let available = area
            .height
            .saturating_sub(header_height + composer_height + notice_height + 2);
        required.min(available.max(3))
    } else {
        0
    };
    let tray_height = sheet_height.max(approval_height);
    let bottom = composer_height + notice_height + queue_height + tray_height + 1;
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(1),
            Constraint::Length(bottom),
        ])
        .split(area);
    render_header(frame, app, rows[0]);
    render_transcript(frame, app, rows[1], fx_delta);

    let footer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(tray_height),
            Constraint::Length(notice_height),
            Constraint::Length(queue_height),
            Constraint::Length(composer_height),
            Constraint::Length(1),
        ])
        .split(rows[2]);
    if approval_height > 0 {
        render_approval_tray(frame, app, footer[0]);
    } else if sheet_height > 0 {
        render_sheet(frame, app, footer[0]);
    }
    if let Some((notice, error)) = notice {
        frame.render_widget(
            Paragraph::new(Line::from(vec![
                Span::styled(
                    "  ◆ ",
                    Style::default().fg(if error { CORAL } else { GOLD }),
                ),
                Span::styled(
                    notice,
                    Style::default().fg(if error { CORAL } else { MUTED }),
                ),
            ])),
            footer[1],
        );
    }
    render_queue(frame, app, footer[2]);
    render_composer(frame, app, footer[3]);
    render_status_bar(frame, app, footer[4], fx_delta);
    render_modal(frame, app, area);
    apply_theme(frame, area, app.theme);
}

fn render_header(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let activity = current_activity(app);
    let mut left_spans = vec![
        Span::styled(" ◈ Hames", Style::default().fg(MINT).bold()),
        Span::styled(
            format!(" · {}", app.workspace_name),
            Style::default().fg(MUTED),
        ),
    ];
    if let Some(reference) = &app.git_ref {
        left_spans.push(Span::styled(
            format!(" · {reference}"),
            Style::default().fg(MUTED),
        ));
    }
    let left = Line::from(left_spans);
    let session = Line::from(vec![
        Span::styled(
            app.session.title.as_deref().unwrap_or("New session"),
            Style::default().fg(INPUT).bold(),
        ),
        Span::styled(" · ", Style::default().fg(MUTED)),
        Span::styled(
            format!("{activity}  "),
            Style::default().fg(if app.active_run.is_some() {
                GOLD
            } else {
                MUTED
            }),
        ),
    ])
    .right_aligned();
    let block = Block::default()
        .borders(Borders::BOTTOM)
        .border_style(Style::default().fg(Color::Rgb(54, 63, 78)));
    let right_start = area.width / 2;
    frame.render_widget(
        Paragraph::new(left),
        Rect::new(area.x, area.y, right_start.max(1), 1),
    );
    frame.render_widget(block, area);
    frame.render_widget(
        Paragraph::new(session),
        Rect::new(
            area.x + right_start,
            area.y,
            area.width.saturating_sub(right_start),
            1,
        ),
    );
    app.hits.push(HitRegion {
        x: area.x,
        y: area.y,
        width: area.width,
        height: 1,
        action: HitAction::ShowSession,
    });
}

fn render_queue(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    if area.height == 0 {
        return;
    }
    let width = usize::from(area.width.saturating_sub(5));
    let total = app.queued_messages.len();
    let lines = app
        .queued_messages
        .iter()
        .enumerate()
        .map(|(index, item)| {
            let content = item
                .content
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            let prefix = format!("  Queued {}/{}  ", index + 1, total);
            let body_width = width.saturating_sub(prefix.width());
            Line::from(vec![
                Span::styled(prefix, Style::default().fg(INPUT)),
                Span::styled(fit(&content, body_width), Style::default().fg(MUTED)),
            ])
        })
        .collect::<Vec<_>>();
    frame.render_widget(Paragraph::new(lines), area);
    for (index, item) in app.queued_messages.iter().enumerate() {
        app.hits.push(HitRegion {
            x: area.x,
            y: area.y.saturating_add(u16::try_from(index).unwrap_or(0)),
            width: area.width,
            height: 1,
            action: HitAction::QueuedMessage(item.id.clone()),
        });
    }
}

pub(super) fn current_activity(app: &App) -> &'static str {
    if app.active_run.is_none() {
        return "Ready";
    }
    for item in app.transcript.iter().rev() {
        match item {
            TranscriptItem::Compaction { live: true, .. } => return "Compacting",
            TranscriptItem::Thought { live: true, .. } => return "Thinking",
            TranscriptItem::Assistant { live: true, .. } => return "Responding",
            TranscriptItem::Activity { rows, .. } => {
                if let Some(row) = rows.iter().rev().find(|row| !row.phase.terminal()) {
                    return match row.category() {
                        ActivityCategory::Explore => "Exploring",
                        ActivityCategory::Change => "Writing",
                        ActivityCategory::Run => "Running",
                        ActivityCategory::Delegate => "Delegating",
                        ActivityCategory::Skills => "Skills",
                        ActivityCategory::Memory => "Memory",
                        ActivityCategory::Scars => "Scars",
                        ActivityCategory::Plugin => "Plugin",
                    };
                }
            }
            _ => {}
        }
    }
    "Working"
}

struct RenderLine<'a> {
    line: Line<'a>,
    thought: Option<TranscriptDisclosure>,
    sheen: Option<(u16, u16)>,
}

#[derive(Clone, Copy)]
enum TranscriptDisclosure {
    Thought(usize),
    Activity(usize),
    Compaction(usize),
}

fn render_transcript(frame: &mut Frame<'_>, app: &mut App, area: Rect, fx_delta: Duration) {
    let width = usize::from(area.width.saturating_sub(3).max(20));
    let lines = transcript_lines(app, width);
    let height = usize::from(area.height);
    let bottom_start = lines.len().saturating_sub(height);
    let start = bottom_start.saturating_sub(app.scroll.min(bottom_start));
    let end = (start + height).min(lines.len());
    app.transcript_viewport = TranscriptViewport {
        x: area.x,
        y: area.y,
        width: area.width.saturating_sub(1),
        height: area.height,
        lines: lines[start..end]
            .iter()
            .map(|item| line_text(&item.line))
            .collect(),
    };
    let visible: Vec<Line<'_>> = lines[start..end]
        .iter()
        .enumerate()
        .map(|(row, item)| {
            app.transcript_selection_range(row).map_or_else(
                || item.line.clone(),
                |range| highlight_line(&item.line, range),
            )
        })
        .collect();
    frame.render_widget(Paragraph::new(visible), area);
    let sheen_regions = lines[start..end]
        .iter()
        .enumerate()
        .filter_map(|(row, item)| {
            item.sheen.map(|(x, width)| {
                Rect::new(
                    area.x.saturating_add(x),
                    area.y.saturating_add(u16::try_from(row).unwrap_or(0)),
                    width.min(area.width.saturating_sub(x)),
                    1,
                )
            })
        })
        .filter(|region| region.width > 0)
        .collect::<Vec<_>>();
    if sheen_regions.is_empty() {
        app.transcript_sheen_effect = None;
    } else {
        let effect = app
            .transcript_sheen_effect
            .get_or_insert_with(|| traveling_sheen(TEXT_IDLE, TEXT_SWEEP, None));
        for (index, region) in sheen_regions.into_iter().enumerate() {
            frame.render_effect(
                effect,
                region,
                if index == 0 { fx_delta } else { Duration::ZERO },
            );
        }
    }
    for (offset, item) in lines[start..end].iter().enumerate() {
        if let Some(disclosure) = item.thought {
            let action = match disclosure {
                TranscriptDisclosure::Thought(index) => HitAction::ToggleThought(index),
                TranscriptDisclosure::Activity(index) => HitAction::ToggleActivity(index),
                TranscriptDisclosure::Compaction(index) => HitAction::ToggleActivity(index),
            };
            app.hits.push(HitRegion {
                x: area.x,
                y: area.y + u16::try_from(offset).unwrap_or(0),
                width: area.width.saturating_sub(1),
                height: 1,
                action,
            });
        }
    }
    if lines.len() > height {
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(None)
            .end_symbol(None)
            .track_symbol(Some("░"))
            .thumb_symbol("█")
            .track_style(Style::default().fg(RULE))
            .thumb_style(Style::default().fg(INPUT));
        let mut state = ScrollbarState::new(lines.len())
            .position(scrollbar_position(start, lines.len(), height))
            .viewport_content_length(height);
        frame.render_stateful_widget(scrollbar, area, &mut state);
        app.hits.push(HitRegion {
            x: area.x.saturating_add(area.width.saturating_sub(1)),
            y: area.y,
            width: 1,
            height: area.height,
            action: HitAction::Scrollbar {
                target: ScrollTarget::Transcript,
                content_len: lines.len(),
                viewport_len: height,
            },
        });
    }
}

fn traveling_sheen(idle: Duration, sweep: Duration, highlight_color: Option<Color>) -> Effect {
    let sweep_ms = u32::try_from(sweep.as_millis()).unwrap_or(u32::MAX);
    let highlight = fx::effect_fn(
        (),
        EffectTimer::from_ms(sweep_ms, Interpolation::SineInOut),
        move |_, context, cells| {
            let travel = f32::from(
                context
                    .area
                    .width
                    .saturating_add(context.area.height / 3)
                    .saturating_add(8),
            );
            let center = context.alpha() * travel - 4.0;
            for (position, cell) in cells {
                if cell.symbol().trim().is_empty() {
                    continue;
                }
                let coordinate = f32::from(position.x.saturating_sub(context.area.x))
                    + f32::from(position.y.saturating_sub(context.area.y)) / 3.0;
                let distance = (coordinate - center).abs();
                if distance < 1.35 {
                    cell.set_fg(highlight_color.unwrap_or_else(|| lighter_color(cell.fg)));
                }
            }
        },
    );
    fx::repeating(fx::sequence(&[fx::sleep(idle), highlight]))
}

fn lighter_color(color: Color) -> Color {
    match color {
        MINT => MINT_LIGHT,
        SKY => SKY_LIGHT,
        LILAC => LILAC_LIGHT,
        CORAL => CORAL_LIGHT,
        GOLD => GOLD_LIGHT,
        MUTED => MUTED_LIGHT,
        INPUT => INPUT_LIGHT,
        RULE => RULE_LIGHT,
        value => value,
    }
}

fn transcript_lines(app: &App, width: usize) -> Vec<RenderLine<'static>> {
    let mut lines = Vec::new();
    for (index, item) in app.transcript.iter().enumerate() {
        match item {
            TranscriptItem::User { content } => {
                lines.push(RenderLine {
                    line: Line::from(Span::styled("You", Style::default().fg(INPUT).bold())),
                    thought: None,
                    sheen: None,
                });
                push_markdown(
                    &mut lines,
                    content,
                    width,
                    Style::default().fg(Color::White),
                );
            }
            TranscriptItem::Thought {
                content,
                duration_seconds,
                interrupted,
                live,
                collapsed,
                ..
            } => {
                let interactive = !(*interrupted && !*live && content.is_empty());
                let label = if *live {
                    let mut spans = vec![Span::styled(
                        "◈ Thinking",
                        Style::default().fg(INPUT).bold(),
                    )];
                    if interactive {
                        spans.push(Span::styled(
                            if *collapsed { "  ▸" } else { "  ▾" },
                            Style::default().fg(MUTED),
                        ));
                    }
                    Line::from(spans)
                } else {
                    let mut spans = vec![
                        Span::styled("◈ ", Style::default().fg(MUTED)),
                        Span::styled(
                            thought_label(*duration_seconds),
                            Style::default().fg(INPUT).bold(),
                        ),
                    ];
                    if interactive {
                        spans.push(Span::styled(
                            if *collapsed { "  ▸" } else { "  ▾" },
                            Style::default().fg(MUTED),
                        ));
                    }
                    Line::from(spans)
                };
                lines.push(RenderLine {
                    line: if app.focused_thought == Some(index) {
                        label.style(Style::default().bg(PANEL_BRIGHT))
                    } else {
                        label
                    },
                    thought: interactive.then_some(TranscriptDisclosure::Thought(index)),
                    sheen: live.then_some((0, 10)),
                });
                if !*collapsed && !content.is_empty() {
                    push_wrapped(
                        &mut lines,
                        content,
                        width,
                        "  ",
                        Style::default().fg(Color::Rgb(174, 180, 192)),
                    );
                }
            }
            TranscriptItem::Assistant { content, live, .. } if !content.trim().is_empty() => {
                lines.push(RenderLine {
                    line: Line::from(Span::styled("✦ Hames", Style::default().fg(MINT).bold())),
                    thought: None,
                    sheen: live.then_some((0, 7)),
                });
                push_markdown(
                    &mut lines,
                    content,
                    width,
                    Style::default().fg(Color::White),
                );
            }
            TranscriptItem::Activity {
                rows, collapsed, ..
            } => {
                let visible_rows = rows
                    .iter()
                    .filter(|row| !row.name.is_empty())
                    .collect::<Vec<_>>();
                if visible_rows.is_empty() {
                    continue;
                }
                let failed = visible_rows.iter().any(|row| {
                    matches!(
                        row.phase,
                        ActivityPhase::Failed | ActivityPhase::Rejected | ActivityPhase::Cancelled
                    )
                });
                let complete = visible_rows.iter().all(|row| row.phase.terminal());
                let state = if failed {
                    "Attention"
                } else if complete {
                    "Completed"
                } else {
                    "Working"
                };
                let count = visible_rows.len();
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled("◆ ", Style::default().fg(INPUT)),
                        Span::styled("Work", Style::default().fg(INPUT).bold()),
                        Span::styled(
                            format!(
                                " · {count} {} · {state}  {}",
                                if count == 1 { "action" } else { "actions" },
                                if *collapsed { "▸" } else { "▾" }
                            ),
                            Style::default().fg(MUTED),
                        ),
                    ]),
                    thought: Some(TranscriptDisclosure::Activity(index)),
                    sheen: None,
                });
                if *collapsed {
                    continue;
                }
                for row in visible_rows {
                    let glyph = match row.phase {
                        ActivityPhase::Preparing => "·",
                        ActivityPhase::Checking | ActivityPhase::Approval => "○",
                        ActivityPhase::Running => "◐",
                        ActivityPhase::Completed => "✓",
                        ActivityPhase::Failed | ActivityPhase::Cancelled => "×",
                        ActivityPhase::Rejected => "!",
                    };
                    let color = phase_color(row.phase);
                    let target = row.target();
                    let mut detail = target.clone();
                    let summary_is_redundant = row.name == "memory_forget"
                        && row
                            .arguments
                            .get("memory_id")
                            .and_then(serde_json::Value::as_str)
                            .is_some_and(|id| row.summary.contains(id));
                    if !row.summary.is_empty() && row.summary != detail && !summary_is_redundant {
                        detail.push_str(" · ");
                        detail.push_str(&row.summary.replace('\n', " "));
                    }
                    let prefix = format!("  {glyph} {}  ", row.verb());
                    let body_width = width.saturating_sub(UnicodeWidthStr::width(prefix.as_str()));
                    let fitted = fit(&detail, body_width);
                    let active = matches!(
                        row.phase,
                        ActivityPhase::Preparing | ActivityPhase::Checking | ActivityPhase::Running
                    );
                    let mut spans = vec![Span::styled(
                        format!("  {glyph} {}  ", row.verb()),
                        Style::default().fg(color),
                    )];
                    if row
                        .arguments
                        .get("path")
                        .and_then(serde_json::Value::as_str)
                        .is_some()
                    {
                        if let Some(remainder) = fitted.strip_prefix(&target) {
                            spans.push(Span::styled(target, Style::default().fg(SKY)));
                            spans.push(Span::styled(
                                remainder.to_owned(),
                                Style::default().fg(MUTED),
                            ));
                        } else {
                            spans.push(Span::styled(fitted, Style::default().fg(SKY)));
                        }
                    } else {
                        spans.push(Span::styled(fitted, Style::default().fg(MUTED)));
                    }
                    let line = Line::from(spans);
                    lines.push(RenderLine {
                        line,
                        thought: None,
                        sheen: active
                            .then_some((4, u16::try_from(row.verb().width()).unwrap_or(0))),
                    });
                    if row.phase == ActivityPhase::Completed
                        && !row.content.is_empty()
                        && (matches!(row.name.as_str(), "edit_file" | "write_file")
                            || looks_like_unified_diff(&row.content))
                    {
                        push_diff(&mut lines, &row.content, width, row.truncated);
                    }
                }
            }
            TranscriptItem::Dream { phase, detail, .. } => {
                let (glyph, color) = match phase {
                    DreamPhase::Queued => ("·", MUTED),
                    DreamPhase::Running => ("○", INPUT),
                    DreamPhase::Paused => ("·", MUTED),
                    DreamPhase::Completed => ("✓", MUTED),
                    DreamPhase::Failed => ("!", CORAL),
                };
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled("☾ ", Style::default().fg(MUTED)),
                        Span::styled("Dream", Style::default().fg(INPUT).bold()),
                    ]),
                    thought: None,
                    sheen: None,
                });
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled(format!("  {glyph} "), Style::default().fg(color)),
                        Span::styled(detail.clone(), Style::default().fg(MUTED)),
                    ]),
                    thought: None,
                    sheen: None,
                });
            }
            TranscriptItem::Compaction {
                summary,
                provider,
                model,
                trigger,
                turns_compacted,
                before_tokens,
                after_tokens,
                passes,
                partial,
                live,
                collapsed,
                ..
            } => {
                let title = if *live {
                    "Compacting context".to_owned()
                } else {
                    format!(
                        "Compacted context · {turns_compacted} turns · {} → {}",
                        format_token_count(*before_tokens),
                        format_token_count(*after_tokens)
                    )
                };
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled("◆ ", Style::default().fg(INPUT)),
                        Span::styled(title, Style::default().fg(INPUT).bold()),
                        Span::styled(
                            if *live {
                                String::new()
                            } else if *collapsed {
                                "  ▸".to_owned()
                            } else {
                                "  ▾".to_owned()
                            },
                            Style::default().fg(MUTED),
                        ),
                    ]),
                    thought: (!*live).then_some(TranscriptDisclosure::Compaction(index)),
                    sheen: live.then_some((2, 18)),
                });
                if !*live && !*collapsed {
                    push_wrapped(
                        &mut lines,
                        &format!(
                            "{} / {} · {} · {} {}{}",
                            provider,
                            model,
                            trigger,
                            passes,
                            if *passes == 1 { "pass" } else { "passes" },
                            if *partial { " · more remains" } else { "" }
                        ),
                        width,
                        "  ",
                        Style::default().fg(MUTED),
                    );
                    push_markdown(
                        &mut lines,
                        summary,
                        width,
                        Style::default().fg(Color::White),
                    );
                }
            }
            TranscriptItem::Worked {
                duration_seconds, ..
            } => {
                let elapsed = format_elapsed(duration_seconds.round().max(0.0) as u64);
                let label = format!("Worked for {elapsed} ");
                let used = 2 + UnicodeWidthStr::width(label.as_str());
                lines.push(RenderLine {
                    line: Line::from(""),
                    thought: None,
                    sheen: None,
                });
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled("─ ", Style::default().fg(RULE)),
                        Span::styled(label, Style::default().fg(MUTED)),
                        Span::styled(
                            "─".repeat(width.saturating_sub(used)),
                            Style::default().fg(RULE),
                        ),
                    ]),
                    thought: None,
                    sheen: None,
                });
            }
            TranscriptItem::Assistant { .. } => {}
            TranscriptItem::Status { text, error } => {
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled(
                            if *error { "× " } else { "◆ " },
                            Style::default().fg(if *error { CORAL } else { GOLD }),
                        ),
                        Span::styled(
                            text.clone(),
                            Style::default().fg(if *error { CORAL } else { MUTED }),
                        ),
                    ]),
                    thought: None,
                    sheen: None,
                });
            }
        }
        let compact_tool_handoff = (matches!(item, TranscriptItem::Assistant { .. })
            && matches!(
                app.transcript.get(index + 1),
                Some(TranscriptItem::Activity { .. })
            ))
            || matches!(
                app.transcript.get(index + 1),
                Some(TranscriptItem::Worked { .. })
            );
        if !compact_tool_handoff {
            lines.push(RenderLine {
                line: Line::from(""),
                thought: None,
                sheen: None,
            });
        }
    }
    if lines.is_empty() {
        lines.extend([
            RenderLine {
                line: Line::from(Span::styled(
                    "  A fresh canvas.",
                    Style::default().fg(MUTED),
                )),
                thought: None,
                sheen: None,
            },
            RenderLine {
                line: Line::from(Span::styled(
                    "  Ask Hames to explore, change, or run something.",
                    Style::default().fg(MUTED),
                )),
                thought: None,
                sheen: None,
            },
        ]);
    }
    lines
}

fn render_sheet(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let Some(sheet) = &app.sheet else {
        return;
    };
    let inner_height = usize::from(area.height.saturating_sub(2));
    let start = sheet
        .selected
        .saturating_add(1)
        .saturating_sub(inner_height);
    let command_tray = sheet.kind == crate::tui::app::SheetKind::Commands;
    let command_query = app
        .composer
        .text()
        .strip_prefix('/')
        .unwrap_or_default()
        .to_ascii_lowercase();
    let mut lines = Vec::new();
    for (offset, option) in sheet
        .options
        .iter()
        .enumerate()
        .skip(start)
        .take(inner_height)
    {
        let selected = offset == sheet.selected;
        let deleting = sheet.pending_delete == Some(offset);
        let row_style = if deleting {
            Style::default().bg(DELETE_BG)
        } else if selected {
            Style::default().bg(PANEL_BRIGHT)
        } else {
            Style::default()
        };
        let mut spans = vec![Span::styled(
            if selected { "  •" } else { "   " },
            Style::default()
                .fg(if deleting {
                    CORAL
                } else if selected {
                    INPUT
                } else {
                    MUTED
                })
                .patch(row_style),
        )];
        if deleting {
            let prompt = " Press Ctrl+D again to delete this entry";
            let used = 3 + UnicodeWidthStr::width(prompt);
            spans.extend([
                Span::styled(prompt, Style::default().fg(CORAL).bold().patch(row_style)),
                Span::styled(
                    " ".repeat(usize::from(area.width).saturating_sub(used)),
                    row_style,
                ),
            ]);
            lines.push(Line::from(spans));
            app.hits.push(HitRegion {
                x: area.x,
                y: area.y + 1 + u16::try_from(offset - start).unwrap_or(0),
                width: area.width,
                height: 1,
                action: HitAction::SelectSheet(offset),
            });
            continue;
        }
        let label_field = format!(" {:<20}", option.label);
        if command_tray {
            spans.extend(command_label_spans(
                &option.label,
                &command_query,
                selected,
                row_style,
            ));
        } else {
            spans.push(Span::styled(
                label_field.clone(),
                Style::default()
                    .fg(sheet_text_color(app.theme))
                    .add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    })
                    .patch(row_style),
            ));
        }
        spans.push(Span::styled(
            format!(" {}", option.detail),
            Style::default().fg(MUTED).patch(row_style),
        ));
        let used = 4
            + UnicodeWidthStr::width(label_field.as_str())
            + UnicodeWidthStr::width(option.detail.as_str());
        spans.push(Span::styled(
            " ".repeat(usize::from(area.width).saturating_sub(used)),
            row_style,
        ));
        lines.push(Line::from(spans));
        app.hits.push(HitRegion {
            x: area.x,
            y: area.y + 1 + u16::try_from(offset - start).unwrap_or(0),
            width: area.width,
            height: 1,
            action: HitAction::SelectSheet(offset),
        });
    }
    let rule = Style::default().fg(RULE);
    let title = (!command_tray).then(|| {
        Line::from(vec![
            Span::styled("─ ", rule),
            Span::styled(sheet.title.clone(), Style::default().fg(INPUT).bold()),
            Span::styled(" ─", rule),
        ])
    });
    let mut block = Block::default()
        .borders(Borders::TOP | Borders::BOTTOM)
        .border_style(rule);
    if let Some(title) = title {
        block = block.title(title);
    }
    frame.render_widget(Paragraph::new(lines).block(block), area);
}

fn command_label_spans(
    label: &str,
    query: &str,
    selected: bool,
    row_style: Style,
) -> Vec<Span<'static>> {
    let field = format!(" {label:<20}");
    let base = Style::default()
        .fg(INPUT)
        .add_modifier(if selected {
            Modifier::BOLD
        } else {
            Modifier::empty()
        })
        .patch(row_style);
    if query.is_empty() {
        return vec![Span::styled(field, base)];
    }
    let lower = field.to_ascii_lowercase();
    let Some(start) = lower.find(query) else {
        return vec![Span::styled(field, base)];
    };
    let end = start + query.len();
    vec![
        Span::styled(field[..start].to_owned(), base),
        Span::styled(field[start..end].to_owned(), base.fg(MINT).bold()),
        Span::styled(field[end..].to_owned(), base),
    ]
}

fn render_composer(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let mode = match app.session.interaction_mode.as_str() {
        "manual" => "Manual",
        "plan" => "Plan",
        _ => "Auto",
    };
    let reasoning = if app.session.reasoning_effort.is_empty() {
        "default"
    } else {
        &app.session.reasoning_effort
    };
    let accent = mode_color(&app.session.interaction_mode);
    let title = Line::from(vec![
        Span::styled(
            "─ ",
            Style::default().fg(mode_outline(&app.session.interaction_mode)),
        ),
        Span::styled(
            format!("{} ({reasoning})", app.session.model),
            Style::default().fg(MUTED).bold(),
        ),
        Span::styled(" · ", Style::default().fg(MUTED)),
        Span::styled(mode, Style::default().fg(accent).bold()),
        Span::styled(
            " ─",
            Style::default().fg(mode_outline(&app.session.interaction_mode)),
        ),
    ])
    .right_aligned();
    let block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .padding(Padding::horizontal(1))
        .border_style(Style::default().fg(mode_outline(&app.session.interaction_mode)));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.push(HitRegion {
        x: area.x,
        y: area.y,
        width: area.width,
        height: area.height,
        action: HitAction::FocusComposer,
    });
    let content_width = inner.width.saturating_sub(1).max(1);
    let content_area = Rect::new(inner.x, inner.y, content_width, inner.height);
    let (lines, cursor_x, cursor_y) = composer_lines(app, usize::from(content_width));
    let available = usize::from(inner.height);
    let automatic_start = cursor_y
        .saturating_add(1)
        .saturating_sub(available)
        .min(lines.len().saturating_sub(available));
    let start = app
        .composer_scroll
        .unwrap_or(automatic_start)
        .min(lines.len().saturating_sub(available));
    let end = (start + available).min(lines.len());
    frame.render_widget(Paragraph::new(lines[start..end].to_vec()), content_area);
    if lines.len() > available {
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(None)
            .end_symbol(None)
            .track_symbol(Some("░"))
            .thumb_symbol("█")
            .track_style(Style::default().fg(RULE))
            .thumb_style(Style::default().fg(INPUT));
        let mut state = ScrollbarState::new(lines.len())
            .position(scrollbar_position(start, lines.len(), available))
            .viewport_content_length(available);
        frame.render_stateful_widget(scrollbar, inner, &mut state);
        app.hits.push(HitRegion {
            x: inner.x.saturating_add(inner.width.saturating_sub(1)),
            y: inner.y,
            width: 1,
            height: inner.height,
            action: HitAction::Scrollbar {
                target: ScrollTarget::Composer,
                content_len: lines.len(),
                viewport_len: available,
            },
        });
    }
    if app.modal.is_none() {
        let adjusted_y = cursor_y.saturating_sub(start);
        if adjusted_y < available {
            frame.set_cursor_position((
                inner.x
                    + u16::try_from(cursor_x)
                        .unwrap_or(0)
                        .min(inner.width.saturating_sub(1)),
                inner.y + u16::try_from(adjusted_y).unwrap_or(0),
            ));
        }
    }
}

fn composer_lines(app: &App, width: usize) -> (Vec<Line<'static>>, usize, usize) {
    if app.composer.units.is_empty() {
        return (
            vec![Line::from(vec![
                Span::styled("❯ ", Style::default().fg(INPUT).bold()),
                Span::styled("Message Hames…", Style::default().fg(MUTED)),
            ])],
            2,
            0,
        );
    }
    let mut rows: Vec<Vec<Span<'static>>> =
        vec![vec![Span::styled("❯ ", Style::default().fg(INPUT).bold())]];
    let mut x = 2;
    let mut cursor = (2, 0);
    for (index, unit) in app.composer.units.iter().enumerate() {
        if index == app.composer.cursor {
            cursor = (x, rows.len() - 1);
        }
        let (display, style) = match unit {
            ComposerUnit::Text(value) if value == "\n" => {
                rows.push(vec![Span::raw("  ")]);
                x = 2;
                continue;
            }
            ComposerUnit::Text(value) => (value.clone(), Style::default().fg(INPUT)),
            ComposerUnit::Paste(value) => (
                paste_capsule(value),
                Style::default().fg(Color::Black).bg(LILAC).bold(),
            ),
        };
        let token_width = UnicodeWidthStr::width(display.as_str());
        if x > 0 && x + token_width > width {
            rows.push(vec![Span::raw("  ")]);
            x = 2;
            if index == app.composer.cursor {
                cursor = (2, rows.len() - 1);
            }
        }
        rows.last_mut()
            .expect("composer row")
            .push(Span::styled(display, style));
        x += token_width;
    }
    if app.composer.cursor == app.composer.units.len() {
        cursor = (x, rows.len() - 1);
    }
    (
        rows.into_iter().map(Line::from).collect(),
        cursor.0,
        cursor.1,
    )
}

fn scrollbar_position(top: usize, content_len: usize, viewport_len: usize) -> usize {
    let max_top = content_len.saturating_sub(viewport_len);
    if max_top == 0 {
        return 0;
    }
    top.min(max_top)
        .saturating_mul(content_len.saturating_sub(1))
        / max_top
}

fn render_status_bar(frame: &mut Frame<'_>, app: &mut App, area: Rect, fx_delta: Duration) {
    let left = if matches!(app.modal, Some(Modal::Approval(_))) {
        Line::from(vec![
            Span::styled("  ←→", Style::default().fg(INPUT).bold()),
            Span::styled(" choose · ", Style::default().fg(MUTED)),
            Span::styled("PgUp/PgDn", Style::default().fg(INPUT).bold()),
            Span::styled(" details · ", Style::default().fg(MUTED)),
            Span::styled("Enter", Style::default().fg(INPUT).bold()),
            Span::styled(" confirm · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" deny", Style::default().fg(MUTED)),
        ])
    } else if app.sheet.is_some() {
        sheet_shortcuts(app)
    } else if app.active_run.is_some() {
        activity_bar(app)
    } else {
        Line::from(Span::styled(
            "  Shift+Tab mode · Ctrl+K commands",
            Style::default().fg(MUTED),
        ))
    };
    frame.render_widget(Paragraph::new(left), area);
    if app.sheet.is_none()
        && app.active_run.is_some()
        && !matches!(app.modal, Some(Modal::Approval(_)))
    {
        let effect = app
            .activity_bar_effect
            .get_or_insert_with(|| traveling_sheen(ACTIVITY_IDLE, ACTIVITY_SWEEP, Some(MINT)));
        frame.render_effect(
            effect,
            Rect::new(area.x.saturating_add(2), area.y, 6.min(area.width), 1),
            fx_delta,
        );
    } else {
        app.activity_bar_effect = None;
    }
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled("[", Style::default().fg(MUTED)),
            Span::styled("connected", Style::default().fg(MINT)),
            Span::styled("]  ", Style::default().fg(MUTED)),
        ]))
        .alignment(Alignment::Right),
        area,
    );
}

fn sheet_shortcuts(app: &App) -> Line<'static> {
    let Some(sheet) = &app.sheet else {
        return Line::default();
    };
    if matches!(
        sheet.kind,
        SheetKind::Sessions | SheetKind::Agents | SheetKind::Queue
    ) && sheet.pending_delete.is_some()
    {
        return Line::from(vec![
            Span::styled("  ↑↓", Style::default().fg(INPUT).bold()),
            Span::styled(" cancel · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" close", Style::default().fg(MUTED)),
        ]);
    }
    let action = match sheet.kind {
        SheetKind::Commands => "open",
        SheetKind::Sessions => "resume",
        SheetKind::Queue => "edit",
        _ => "select",
    };
    let mut spans = vec![
        Span::styled("  ↑↓", Style::default().fg(INPUT).bold()),
        Span::styled(" navigate · ", Style::default().fg(MUTED)),
        Span::styled("Enter", Style::default().fg(INPUT).bold()),
        Span::styled(format!(" {action} · "), Style::default().fg(MUTED)),
    ];
    if sheet.kind == SheetKind::Sessions {
        spans.extend([
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" remove · ", Style::default().fg(MUTED)),
        ]);
    } else if sheet.kind == SheetKind::Queue {
        spans.extend([
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" delete · ", Style::default().fg(MUTED)),
        ]);
    } else if sheet.kind == SheetKind::Agents {
        spans.extend([
            Span::styled("Ctrl+N", Style::default().fg(INPUT).bold()),
            Span::styled(" new · ", Style::default().fg(MUTED)),
        ]);
        let selected_is_default = sheet
            .options
            .get(sheet.selected)
            .is_some_and(|option| matches!(&option.action, super::app::MenuAction::SetAgent(id) if id == "default"));
        if !selected_is_default {
            spans.extend([
                Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
                Span::styled(" delete · ", Style::default().fg(MUTED)),
            ]);
        }
    }
    spans.extend([
        Span::styled("Esc", Style::default().fg(INPUT).bold()),
        Span::styled(" close", Style::default().fg(MUTED)),
    ]);
    Line::from(spans)
}

fn activity_bar(app: &App) -> Line<'static> {
    let mut spans = vec![
        Span::raw("  "),
        Span::styled("──────", Style::default().fg(MUTED)),
    ];
    spans.push(Span::styled(
        format!("  {}", current_activity(app)),
        Style::default().fg(INPUT),
    ));
    let elapsed = app
        .run_started_at
        .map(|started| format_elapsed(started.elapsed().as_secs()))
        .unwrap_or_else(|| "0s".to_owned());
    let queue_hint = if app.queued_messages.len() >= 2 {
        "Queue full 2/2"
    } else {
        "Enter queue · Alt+↑ now · ↑ edit"
    };
    spans.push(Span::styled(
        format!(" · {elapsed} · {queue_hint} · Esc interrupt"),
        Style::default().fg(MUTED),
    ));
    Line::from(spans)
}

fn line_text(line: &Line<'_>) -> String {
    line.spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect()
}

fn highlight_line(line: &Line<'_>, (start, end): (usize, usize)) -> Line<'static> {
    let mut column = 0;
    let mut spans = Vec::new();
    for span in &line.spans {
        for grapheme in span.content.graphemes(true) {
            let next = column + UnicodeWidthStr::width(grapheme);
            let selected = next > start && column < end;
            let style = if selected {
                span.style.fg(Color::White).bg(PANEL_BRIGHT)
            } else {
                span.style
            };
            spans.push(Span::styled(grapheme.to_owned(), style));
            column = next;
        }
    }
    Line::from(spans).style(line.style)
}

fn format_elapsed(seconds: u64) -> String {
    match seconds {
        0..=59 => format!("{seconds}s"),
        60..=3599 => format!("{}m {:02}s", seconds / 60, seconds % 60),
        _ => format!(
            "{}h {:02}m {:02}s",
            seconds / 3600,
            (seconds % 3600) / 60,
            seconds % 60
        ),
    }
}

fn format_token_count(tokens: u64) -> String {
    if tokens >= 10_000 {
        format!("{}k", tokens / 1_000)
    } else if tokens >= 1_000 {
        format!("{:.1}k", tokens as f64 / 1_000.0)
    } else {
        tokens.to_string()
    }
}

fn composer_rows(app: &App, width: u16) -> u16 {
    let (lines, _, _) = composer_lines(app, usize::from(width.max(1)));
    u16::try_from(lines.len()).unwrap_or(u16::MAX)
}

fn complete_wrapped_lines(value: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut lines = Vec::new();
    for raw in value.lines().chain(value.is_empty().then_some("")) {
        let mut remaining = raw.trim();
        if remaining.is_empty() {
            lines.push(String::new());
            continue;
        }
        while !remaining.is_empty() {
            let (part, rest) = split_width(remaining, width);
            lines.push(part.trim_end().to_owned());
            remaining = rest.trim_start();
        }
    }
    lines
}

fn push_approval_field(
    lines: &mut Vec<Line<'static>>,
    label: &str,
    value: &str,
    width: usize,
    style: Style,
) {
    let label_width = UnicodeWidthStr::width(label);
    let content_width = width.saturating_sub(label_width).max(1);
    for (index, part) in complete_wrapped_lines(value, content_width)
        .into_iter()
        .enumerate()
    {
        lines.push(Line::from(vec![
            Span::styled(
                if index == 0 {
                    label.to_owned()
                } else {
                    " ".repeat(label_width)
                },
                Style::default().fg(MUTED),
            ),
            Span::styled(part, style),
        ]));
    }
}

fn approval_detail_lines(approval: &ApprovalModal, width: usize) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    push_approval_field(
        &mut lines,
        "Action  ",
        &approval.name,
        width,
        Style::default().fg(Color::White).bold(),
    );
    push_approval_field(
        &mut lines,
        "Reason  ",
        &approval.reason,
        width,
        Style::default().fg(GOLD),
    );
    lines.push(Line::from(""));
    push_approval_field(
        &mut lines,
        "Request ",
        &approval.arguments,
        width,
        Style::default().fg(INPUT),
    );
    lines.push(Line::from(""));
    lines
}

fn render_approval_tray(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let Some(Modal::Approval(approval)) = app.modal.clone() else {
        return;
    };
    let detail_lines = approval_detail_lines(&approval, usize::from(area.width.saturating_sub(1)));
    let choices = if approval.allow_session {
        [" Allow session ", " Allow once ", " Deny "].as_slice()
    } else {
        [" Allow once ", " Deny "].as_slice()
    };
    let mut spans = vec![Span::raw("  ")];
    let mut action_x = 2u16;
    let action_y = area.y.saturating_add(area.height.saturating_sub(2));
    for (index, choice) in choices.iter().enumerate() {
        let selected = approval.selected == index;
        spans.push(Span::styled(
            *choice,
            if selected {
                Style::default()
                    .fg(Color::Black)
                    .bg(if index + 1 == choices.len() {
                        CORAL
                    } else {
                        MINT
                    })
                    .bold()
            } else {
                Style::default().fg(Color::White).bg(PANEL_BRIGHT)
            },
        ));
        app.hits.push(HitRegion {
            x: area.x.saturating_add(action_x),
            y: action_y,
            width: u16::try_from(UnicodeWidthStr::width(*choice)).unwrap_or(0),
            height: 1,
            action: HitAction::Approval(index),
        });
        action_x = action_x
            .saturating_add(u16::try_from(UnicodeWidthStr::width(*choice)).unwrap_or(0))
            .saturating_add(2);
        spans.push(Span::raw("  "));
    }
    let inner_height = usize::from(area.height.saturating_sub(2));
    let detail_height = inner_height.saturating_sub(1);
    let max_top = detail_lines.len().saturating_sub(detail_height);
    let top = approval.detail_scroll.min(max_top);
    let mut lines = detail_lines
        .iter()
        .skip(top)
        .take(detail_height)
        .cloned()
        .collect::<Vec<_>>();
    while lines.len() < detail_height {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(spans));
    app.modal_viewport = TranscriptViewport {
        x: area.x,
        y: area.y.saturating_add(1),
        width: area.width,
        height: area.height.saturating_sub(2),
        lines: lines.iter().map(line_text).collect(),
    };
    let block = Block::default()
        .title(Line::from(vec![
            Span::styled("─ ", Style::default().fg(RULE)),
            Span::styled("Permission required", Style::default().fg(INPUT).bold()),
            Span::styled(" ─", Style::default().fg(RULE)),
        ]))
        .borders(Borders::TOP | Borders::BOTTOM)
        .border_style(Style::default().fg(RULE));
    frame.render_widget(
        Paragraph::new(lines)
            .block(block)
            .style(Style::default().bg(Color::Reset)),
        area,
    );
    if max_top > 0 && detail_height > 0 {
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(None)
            .end_symbol(None)
            .track_symbol(Some("░"))
            .thumb_symbol("█")
            .track_style(Style::default().fg(RULE))
            .thumb_style(Style::default().fg(INPUT));
        let mut state = ScrollbarState::new(detail_lines.len())
            .position(top)
            .viewport_content_length(detail_height);
        frame.render_stateful_widget(
            scrollbar,
            Rect::new(
                area.x,
                area.y.saturating_add(1),
                area.width,
                u16::try_from(detail_height).unwrap_or(0),
            ),
            &mut state,
        );
    }
}

fn render_modal(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let Some(modal) = app.modal.clone() else {
        return;
    };
    if matches!(modal, Modal::Approval(_)) {
        return;
    }
    let (title, mut body, width, height) = match &modal {
        Modal::Trust => (
            "Trust this workspace",
            vec![
                Line::from(Span::styled(
                    compact_home(&app.session.working_directory),
                    Style::default().fg(INPUT).bold(),
                )),
                Line::from(""),
                Line::from(Span::styled(
                    "Hames can inspect this project after you trust it.",
                    Style::default().fg(MUTED),
                )),
                Line::from(Span::styled(
                    "Tool permissions still follow the selected execution mode.",
                    Style::default().fg(MUTED),
                )),
                Line::from(""),
                Line::from(vec![
                    Span::styled(
                        "  Trust workspace  ",
                        Style::default().fg(Color::Black).bg(MINT).bold(),
                    ),
                    Span::raw("    "),
                    Span::styled(
                        "  Quit  ",
                        Style::default().fg(Color::White).bg(PANEL_BRIGHT),
                    ),
                ]),
            ],
            68,
            9,
        ),
        Modal::Approval(_) => unreachable!("approvals render in the lower tray"),
        Modal::Help => (
            "Hames shortcuts",
            vec![
                help_line("Enter", "send"),
                help_line("Alt+Enter / Shift+Enter / Ctrl+J", "new line"),
                help_line("Ctrl+K", "command palette"),
                help_line("Shift+Tab", "cycle Manual, Auto, and Plan mode"),
                help_line("Ctrl+C", "cancel active work"),
                help_line("PgUp / wheel", "scroll transcript"),
                help_line("Enter / Space", "expand or collapse a selected Thought"),
                help_line(
                    "/new /clear /resume /compact",
                    "session continuity and context",
                ),
                help_line("/model /agent /mode", "runtime controls"),
                Line::from(""),
                Line::from(Span::styled(
                    "The palette opens session status, gateway health, usage, events, run/context, memory, Skills, Scars, and plugins.",
                    Style::default().fg(MUTED),
                )),
            ],
            78,
            14,
        ),
        Modal::Session => {
            let mut lines = vec![
                detail_line("Session", &app.session.id),
                detail_line("Workspace", &compact_home(&app.session.working_directory)),
                detail_line("Agent", &app.session.agent_id),
                detail_line(
                    "Model",
                    &format!("{} / {}", app.session.provider, app.session.model),
                ),
                detail_line("Mode", &app.session.interaction_mode),
                detail_line("Lineage", &app.session.lineage_kind),
                Line::from(""),
                Line::from(Span::styled(
                    format!("Classic REPL: /resume {}", app.session.id),
                    Style::default().fg(MINT),
                )),
            ];
            if let Some(TranscriptItem::Compaction {
                turns_compacted,
                before_tokens,
                after_tokens,
                ..
            }) = app
                .transcript
                .iter()
                .rev()
                .find(|item| matches!(item, TranscriptItem::Compaction { live: false, .. }))
            {
                lines.insert(
                    6,
                    detail_line(
                        "Compaction",
                        &format!(
                            "{turns_compacted} turns · {} → {}",
                            format_token_count(*before_tokens),
                            format_token_count(*after_tokens)
                        ),
                    ),
                );
            }
            ("Session continuity", lines, 78, 13)
        }
        Modal::Memory(browser) => ("Memory", memory_browser_body(browser), 92, 23),
        Modal::Scars(browser) => ("Scars and evolution", scar_browser_body(browser), 94, 25),
        Modal::ScarEdit(editor) => ("Edit Scar", scar_editor_body(editor), 94, 25),
        Modal::AgentEdit(editor) => ("New Agent", agent_editor_body(editor), 96, 27),
        Modal::PastePreview(value) => {
            let mut lines = vec![
                Line::from(Span::styled(
                    paste_capsule(value),
                    Style::default().fg(LILAC).bold(),
                )),
                Line::from(""),
            ];
            for line in value.lines().take(10) {
                lines.push(Line::from(Span::styled(
                    fit(line, 70),
                    Style::default().fg(Color::White),
                )));
            }
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Backspace or Delete removes the entire paste capsule.",
                Style::default().fg(MUTED),
            )));
            ("Pasted text preview", lines, 78, 16)
        }
        Modal::Error(message) => (
            "Something went wrong",
            vec![
                Line::from(Span::styled(message.clone(), Style::default().fg(CORAL))),
                Line::from(""),
                Line::from(Span::styled(
                    "Press Esc or Enter to return to the transcript.",
                    Style::default().fg(MUTED),
                )),
            ],
            72,
            7,
        ),
        Modal::Info { title, lines } => (
            title.as_str(),
            lines
                .iter()
                .map(|line| {
                    Line::from(Span::styled(
                        line.clone(),
                        Style::default().fg(Color::White),
                    ))
                })
                .collect(),
            82,
            u16::try_from(lines.len().saturating_add(4))
                .unwrap_or(20)
                .clamp(7, 22),
        ),
    };
    let popup = centered(area, width, height);
    let inner_height = usize::from(popup.height.saturating_sub(2));
    if matches!(modal, Modal::Approval(_))
        && let Some(actions) = body.pop()
    {
        let content_rows = inner_height.saturating_sub(1);
        body.truncate(content_rows);
        while body.len() < content_rows {
            body.push(Line::from(""));
        }
        body.push(actions);
    }
    app.modal_viewport = TranscriptViewport {
        x: popup.x.saturating_add(1),
        y: popup.y.saturating_add(1),
        width: popup.width.saturating_sub(2),
        height: popup.height.saturating_sub(2),
        lines: body.iter().take(inner_height).map(line_text).collect(),
    };
    let body: Vec<Line<'_>> = body
        .iter()
        .take(inner_height)
        .enumerate()
        .map(|(row, line)| {
            app.modal_selection_range(row)
                .map_or_else(|| line.clone(), |range| highlight_line(line, range))
        })
        .collect();
    let modal_background = if width >= 78 || height >= 14 {
        Color::Reset
    } else {
        PANEL
    };
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Paragraph::new(body)
            .wrap(ratatui::widgets::Wrap { trim: false })
            .block(
                Block::default()
                    .title(format!(" {title} "))
                    .borders(Borders::ALL)
                    .border_type(BorderType::Plain)
                    .border_style(Style::default().fg(INPUT))
                    .style(Style::default().bg(modal_background)),
            ),
        popup,
    );
    match &modal {
        Modal::Trust => {
            let y = popup.bottom().saturating_sub(2);
            app.hits.push(HitRegion {
                x: popup.x + 2,
                y,
                width: 19,
                height: 1,
                action: HitAction::TrustWorkspace,
            });
            app.hits.push(HitRegion {
                x: popup.x + 25,
                y,
                width: 8,
                height: 1,
                action: HitAction::Quit,
            });
        }
        Modal::Approval(approval) => {
            let count = if approval.allow_session { 3 } else { 2 };
            let y = popup.bottom().saturating_sub(2);
            for index in 0..count {
                app.hits.push(HitRegion {
                    x: popup.x + 3 + u16::try_from(index * 18).unwrap_or(0),
                    y,
                    width: 16,
                    height: 1,
                    action: HitAction::Approval(index),
                });
            }
        }
        Modal::Memory(browser) => {
            let (start, end) = memory_window(browser);
            for (row, index) in (start..end).enumerate() {
                app.hits.push(HitRegion {
                    x: popup.x + 1,
                    y: popup.y + 3 + u16::try_from(row).unwrap_or(0),
                    width: popup.width.saturating_sub(2),
                    height: 1,
                    action: HitAction::SelectMemory(index),
                });
            }
        }
        Modal::Scars(browser) => {
            let (start, end) = scar_window(browser);
            for (row, index) in (start..end).enumerate() {
                app.hits.push(HitRegion {
                    x: popup.x + 1,
                    y: popup.y + 3 + u16::try_from(row).unwrap_or(0),
                    width: popup.width.saturating_sub(2),
                    height: 1,
                    action: HitAction::SelectScar(index),
                });
            }
        }
        _ => {}
    }
}

const MEMORY_LIST_ROWS: usize = 5;
const MEMORY_DETAIL_ROWS: usize = 8;

fn memory_window(browser: &MemoryBrowser) -> (usize, usize) {
    let selected = browser
        .selected
        .min(browser.records.len().saturating_sub(1));
    let start = selected.saturating_add(1).saturating_sub(MEMORY_LIST_ROWS);
    (start, (start + MEMORY_LIST_ROWS).min(browser.records.len()))
}

fn memory_browser_body(browser: &MemoryBrowser) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                format!("{} active memories", browser.records.len()),
                Style::default().fg(INPUT).bold(),
            ),
            Span::styled(
                " · focused memory expands below",
                Style::default().fg(MUTED),
            ),
        ]),
        Line::from(""),
    ];
    let (start, end) = memory_window(browser);
    for index in start..end {
        let memory = &browser.records[index];
        let selected = index == browser.selected;
        let pending_delete = browser.pending_delete == Some(index);
        let row_style = if pending_delete {
            Style::default().bg(DELETE_BG)
        } else if selected {
            Style::default().bg(PANEL_BRIGHT)
        } else {
            Style::default()
        };
        let label = if pending_delete {
            fit("  Press Ctrl+D again to delete this memory", 84)
        } else {
            fit(
                &format!(
                    "{}  {} · {} · {}",
                    if selected { "•" } else { " " },
                    memory.summary,
                    memory.layer,
                    memory.visibility
                ),
                84,
            )
        };
        let used = UnicodeWidthStr::width(label.as_str());
        lines.push(Line::from(vec![
            Span::styled(
                label,
                Style::default()
                    .fg(if pending_delete || selected {
                        INPUT
                    } else {
                        MUTED
                    })
                    .add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    })
                    .patch(row_style),
            ),
            Span::styled(" ".repeat(84usize.saturating_sub(used)), row_style),
        ]));
    }
    for _ in end - start..MEMORY_LIST_ROWS {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(Span::styled(
        "─".repeat(84),
        Style::default().fg(RULE),
    )));

    if let Some(memory) = browser.records.get(browser.selected) {
        lines.push(Line::from(vec![
            Span::styled(
                format!("{} · {}", memory.subject, memory.predicate),
                Style::default().fg(INPUT).bold(),
            ),
            Span::styled(
                format!(
                    "  {} · confidence {:.0}%",
                    memory.status,
                    memory.confidence * 100.0
                ),
                Style::default().fg(MUTED),
            ),
        ]));
        let value = memory.value.as_str().map(str::to_owned).unwrap_or_else(|| {
            serde_json::to_string_pretty(&memory.value).unwrap_or_else(|_| memory.value.to_string())
        });
        let mut detail = Vec::new();
        push_memory_detail(&mut detail, "Summary", &memory.summary);
        push_memory_detail(&mut detail, "Value", &value);
        let start = browser
            .detail_scroll
            .min(detail.len().saturating_sub(MEMORY_DETAIL_ROWS));
        lines.extend(detail.into_iter().skip(start).take(MEMORY_DETAIL_ROWS));
        while lines.len() < 2 + MEMORY_LIST_ROWS + 1 + 1 + MEMORY_DETAIL_ROWS {
            lines.push(Line::from(""));
        }
    } else {
        lines.push(Line::from(Span::styled(
            "No active memories yet. Use /remember to capture something durable.",
            Style::default().fg(MUTED),
        )));
        for _ in 1..=MEMORY_DETAIL_ROWS {
            lines.push(Line::from(""));
        }
    }
    lines.push(Line::from(""));
    lines.push(if browser.pending_delete.is_some() {
        Line::from(vec![
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" confirm delete · ", Style::default().fg(MUTED)),
            Span::styled("↑↓", Style::default().fg(INPUT).bold()),
            Span::styled(" cancel · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" close", Style::default().fg(MUTED)),
        ])
    } else {
        Line::from(vec![
            Span::styled("↑↓", Style::default().fg(INPUT).bold()),
            Span::styled(" memories · ", Style::default().fg(MUTED)),
            Span::styled("PgUp/PgDn", Style::default().fg(INPUT).bold()),
            Span::styled(" full text · ", Style::default().fg(MUTED)),
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" delete · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" close", Style::default().fg(MUTED)),
        ])
    });
    lines
}

const SCAR_LIST_ROWS: usize = 5;
const SCAR_DETAIL_ROWS: usize = 9;

fn scar_window(browser: &ScarBrowser) -> (usize, usize) {
    let selected = browser
        .selected
        .min(browser.records.len().saturating_sub(1));
    let start = selected.saturating_add(1).saturating_sub(SCAR_LIST_ROWS);
    (start, (start + SCAR_LIST_ROWS).min(browser.records.len()))
}

fn scar_browser_body(browser: &ScarBrowser) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                format!("{} visible Scars", browser.records.len()),
                Style::default().fg(INPUT).bold(),
            ),
            Span::styled(" · focused Scar expands below", Style::default().fg(MUTED)),
        ]),
        Line::from(""),
    ];
    let (start, end) = scar_window(browser);
    for index in start..end {
        let scar = &browser.records[index];
        let selected = index == browser.selected;
        let pending_delete = browser.pending_delete == Some(index);
        let row_style = if pending_delete {
            Style::default().bg(DELETE_BG)
        } else if selected {
            Style::default().bg(PANEL_BRIGHT)
        } else {
            Style::default()
        };
        let label = if pending_delete {
            fit("  Press Ctrl+D again to permanently delete this Scar", 86)
        } else {
            fit(
                &format!(
                    "{}  {:<10} · {:<6} · {}",
                    if selected { "•" } else { " " },
                    scar.status,
                    scar.severity,
                    scar.title
                ),
                86,
            )
        };
        let used = UnicodeWidthStr::width(label.as_str());
        lines.push(Line::from(vec![
            Span::styled(
                label,
                Style::default()
                    .fg(if pending_delete || selected {
                        INPUT
                    } else {
                        MUTED
                    })
                    .add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    })
                    .patch(row_style),
            ),
            Span::styled(" ".repeat(86usize.saturating_sub(used)), row_style),
        ]));
    }
    for _ in end - start..SCAR_LIST_ROWS {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(Span::styled(
        "─".repeat(86),
        Style::default().fg(RULE),
    )));

    if let Some(scar) = browser.records.get(browser.selected) {
        let mut detail = vec![Line::from(vec![
            Span::styled(scar.title.clone(), Style::default().fg(INPUT).bold()),
            Span::styled(
                format!("  {}", short_identifier(&scar.id)),
                Style::default().fg(MUTED),
            ),
        ])];
        push_memory_detail(
            &mut detail,
            "State",
            &format!(
                "{} · severity {} · scope {} · detected {}",
                scar.status, scar.severity, scar.scope, scar.detection
            ),
        );
        push_memory_detail(&mut detail, "Signature", &scar.failure_signature);
        push_memory_detail(&mut detail, "Problem", &scar.description);
        push_memory_detail(&mut detail, "Expected", &scar.expected_behavior);
        push_memory_detail(
            &mut detail,
            "Repair",
            &match (&scar.repair_layer, &scar.repair_reference) {
                (Some(layer), Some(reference)) => {
                    format!("{} · {}", layer, short_identifier(reference))
                }
                (Some(layer), None) => layer.clone(),
                _ => "No repair attached".to_owned(),
            },
        );
        push_memory_detail(
            &mut detail,
            "History",
            &format!(
                "{} evidence · {} clean guards · {} regressions · last triggered {}",
                scar.evidence_event_ids.len(),
                scar.successful_guard_count,
                scar.regression_count,
                scar.last_triggered_at
            ),
        );
        let start = browser
            .detail_scroll
            .min(detail.len().saturating_sub(SCAR_DETAIL_ROWS));
        lines.extend(detail.into_iter().skip(start).take(SCAR_DETAIL_ROWS));
        while lines.len() < 2 + SCAR_LIST_ROWS + 1 + SCAR_DETAIL_ROWS {
            lines.push(Line::from(""));
        }
    } else {
        lines.push(Line::from(Span::styled(
            "No visible Scars. Corrections and recurring failures appear here.",
            Style::default().fg(MUTED),
        )));
        for _ in 1..SCAR_DETAIL_ROWS {
            lines.push(Line::from(""));
        }
    }
    lines.push(Line::from(""));
    lines.push(if browser.pending_delete.is_some() {
        Line::from(vec![
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" confirm permanent deletion · ", Style::default().fg(MUTED)),
            Span::styled("↑↓", Style::default().fg(INPUT).bold()),
            Span::styled(" cancel · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" close", Style::default().fg(MUTED)),
        ])
    } else {
        Line::from(vec![
            Span::styled("↑↓", Style::default().fg(INPUT).bold()),
            Span::styled(" Scars · ", Style::default().fg(MUTED)),
            Span::styled("PgUp/PgDn", Style::default().fg(INPUT).bold()),
            Span::styled(" details · ", Style::default().fg(MUTED)),
            Span::styled("E", Style::default().fg(INPUT).bold()),
            Span::styled(" edit · ", Style::default().fg(MUTED)),
            Span::styled("Ctrl+D", Style::default().fg(INPUT).bold()),
            Span::styled(" delete · ", Style::default().fg(MUTED)),
            Span::styled("Esc", Style::default().fg(INPUT).bold()),
            Span::styled(" close", Style::default().fg(MUTED)),
        ])
    });
    lines
}

fn scar_editor_body(editor: &ScarEditor) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                format!("Scar {}", short_identifier(&editor.scar_id)),
                Style::default().fg(INPUT).bold(),
            ),
            Span::styled(
                " · evidence, trigger signature, and repair history remain immutable",
                Style::default().fg(MUTED),
            ),
        ]),
        Line::from(""),
    ];
    push_scar_editor_field(
        &mut lines,
        "Title",
        &editor.title,
        editor.field == ScarEditField::Title,
        2,
    );
    let severity_selected = editor.field == ScarEditField::Severity;
    lines.push(Line::from(vec![
        Span::styled(
            "Severity  ",
            Style::default().fg(if severity_selected { INPUT } else { MUTED }),
        ),
        Span::styled(
            format!("‹ {} ›", editor.severity),
            Style::default()
                .fg(if severity_selected { GOLD } else { INPUT })
                .add_modifier(if severity_selected {
                    Modifier::BOLD
                } else {
                    Modifier::empty()
                }),
        ),
    ]));
    lines.push(Line::from(""));
    push_scar_editor_field(
        &mut lines,
        "Problem",
        &editor.description,
        editor.field == ScarEditField::Description,
        4,
    );
    push_scar_editor_field(
        &mut lines,
        "Expected",
        &editor.expected_behavior,
        editor.field == ScarEditField::ExpectedBehavior,
        4,
    );
    while lines.len() < 20 {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(vec![
        Span::styled("Tab", Style::default().fg(INPUT).bold()),
        Span::styled(" next field · ", Style::default().fg(MUTED)),
        Span::styled("Ctrl+S", Style::default().fg(INPUT).bold()),
        Span::styled(" save · ", Style::default().fg(MUTED)),
        Span::styled("Esc", Style::default().fg(INPUT).bold()),
        Span::styled(" discard changes", Style::default().fg(MUTED)),
    ]));
    lines
}

fn agent_editor_body(editor: &AgentEditor) -> Vec<Line<'static>> {
    match editor.page {
        AgentEditorPage::Identity => agent_identity_body(editor),
        AgentEditorPage::Access => agent_access_body(editor),
    }
}

fn agent_identity_body(editor: &AgentEditor) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(vec![
            Span::styled("Identity", Style::default().fg(INPUT).bold()),
            Span::styled("  1 / 2", Style::default().fg(MUTED)),
            Span::styled(
                " · AGENT.md is stored as portable Markdown",
                Style::default().fg(MUTED),
            ),
        ]),
        Line::from(""),
    ];
    push_agent_editor_field(
        &mut lines,
        "Name",
        &editor.name,
        editor.field == AgentEditField::Name,
        1,
        false,
    );
    lines.push(Line::from(""));
    push_agent_editor_field(
        &mut lines,
        "Slug",
        &editor.slug,
        editor.field == AgentEditField::Slug,
        1,
        false,
    );
    lines.push(Line::from(""));
    push_agent_editor_field(
        &mut lines,
        "AGENT.md instructions",
        &editor.instructions,
        editor.field == AgentEditField::Instructions,
        10,
        true,
    );
    while lines.len() < 22 {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(vec![
        Span::styled("↑↓", Style::default().fg(INPUT).bold()),
        Span::styled(" field · ", Style::default().fg(MUTED)),
        Span::styled("←→", Style::default().fg(INPUT).bold()),
        Span::styled(" page · ", Style::default().fg(MUTED)),
        Span::styled("Ctrl+Enter", Style::default().fg(INPUT).bold()),
        Span::styled(" create · ", Style::default().fg(MUTED)),
        Span::styled("Esc", Style::default().fg(INPUT).bold()),
        Span::styled(" cancel", Style::default().fg(MUTED)),
    ]));
    lines
}

fn push_agent_editor_field(
    lines: &mut Vec<Line<'static>>,
    label: &str,
    input: &Composer,
    selected: bool,
    max_rows: usize,
    markdown: bool,
) {
    lines.push(Line::from(Span::styled(
        label.to_owned(),
        Style::default()
            .fg(if selected { INPUT } else { MUTED })
            .add_modifier(if selected {
                Modifier::BOLD
            } else {
                Modifier::empty()
            }),
    )));
    let value = composer_edit_text(input, selected);
    let placeholder = if label == "Name" {
        "Agent name"
    } else if label == "Slug" {
        "agent-slug"
    } else {
        "# Role\nDescribe how this agent should work…"
    };
    let empty = input.is_empty();
    let shown = if empty {
        format!("{}{placeholder}", if selected { "▏" } else { "" })
    } else {
        value.clone()
    };
    let mut wrapped = Vec::new();
    for raw in shown.lines().chain(shown.is_empty().then_some("")) {
        let mut remaining = raw;
        loop {
            let (part, rest) = split_width(remaining, 88);
            wrapped.push(part.to_owned());
            if rest.is_empty() {
                break;
            }
            remaining = rest;
        }
    }
    let caret_row = wrapped
        .iter()
        .position(|row| row.contains('▏'))
        .unwrap_or_default();
    let start = caret_row
        .saturating_add(1)
        .saturating_sub(max_rows)
        .min(wrapped.len().saturating_sub(max_rows));
    let first_row = lines.len();
    for row in wrapped.into_iter().skip(start).take(max_rows) {
        let spans = if empty {
            vec![Span::styled(row, Style::default().fg(MUTED))]
        } else if markdown {
            agent_markdown_source_spans(&row)
        } else {
            vec![Span::styled(row, Style::default().fg(INPUT))]
        };
        lines.push(Line::from(spans).style(Style::default().bg(if selected {
            PANEL_BRIGHT
        } else {
            Color::Reset
        })));
    }
    while lines.len() < first_row + max_rows {
        lines.push(Line::from(""));
    }
}

fn agent_markdown_source_spans(value: &str) -> Vec<Span<'static>> {
    if value.trim_start().starts_with('#') {
        return vec![Span::styled(
            value.to_owned(),
            Style::default().fg(CYAN).bold(),
        )];
    }
    let mut spans = Vec::new();
    let mut remaining = value;
    while let Some(open) = remaining.find('`') {
        if open > 0 {
            spans.push(Span::styled(
                remaining[..open].to_owned(),
                Style::default().fg(INPUT),
            ));
        }
        spans.push(Span::styled("`", Style::default().fg(MUTED)));
        let rest = &remaining[open + 1..];
        if let Some(close) = rest.find('`') {
            spans.push(Span::styled(
                rest[..close].to_owned(),
                Style::default().fg(CYAN),
            ));
            spans.push(Span::styled("`", Style::default().fg(MUTED)));
            remaining = &rest[close + 1..];
        } else {
            remaining = rest;
            break;
        }
    }
    if !remaining.is_empty() || spans.is_empty() {
        spans.push(Span::styled(
            remaining.to_owned(),
            Style::default().fg(INPUT),
        ));
    }
    let trimmed = value.trim_start();
    if (trimmed.starts_with("- ") || trimmed.starts_with("* "))
        && let Some(first) = spans.first_mut()
    {
        first.style = first.style.fg(CYAN);
    }
    spans
}

fn agent_access_body(editor: &AgentEditor) -> Vec<Line<'static>> {
    let mut rows: Vec<(Option<usize>, Line<'static>)> = Vec::new();
    rows.push((
        None,
        Line::from(Span::styled("Tools", Style::default().fg(MUTED).bold())),
    ));
    for (index, choice) in editor.tools.iter().enumerate() {
        rows.push((
            Some(index),
            agent_choice_line(choice, index == editor.access_selected),
        ));
    }
    rows.push((None, Line::from("")));
    rows.push((
        None,
        Line::from(Span::styled("Skills", Style::default().fg(MUTED).bold())),
    ));
    for (index, choice) in editor.skills.iter().enumerate() {
        let global = editor.tools.len() + index;
        rows.push((
            Some(global),
            agent_choice_line(choice, global == editor.access_selected),
        ));
    }
    if editor.skills.is_empty() {
        rows.push((
            None,
            Line::from(Span::styled(
                "  No workspace Skills are currently available",
                Style::default().fg(MUTED),
            )),
        ));
    }
    let selected_row = rows
        .iter()
        .position(|(index, _)| *index == Some(editor.access_selected))
        .unwrap_or_default();
    const WINDOW: usize = 18;
    let start = selected_row
        .saturating_add(1)
        .saturating_sub(WINDOW)
        .min(rows.len().saturating_sub(WINDOW));
    let mut lines = vec![
        Line::from(vec![
            Span::styled("Capabilities", Style::default().fg(INPUT).bold()),
            Span::styled("  2 / 2", Style::default().fg(MUTED)),
            Span::styled(" · everything starts selected", Style::default().fg(MUTED)),
        ]),
        Line::from(""),
    ];
    lines.extend(
        rows.into_iter()
            .skip(start)
            .take(WINDOW)
            .map(|(_, line)| line),
    );
    while lines.len() < 22 {
        lines.push(Line::from(""));
    }
    lines.push(Line::from(vec![
        Span::styled("↑↓", Style::default().fg(INPUT).bold()),
        Span::styled(" navigate · ", Style::default().fg(MUTED)),
        Span::styled("←→", Style::default().fg(INPUT).bold()),
        Span::styled(" page · ", Style::default().fg(MUTED)),
        Span::styled("Space", Style::default().fg(INPUT).bold()),
        Span::styled(" toggle · ", Style::default().fg(MUTED)),
        Span::styled("Ctrl+Enter", Style::default().fg(INPUT).bold()),
        Span::styled(" create · ", Style::default().fg(MUTED)),
        Span::styled("Esc", Style::default().fg(INPUT).bold()),
        Span::styled(" cancel", Style::default().fg(MUTED)),
    ]));
    lines
}

fn agent_choice_line(choice: &AgentChoice, focused: bool) -> Line<'static> {
    let row_style = if focused {
        Style::default().bg(PANEL_BRIGHT)
    } else {
        Style::default()
    };
    Line::from(vec![
        Span::styled(
            if choice.selected { "  ▣ " } else { "  □ " },
            Style::default()
                .fg(if choice.selected { CYAN } else { MUTED })
                .patch(row_style),
        ),
        Span::styled(
            fit(&choice.label, 25),
            Style::default()
                .fg(if focused { Color::White } else { INPUT })
                .add_modifier(if focused {
                    Modifier::BOLD
                } else {
                    Modifier::empty()
                })
                .patch(row_style),
        ),
        Span::styled(
            format!("  {}", fit(&choice.detail, 52)),
            Style::default().fg(MUTED).patch(row_style),
        ),
    ])
}

fn push_scar_editor_field(
    lines: &mut Vec<Line<'static>>,
    label: &str,
    input: &Composer,
    selected: bool,
    max_rows: usize,
) {
    lines.push(Line::from(Span::styled(
        label.to_owned(),
        Style::default()
            .fg(if selected { INPUT } else { MUTED })
            .add_modifier(if selected {
                Modifier::BOLD
            } else {
                Modifier::empty()
            }),
    )));
    let value = composer_edit_text(input, selected);
    let mut wrapped = Vec::new();
    for raw in value.lines().chain(value.is_empty().then_some("")) {
        let mut remaining = raw;
        loop {
            let (part, rest) = split_width(remaining, 84);
            wrapped.push(part.to_owned());
            if rest.is_empty() {
                break;
            }
            remaining = rest.trim_start_matches(' ');
        }
    }
    let caret_row = wrapped
        .iter()
        .position(|row| row.contains('▏'))
        .unwrap_or_default();
    let start = caret_row
        .saturating_add(1)
        .saturating_sub(max_rows)
        .min(wrapped.len().saturating_sub(max_rows));
    let first_row = lines.len();
    for row in wrapped.into_iter().skip(start).take(max_rows) {
        lines.push(Line::from(Span::styled(
            format!("  {row}"),
            Style::default()
                .fg(INPUT)
                .bg(if selected { PANEL_BRIGHT } else { Color::Reset }),
        )));
    }
    while lines.len() < first_row + max_rows {
        lines.push(Line::from(""));
    }
}

fn composer_edit_text(input: &Composer, caret: bool) -> String {
    let mut value = String::new();
    for (index, unit) in input.units.iter().enumerate() {
        if caret && index == input.cursor {
            value.push('▏');
        }
        match unit {
            ComposerUnit::Text(text) | ComposerUnit::Paste(text) => value.push_str(text),
        }
    }
    if caret && input.cursor == input.units.len() {
        value.push('▏');
    }
    value
}

fn short_identifier(value: &str) -> &str {
    value.get(..8).unwrap_or(value)
}

fn push_memory_detail(lines: &mut Vec<Line<'static>>, label: &str, value: &str) {
    let mut first = true;
    for raw in value.lines().chain(value.is_empty().then_some("")) {
        let mut remaining = raw;
        loop {
            let prefix = if first {
                format!("{label:<10}")
            } else {
                " ".repeat(10)
            };
            let (part, rest) = split_width(remaining, 74);
            lines.push(Line::from(vec![
                Span::styled(prefix, Style::default().fg(MUTED)),
                Span::styled(part.to_owned(), Style::default().fg(INPUT)),
            ]));
            first = false;
            if rest.is_empty() {
                break;
            }
            remaining = rest.trim_start_matches(' ');
        }
    }
}

fn push_markdown(
    lines: &mut Vec<RenderLine<'static>>,
    value: &str,
    width: usize,
    base_style: Style,
) {
    let mut fence: Option<String> = None;
    for raw in value.lines() {
        let trimmed = raw.trim_start();
        if let Some(marker) = trimmed.strip_prefix("```") {
            if fence.is_some() {
                lines.push(markdown_rule(width));
                fence = None;
            } else {
                let language = marker.trim().to_owned();
                let label = if language.is_empty() {
                    "code".to_owned()
                } else {
                    language.clone()
                };
                lines.push(RenderLine {
                    line: Line::from(vec![
                        Span::styled("  ── ", Style::default().fg(RULE)),
                        Span::styled(label, Style::default().fg(MUTED)),
                    ]),
                    thought: None,
                    sheen: None,
                });
                fence = Some(language);
            }
            continue;
        }
        if let Some(language) = &fence {
            if language.eq_ignore_ascii_case("diff") {
                push_diff_source_line(lines, raw, width);
            } else {
                push_styled_wrapped(
                    lines,
                    vec![Span::styled(raw.to_owned(), Style::default().fg(INPUT))],
                    width,
                    "",
                    Style::default(),
                );
            }
            continue;
        }
        if trimmed.is_empty() {
            lines.push(RenderLine {
                line: Line::from(""),
                thought: None,
                sheen: None,
            });
            continue;
        }
        if is_markdown_rule(trimmed) {
            lines.push(markdown_rule(width));
            continue;
        }
        let heading_depth = trimmed
            .chars()
            .take_while(|character| *character == '#')
            .count();
        if (1..=6).contains(&heading_depth) && trimmed.as_bytes().get(heading_depth) == Some(&b' ')
        {
            let style = base_style
                .fg(if heading_depth == 1 {
                    Color::White
                } else {
                    INPUT
                })
                .bold();
            push_styled_wrapped(
                lines,
                markdown_spans(trimmed[heading_depth + 1..].trim(), style),
                width,
                "  ",
                Style::default(),
            );
            continue;
        }
        if let Some(quoted) = trimmed.strip_prefix("> ") {
            push_styled_wrapped(
                lines,
                markdown_spans(quoted, Style::default().fg(MUTED)),
                width,
                "  │ ",
                Style::default().fg(RULE),
            );
            continue;
        }
        if let Some(item) = ["- ", "* ", "+ "]
            .iter()
            .find_map(|marker| trimmed.strip_prefix(marker))
        {
            push_styled_wrapped(
                lines,
                markdown_spans(item, base_style),
                width,
                "  • ",
                Style::default().fg(MUTED),
            );
            continue;
        }
        if let Some((prefix, item)) = numbered_markdown_item(trimmed) {
            push_styled_wrapped(
                lines,
                markdown_spans(item, base_style),
                width,
                &format!("  {prefix} "),
                Style::default().fg(MUTED),
            );
            continue;
        }
        push_styled_wrapped(
            lines,
            markdown_spans(trimmed, base_style),
            width,
            "  ",
            Style::default(),
        );
    }
    if fence.is_some() {
        lines.push(markdown_rule(width));
    }
}

fn markdown_spans(value: &str, base_style: Style) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    let mut remaining = value;
    while !remaining.is_empty() {
        if let Some(rest) = remaining.strip_prefix('`')
            && let Some(end) = rest.find('`')
        {
            spans.push(Span::styled(
                rest[..end].to_owned(),
                Style::default().fg(CYAN).bg(PANEL_BRIGHT),
            ));
            remaining = &rest[end + 1..];
            continue;
        }
        if let Some(rest) = remaining.strip_prefix("**")
            && let Some(end) = rest.find("**")
        {
            spans.push(Span::styled(
                rest[..end].to_owned(),
                base_style.add_modifier(Modifier::BOLD),
            ));
            remaining = &rest[end + 2..];
            continue;
        }
        if let Some(rest) = remaining.strip_prefix('*')
            && let Some(end) = rest.find('*')
        {
            spans.push(Span::styled(
                rest[..end].to_owned(),
                base_style.add_modifier(Modifier::ITALIC),
            ));
            remaining = &rest[end + 1..];
            continue;
        }
        if let Some(rest) = remaining.strip_prefix('[')
            && let Some(label_end) = rest.find("](")
            && let Some(target_end) = rest[label_end + 2..].find(')')
        {
            spans.push(Span::styled(
                rest[..label_end].to_owned(),
                base_style.add_modifier(Modifier::UNDERLINED),
            ));
            let target_start = label_end + 2;
            spans.push(Span::styled(
                format!(" ({})", &rest[target_start..target_start + target_end]),
                Style::default().fg(MUTED),
            ));
            remaining = &rest[target_start + target_end + 1..];
            continue;
        }
        let end = remaining
            .char_indices()
            .skip(1)
            .find_map(|(index, character)| matches!(character, '`' | '*' | '[').then_some(index))
            .unwrap_or(remaining.len());
        spans.push(Span::styled(remaining[..end].to_owned(), base_style));
        remaining = &remaining[end..];
    }
    spans
}

fn push_styled_wrapped(
    lines: &mut Vec<RenderLine<'static>>,
    spans: Vec<Span<'static>>,
    width: usize,
    prefix: &str,
    prefix_style: Style,
) {
    let prefix_width = UnicodeWidthStr::width(prefix);
    let available = width.saturating_sub(prefix_width).max(1);
    let mut rows: Vec<Vec<Span<'static>>> =
        vec![vec![Span::styled(prefix.to_owned(), prefix_style)]];
    let mut used = 0usize;
    for span in spans {
        let style = span.style;
        for raw_token in span.content.split_inclusive(char::is_whitespace) {
            let mut token = raw_token;
            if used > 0 && used + UnicodeWidthStr::width(token) > available {
                rows.push(vec![Span::raw(" ".repeat(prefix_width))]);
                used = 0;
                token = token.trim_start_matches(char::is_whitespace);
            }
            while !token.is_empty() {
                let room = available.saturating_sub(used).max(1);
                let (part, rest) = split_width(token, room);
                if !part.is_empty() {
                    used += UnicodeWidthStr::width(part);
                    rows.last_mut()
                        .expect("markdown row")
                        .push(Span::styled(part.to_owned(), style));
                }
                if rest.is_empty() {
                    break;
                }
                rows.push(vec![Span::raw(" ".repeat(prefix_width))]);
                used = 0;
                token = rest.trim_start_matches(char::is_whitespace);
            }
        }
    }
    lines.extend(rows.into_iter().map(|spans| RenderLine {
        line: Line::from(spans),
        thought: None,
        sheen: None,
    }));
}

fn numbered_markdown_item(value: &str) -> Option<(&str, &str)> {
    let dot = value.find(". ")?;
    value[..dot]
        .chars()
        .all(|character| character.is_ascii_digit())
        .then_some((&value[..=dot], &value[dot + 2..]))
}

fn is_markdown_rule(value: &str) -> bool {
    let compact = value.replace(' ', "");
    compact.len() >= 3
        && compact
            .chars()
            .all(|character| matches!(character, '-' | '_' | '*'))
}

fn markdown_rule(width: usize) -> RenderLine<'static> {
    RenderLine {
        line: Line::from(Span::styled(
            format!("  {}", "─".repeat(width.saturating_sub(2).min(48))),
            Style::default().fg(RULE),
        )),
        thought: None,
        sheen: None,
    }
}

fn looks_like_unified_diff(value: &str) -> bool {
    value.lines().any(|line| line.starts_with("--- "))
        && value.lines().any(|line| line.starts_with("+++ "))
}

fn push_diff(lines: &mut Vec<RenderLine<'static>>, value: &str, width: usize, truncated: bool) {
    let source_lines = compact_diff_lines(value);
    let additions = source_lines
        .iter()
        .flatten()
        .filter(|line| line.value.starts_with('+'))
        .count();
    let removals = source_lines
        .iter()
        .flatten()
        .filter(|line| line.value.starts_with('-'))
        .count();
    lines.push(RenderLine {
        line: Line::from(vec![
            Span::styled("  ── ", Style::default().fg(RULE)),
            Span::styled("diff", Style::default().fg(MUTED)),
            Span::styled("  (", Style::default().fg(MUTED)),
            Span::styled(format!("+{additions}"), Style::default().fg(MINT)),
            Span::styled(" ", Style::default()),
            Span::styled(format!("-{removals}"), Style::default().fg(CORAL)),
            Span::styled(")", Style::default().fg(MUTED)),
        ]),
        thought: None,
        sheen: None,
    });
    for line in source_lines.iter().take(160) {
        if let Some(line) = line {
            push_numbered_diff_source_line(lines, *line, width);
        } else {
            push_styled_wrapped(
                lines,
                vec![Span::styled("⋯".to_owned(), Style::default().fg(MUTED))],
                width,
                "    ",
                Style::default(),
            );
        }
    }
    if truncated || source_lines.len() > 160 {
        push_styled_wrapped(
            lines,
            vec![Span::styled(
                "Diff truncated; full result is retained by Hames.".to_owned(),
                Style::default().fg(GOLD),
            )],
            width,
            "    ",
            Style::default(),
        );
    }
    lines.push(markdown_rule(width));
}

#[derive(Clone, Copy)]
struct NumberedDiffLine<'a> {
    line_number: usize,
    value: &'a str,
}

fn compact_diff_lines(value: &str) -> Vec<Option<NumberedDiffLine<'_>>> {
    let mut result = Vec::new();
    let mut hunk = Vec::new();
    let mut old_line = 0usize;
    let mut new_line = 0usize;
    for line in value.lines() {
        if line.starts_with("--- ") || line.starts_with("+++ ") {
            continue;
        }
        if line.starts_with("@@") {
            append_compact_hunk(&mut result, &hunk);
            hunk.clear();
            if let Some((old_start, new_start)) = hunk_starts(line) {
                old_line = old_start;
                new_line = new_start;
            }
        } else if line.starts_with('+') {
            hunk.push(NumberedDiffLine {
                line_number: new_line,
                value: line,
            });
            new_line += 1;
        } else if line.starts_with('-') {
            hunk.push(NumberedDiffLine {
                line_number: old_line,
                value: line,
            });
            old_line += 1;
        } else if line.starts_with(' ') {
            hunk.push(NumberedDiffLine {
                line_number: new_line,
                value: line,
            });
            old_line += 1;
            new_line += 1;
        }
    }
    append_compact_hunk(&mut result, &hunk);
    result
}

fn hunk_starts(value: &str) -> Option<(usize, usize)> {
    let mut parts = value.split_whitespace();
    (parts.next()? == "@@").then_some(())?;
    let old = parts.next()?.strip_prefix('-')?;
    let new = parts.next()?.strip_prefix('+')?;
    Some((diff_range_start(old)?, diff_range_start(new)?))
}

fn diff_range_start(value: &str) -> Option<usize> {
    value.split(',').next()?.parse().ok()
}

fn append_compact_hunk<'a>(
    result: &mut Vec<Option<NumberedDiffLine<'a>>>,
    hunk: &[NumberedDiffLine<'a>],
) {
    if hunk.is_empty() {
        return;
    }
    let changed = hunk
        .iter()
        .enumerate()
        .filter_map(|(index, line)| {
            (line.value.starts_with('+') || line.value.starts_with('-')).then_some(index)
        })
        .collect::<Vec<_>>();
    if changed.is_empty() {
        return;
    }
    if !result.is_empty() {
        result.push(None);
    }
    for (index, line) in hunk.iter().enumerate() {
        if changed.iter().any(|changed| changed.abs_diff(index) <= 1) {
            result.push(Some(*line));
        }
    }
}

fn push_numbered_diff_source_line(
    lines: &mut Vec<RenderLine<'static>>,
    source: NumberedDiffLine<'_>,
    width: usize,
) {
    let prefix = format!("  {:>4} ", source.line_number);
    push_diff_source_line_with_prefix(lines, source.value, width, &prefix);
}

fn push_diff_source_line(lines: &mut Vec<RenderLine<'static>>, value: &str, width: usize) {
    push_diff_source_line_with_prefix(lines, value, width, "");
}

fn push_diff_source_line_with_prefix(
    lines: &mut Vec<RenderLine<'static>>,
    value: &str,
    width: usize,
    prefix: &str,
) {
    let (style, rail, background) = if value.starts_with("+++") || value.starts_with("---") {
        (Style::default().fg(MUTED).bold(), RULE, None)
    } else if value.starts_with('+') {
        (Style::default().fg(MINT), MINT, Some(ADDITION_BG))
    } else if value.starts_with('-') {
        (Style::default().fg(CORAL), CORAL, Some(REMOVAL_BG))
    } else if value.starts_with("@@") {
        (Style::default().fg(SKY).bold(), SKY, Some(PANEL_BRIGHT))
    } else {
        (Style::default().fg(INPUT), RULE, None)
    };
    let start = lines.len();
    push_styled_wrapped(
        lines,
        vec![Span::styled(value.to_owned(), style)],
        width,
        prefix,
        Style::default().fg(rail),
    );
    if let Some(background) = background {
        for item in &mut lines[start..] {
            let used = item.line.width();
            for span in &mut item.line.spans {
                span.style = span.style.bg(background);
            }
            if used < width {
                item.line.spans.push(Span::styled(
                    " ".repeat(width - used),
                    Style::default().bg(background),
                ));
            }
        }
    }
}

fn push_wrapped(
    lines: &mut Vec<RenderLine<'static>>,
    value: &str,
    width: usize,
    prefix: &str,
    style: Style,
) {
    for raw in value.split('\n') {
        let mut remaining = raw;
        if remaining.is_empty() {
            lines.push(RenderLine {
                line: Line::from(prefix.to_owned()),
                thought: None,
                sheen: None,
            });
            continue;
        }
        while !remaining.is_empty() {
            let available = width.saturating_sub(UnicodeWidthStr::width(prefix)).max(1);
            let (part, rest) = split_width(remaining, available);
            lines.push(RenderLine {
                line: Line::from(vec![
                    Span::raw(prefix.to_owned()),
                    Span::styled(part.to_owned(), style),
                ]),
                thought: None,
                sheen: None,
            });
            remaining = rest.trim_start_matches(' ');
        }
    }
}

fn split_width(value: &str, width: usize) -> (&str, &str) {
    if UnicodeWidthStr::width(value) <= width {
        return (value, "");
    }
    let mut last_space = None;
    let mut end = 0;
    for (index, character) in value.char_indices() {
        let next = index + character.len_utf8();
        if UnicodeWidthStr::width(&value[..next]) > width {
            break;
        }
        end = next;
        if character.is_whitespace() {
            last_space = Some(index);
        }
    }
    let split = last_space.filter(|index| *index > 0).unwrap_or(end.max(1));
    (&value[..split], &value[split..])
}

fn thought_label(duration: f64) -> String {
    let seconds = duration.max(0.0) as u64;
    if seconds < 10 {
        return "Thought".to_owned();
    }
    if seconds < 60 {
        return format!("Thought ({seconds}s)");
    }
    let minutes = seconds / 60;
    let remainder = seconds % 60;
    if remainder == 0 {
        format!("Thought ({minutes}m)")
    } else {
        format!("Thought ({minutes}m {remainder}s)")
    }
}

fn paste_capsule(value: &str) -> String {
    let lines = value
        .as_bytes()
        .iter()
        .filter(|byte| **byte == b'\n')
        .count()
        + 1;
    format!(
        " Pasted Text · {lines} lines · {} ",
        size_label(value.len())
    )
}

fn size_label(bytes: usize) -> String {
    if bytes < 1024 {
        format!("{bytes} B")
    } else {
        format!("{:.1} KiB", bytes as f64 / 1024.0)
    }
}

fn fit(value: &str, width: usize) -> String {
    if width == 0 {
        return String::new();
    }
    if UnicodeWidthStr::width(value) <= width {
        return value.to_owned();
    }
    let (prefix, _) = split_width(value, width.saturating_sub(1).max(1));
    format!("{}…", prefix.trim_end())
}

fn phase_color(phase: ActivityPhase) -> Color {
    match phase {
        ActivityPhase::Completed => MINT,
        ActivityPhase::Failed | ActivityPhase::Cancelled => CORAL,
        ActivityPhase::Rejected | ActivityPhase::Approval => GOLD,
        _ => INPUT,
    }
}

fn mode_color(mode: &str) -> Color {
    match mode {
        "plan" => GOLD,
        "auto" => SKY,
        _ => MUTED,
    }
}

fn mode_outline(mode: &str) -> Color {
    if mode == "plan" { GOLD } else { MUTED }
}

fn sheet_text_color(theme: ThemeKind) -> Color {
    match theme {
        ThemeKind::Hames => INPUT,
        ThemeKind::Terminal => Color::DarkGray,
    }
}

fn apply_theme(frame: &mut Frame<'_>, area: Rect, theme: ThemeKind) {
    if theme != ThemeKind::Terminal {
        return;
    }
    let buffer = frame.buffer_mut();
    for y in area.y..area.y.saturating_add(area.height) {
        for x in area.x..area.x.saturating_add(area.width) {
            let Some(cell) = buffer.cell_mut((x, y)) else {
                continue;
            };
            cell.fg = terminal_color(cell.fg);
            cell.bg = terminal_color(cell.bg);
        }
    }
}

fn terminal_color(color: Color) -> Color {
    match color {
        MINT => Color::Green,
        MINT_LIGHT => Color::LightGreen,
        SKY => Color::Blue,
        SKY_LIGHT => Color::LightBlue,
        CYAN => Color::Cyan,
        CYAN_LIGHT => Color::LightCyan,
        LILAC => Color::Magenta,
        LILAC_LIGHT => Color::LightMagenta,
        CORAL => Color::Red,
        CORAL_LIGHT => Color::LightRed,
        GOLD => Color::Yellow,
        GOLD_LIGHT => Color::LightYellow,
        MUTED => Color::DarkGray,
        MUTED_LIGHT | INPUT_LIGHT | RULE_LIGHT => Color::Gray,
        INPUT => Color::Gray,
        PANEL => Color::Black,
        PANEL_BRIGHT => Color::DarkGray,
        DELETE_BG => Color::Red,
        ADDITION_BG => Color::DarkGray,
        REMOVAL_BG => Color::DarkGray,
        Color::White => Color::Reset,
        Color::Rgb(_, _, _) => Color::Reset,
        value => value,
    }
}

fn centered(area: Rect, width: u16, height: u16) -> Rect {
    let width = width.min(area.width.saturating_sub(4)).max(1);
    let height = height.min(area.height.saturating_sub(2)).max(1);
    Rect::new(
        area.x + area.width.saturating_sub(width) / 2,
        area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    )
}

fn compact_home(value: &str) -> String {
    std::env::var("HOME")
        .ok()
        .and_then(|home| value.strip_prefix(&home).map(|suffix| format!("~{suffix}")))
        .unwrap_or_else(|| value.to_owned())
}

fn detail_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(format!("{label:<12}"), Style::default().fg(MUTED)),
        Span::styled(value.to_owned(), Style::default().fg(Color::White)),
    ])
}

fn help_line(key: &str, description: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(format!("{key:<24}"), Style::default().fg(MINT).bold()),
        Span::styled(description.to_owned(), Style::default().fg(Color::White)),
    ])
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use ratatui::Terminal;
    use ratatui::backend::TestBackend;
    use ratatui::buffer::Buffer;
    use ratatui::layout::Rect;
    use ratatui::style::Color;
    use serde_json::json;
    use unicode_width::UnicodeWidthStr;

    use super::{
        ADDITION_BG, CORAL, CYAN, DELETE_BG, GOLD, INPUT, INPUT_LIGHT, MINT, MINT_LIGHT, MUTED,
        PANEL_BRIGHT, REMOVAL_BG, SKY, agent_access_body, agent_identity_body,
        approval_detail_lines, compact_diff_lines, draw, format_elapsed, line_text,
        memory_browser_body, mode_color, mode_outline, scar_browser_body, scar_editor_body,
        scrollbar_position, sheet_text_color, thought_label, transcript_lines, traveling_sheen,
    };
    use crate::api::{MemoryRecord, QueuedMessage, Scar, Session};
    use crate::tui::app::{
        ActivityPhase, ActivityRow, AgentEditor, AgentEditorPage, App, ApprovalModal, DreamPhase,
        HitAction, MemoryBrowser, MenuAction, MenuOption, Modal, ScarBrowser, ScarEditor, Sheet,
        SheetKind, TranscriptItem, TranscriptPoint,
    };

    #[test]
    fn thought_duration_uses_significance_threshold_and_readable_units() {
        assert_eq!(thought_label(9.4), "Thought");
        assert_eq!(thought_label(10.0), "Thought (10s)");
        assert_eq!(thought_label(68.0), "Thought (1m 8s)");
    }

    #[test]
    fn tachyon_sheen_crosses_non_blank_text() {
        for (base, lighter) in [(INPUT, INPUT_LIGHT), (MINT, MINT_LIGHT)] {
            let area = Rect::new(0, 0, 8, 1);
            let mut buffer = Buffer::with_lines(["Thinking"]);
            for x in 0..8 {
                buffer[(x, 0)].set_fg(base);
            }
            let mut effect = traveling_sheen(Duration::ZERO, Duration::from_millis(1_000), None);
            effect.process(Duration::from_millis(500), &mut buffer, area);
            assert!((0..8).any(|x| buffer[(x, 0)].fg == lighter));
            assert!(
                (0..8).all(
                    |x| matches!(buffer[(x, 0)].fg, color if color == base || color == lighter)
                )
            );
        }

        let area = Rect::new(0, 0, 6, 1);
        let mut rail = Buffer::with_lines(["──────"]);
        for x in 0..6 {
            rail[(x, 0)].set_fg(MUTED);
        }
        let mut effect = traveling_sheen(Duration::ZERO, Duration::from_millis(1_000), Some(MINT));
        effect.process(Duration::from_millis(500), &mut rail, area);
        assert!((0..6).any(|x| rail[(x, 0)].fg == MINT));
        assert!((0..6).all(|x| matches!(rail[(x, 0)].fg, MUTED | MINT)));
    }

    #[test]
    fn empty_live_thought_has_no_redundant_working_body() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-thinking".to_owned());
        app.transcript.push(TranscriptItem::Thought {
            run_id: "run-thinking".to_owned(),
            content: String::new(),
            duration_seconds: 0.0,
            interrupted: false,
            live: true,
            collapsed: false,
        });

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Thinking"));
        assert!(!rendered.contains("Working…"));
    }

    #[test]
    fn live_reasoning_stays_collapsed_until_explicitly_opened() {
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-thinking".to_owned());
        app.transcript.push(TranscriptItem::Thought {
            run_id: "run-thinking".to_owned(),
            content: "partial private reasoning".to_owned(),
            duration_seconds: 0.0,
            interrupted: false,
            live: true,
            collapsed: true,
        });

        let collapsed = transcript_lines(&app, 80)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(collapsed.contains("Thinking  ▸"));
        assert!(!collapsed.contains("partial private reasoning"));

        app.toggle_thought(0);
        let expanded = transcript_lines(&app, 80)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(expanded.contains("Thinking  ▾"));
        assert!(expanded.contains("partial private reasoning"));
    }

    #[test]
    fn interruption_status_stays_below_the_thought_heading() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Thought {
            run_id: "run-interrupted".to_owned(),
            content: "Partial reasoning".to_owned(),
            duration_seconds: 12.0,
            interrupted: true,
            live: false,
            collapsed: false,
        });
        app.transcript.push(TranscriptItem::Status {
            text: "Turn interrupted".to_owned(),
            error: false,
        });

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Thought (12s)"));
        assert!(!rendered.contains("Thought (12s) · interrupted"));
        assert!(rendered.contains("Turn interrupted"));
    }

    #[test]
    fn empty_interrupted_thought_has_no_disclosure_or_click_target() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Thought {
            run_id: "run-interrupted".to_owned(),
            content: String::new(),
            duration_seconds: 3.0,
            interrupted: true,
            live: false,
            collapsed: true,
        });

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Thought"));
        assert!(!rendered.contains('▸'));
        assert!(!rendered.contains('▾'));
        assert!(
            !app.hits
                .iter()
                .any(|region| matches!(region.action, HitAction::ToggleThought(_)))
        );
    }

    #[test]
    fn composer_mode_colors_keep_the_caret_neutral() {
        assert_eq!(INPUT, Color::Rgb(156, 164, 178));
        assert_eq!(mode_color("manual"), MUTED);
        assert_eq!(mode_color("auto"), SKY);
        assert_eq!(mode_color("plan"), GOLD);
        assert_eq!(mode_outline("manual"), MUTED);
        assert_eq!(mode_outline("auto"), MUTED);
        assert_eq!(mode_outline("plan"), GOLD);
        assert_eq!(sheet_text_color(crate::tui::app::ThemeKind::Hames), INPUT);
        assert_eq!(
            sheet_text_color(crate::tui::app::ThemeKind::Terminal),
            Color::DarkGray
        );
    }

    #[test]
    fn elapsed_time_formats_for_status_bar_density() {
        assert_eq!(format_elapsed(9), "9s");
        assert_eq!(format_elapsed(68), "1m 08s");
        assert_eq!(format_elapsed(3_661), "1h 01m 01s");
    }

    #[test]
    fn scrollbar_position_reaches_both_ends_of_the_track() {
        assert_eq!(scrollbar_position(0, 10, 8), 0);
        assert_eq!(scrollbar_position(1, 10, 8), 4);
        assert_eq!(scrollbar_position(2, 10, 8), 9);
    }

    #[test]
    fn sent_paste_expands_to_full_markdown_in_the_transcript() {
        let mut app = App::new(session(), Vec::new(), true);
        let content = "before\n```rust\nfn main() {}\n```\nafter";
        app.transcript.push(TranscriptItem::User {
            content: content.to_owned(),
        });
        let rendered = transcript_lines(&app, 80)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("fn main() {}"));
        assert!(rendered.contains("── rust"));
        assert!(!rendered.contains("Pasted Text"));
        assert!(!rendered.contains("```"));
        assert_eq!(
            rendered
                .lines()
                .find(|line| line.contains("fn main() {}"))
                .unwrap(),
            "fn main() {}"
        );
    }

    #[test]
    fn assistant_markdown_renders_structure_instead_of_source_markers() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Assistant {
            run_id: "run-markdown".to_owned(),
            content: "# Result\n\n- **Bold** and `code`\n> quoted\n1. [Docs](https://example.test)"
                .to_owned(),
            live: false,
            durable: true,
        });
        let lines = transcript_lines(&app, 80);
        let code = lines
            .iter()
            .flat_map(|item| &item.line.spans)
            .find(|span| span.content == "code")
            .unwrap();
        assert_eq!(code.style.fg, Some(CYAN));
        assert_eq!(code.style.bg, Some(PANEL_BRIGHT));
        let rendered = lines
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("  Result"));
        assert!(rendered.contains("  • Bold and code"));
        assert!(rendered.contains("  │ quoted"));
        assert!(rendered.contains("  1. Docs (https://example.test)"));
        assert!(!rendered.contains("**"));
        assert!(!rendered.contains("`code`"));
    }

    #[test]
    fn agent_editor_preserves_markdown_source_and_defaults_capabilities_on() {
        let mut editor = AgentEditor::new(
            vec!["read_file".to_owned()],
            vec![(
                "testing".to_owned(),
                "Testing".to_owned(),
                "Run focused tests".to_owned(),
            )],
        );
        editor.instructions.insert_text("# Role\nUse `cargo test`.");
        let identity = agent_identity_body(&editor)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(identity.contains("# Role"));
        assert!(identity.contains("`cargo test`"));

        editor.page = AgentEditorPage::Access;
        let access = agent_access_body(&editor)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(access.contains("▣ read_file"));
        assert!(access.contains("▣ Testing"));
        assert!(access.contains("everything starts selected"));
    }

    #[test]
    fn completed_work_ends_with_a_full_width_elapsed_rule() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Assistant {
            run_id: "run-worked".to_owned(),
            content: "Done.".to_owned(),
            live: false,
            durable: true,
        });
        app.transcript.push(TranscriptItem::Worked {
            duration_seconds: 251.0,
        });

        let rendered = transcript_lines(&app, 96)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>();
        let worked = rendered
            .iter()
            .find(|line| line.starts_with("─ Worked for"))
            .unwrap();
        assert!(worked.starts_with("─ Worked for 4m 11s ─"));
        assert_eq!(UnicodeWidthStr::width(worked.as_str()), 96);
        let worked_index = rendered.iter().position(|line| line == worked).unwrap();
        assert_eq!(rendered[worked_index - 1], "");
        assert_eq!(rendered[worked_index - 2], "  Done.");
    }

    #[test]
    fn completed_edits_render_an_authoritative_colored_diff() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Activity {
            run_id: "run-diff".to_owned(),
            collapsed: false,
            rows: vec![ActivityRow {
                index: 0,
                tool_call_id: Some("edit-1".to_owned()),
                name: "edit_file".to_owned(),
                arguments: json!({"path": "src/main.rs"}),
                argument_parts: String::new(),
                phase: ActivityPhase::Completed,
                summary: "edited src/main.rs".to_owned(),
                content: concat!(
                    "--- a/src/main.rs\n",
                    "+++ b/src/main.rs\n",
                    "@@ -2,5 +2,5 @@\n",
                    " before two\n",
                    " before one\n",
                    "-old\n",
                    "+new\n",
                    " after one\n",
                    " after two\n",
                )
                .to_owned(),
                structured_data: json!({"path": "src/main.rs"}),
                truncated: false,
                duration_seconds: 0.01,
            }],
        });
        let rendered = transcript_lines(&app, 80);
        let text = rendered
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>();
        assert!(text.iter().any(|line| line.contains("── diff")));
        assert!(text.iter().any(|line| line.contains("(+1 -1)")));
        assert!(!text.iter().any(|line| line.contains("--- a/src/main.rs")));
        assert!(!text.iter().any(|line| line.contains("@@ -2,5")));
        assert!(!text.iter().any(|line| line.contains("before two")));
        assert!(!text.iter().any(|line| line.contains("after two")));
        assert!(text.iter().any(|line| line.contains("before one")));
        assert!(text.iter().any(|line| line.contains("after one")));
        assert!(text.iter().any(|line| line.contains("4 -old")));
        assert!(text.iter().any(|line| line.contains("4 +new")));
        assert!(
            !text
                .iter()
                .find(|line| line.contains("4 +new"))
                .unwrap()
                .contains('│')
        );
        let addition = rendered
            .iter()
            .find(|item| line_text(&item.line).contains("+new"))
            .unwrap();
        assert!(
            addition
                .line
                .spans
                .iter()
                .any(|span| span.style.fg == Some(MINT))
        );
        assert!(
            addition
                .line
                .spans
                .iter()
                .all(|span| span.style.bg == Some(ADDITION_BG))
        );
        let removal = rendered
            .iter()
            .find(|item| line_text(&item.line).contains("-old"))
            .unwrap();
        assert!(
            removal
                .line
                .spans
                .iter()
                .any(|span| span.style.fg == Some(CORAL))
        );
        assert!(
            removal
                .line
                .spans
                .iter()
                .all(|span| span.style.bg == Some(REMOVAL_BG))
        );

        let activity = rendered
            .iter()
            .find(|item| line_text(&item.line).contains("src/main.rs · edited"))
            .unwrap();
        assert!(
            activity
                .line
                .spans
                .iter()
                .any(|span| span.content.contains("src/main.rs") && span.style.fg == Some(SKY))
        );
    }

    #[test]
    fn nearby_diff_edits_share_context_without_a_duplicate_gap() {
        let compact = compact_diff_lines(concat!(
            "--- a/file.py\n",
            "+++ b/file.py\n",
            "@@ -4,5 +4,5 @@\n",
            "-old one\n",
            "+new one\n",
            " between\n",
            "-old two\n",
            "+new two\n",
        ));
        let values = compact
            .iter()
            .flatten()
            .map(|line| line.value)
            .collect::<Vec<_>>();
        assert_eq!(values.iter().filter(|line| **line == " between").count(), 1);
        assert!(!compact.iter().any(Option::is_none));
    }

    #[test]
    fn dormant_consolidation_is_visible_without_becoming_live_activity() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Dream {
            job_id: "memory-job".to_owned(),
            label: "Memory consolidation".to_owned(),
            phase: DreamPhase::Running,
            detail: "Consolidating memory in the background".to_owned(),
        });
        let rendered = transcript_lines(&app, 80)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("☾ Dream"));
        assert!(rendered.contains("Consolidating memory in the background"));
        assert_eq!(super::current_activity(&app), "Ready");
        assert!(!app.animating());
    }

    #[test]
    fn assistant_preface_hands_directly_to_tool_activity() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Assistant {
            run_id: "run-handoff".to_owned(),
            content: "Next, I'll write it:".to_owned(),
            live: false,
            durable: true,
        });
        app.transcript.push(TranscriptItem::Activity {
            run_id: "run-handoff".to_owned(),
            collapsed: false,
            rows: vec![ActivityRow {
                index: 0,
                tool_call_id: Some("write-1".to_owned()),
                name: "write_file".to_owned(),
                arguments: json!({"path": "game.py"}),
                argument_parts: String::new(),
                phase: ActivityPhase::Running,
                summary: String::new(),
                content: String::new(),
                structured_data: json!({}),
                truncated: false,
                duration_seconds: 0.0,
            }],
        });
        let rendered = transcript_lines(&app, 80)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>();
        let preface = rendered
            .iter()
            .position(|line| line.contains("Next, I'll write it:"))
            .unwrap();
        assert!(rendered[preface + 1].contains("◆ Work"));
    }

    #[test]
    fn test_backend_renders_adaptive_header_and_composer() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.git_ref = Some("main".to_owned());
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("◈ Hames"));
        assert!(rendered.contains("project · main"));
        assert!(!rendered.contains("· default"));
        assert!(rendered.contains("New session · Ready"));
        assert!(rendered.contains("[connected]"));
        assert!(rendered.contains("Message Hames"));
        assert!(rendered.contains("─ fixture (medium) · Auto"));
        assert!(rendered.contains("A fresh canvas"));
    }

    #[test]
    fn completed_compaction_is_a_collapsed_expandable_transcript_disclosure() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Compaction {
            run_id: "compact-1".to_owned(),
            summary: "Keep the interface calm and preserve queue ordering.".to_owned(),
            provider: "fake".to_owned(),
            model: "fixture".to_owned(),
            trigger: "automatic".to_owned(),
            turns_compacted: 18,
            before_tokens: 21_000,
            after_tokens: 1_700,
            passes: 2,
            partial: false,
            live: false,
            collapsed: true,
        });

        let collapsed = transcript_lines(&app, 90)
            .iter()
            .map(|line| line_text(&line.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(collapsed.contains("Compacted context · 18 turns · 21k → 1.7k  ▸"));
        assert!(!collapsed.contains("preserve queue ordering"));

        app.toggle_activity(0);
        let expanded = transcript_lines(&app, 90)
            .iter()
            .map(|line| line_text(&line.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(expanded.contains("fake / fixture · automatic · 2 passes"));
        assert!(expanded.contains("preserve queue ordering"));
    }

    #[test]
    fn action_errors_render_on_the_notice_line_instead_of_a_modal() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.error_notice = Some("cannot clear a session during an active run".to_owned());

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("cannot clear a session during an active run"));
        assert!(!rendered.contains("Something went wrong"));
        let error_cell = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .find(|cell| cell.symbol() == "c" && cell.fg == CORAL);
        assert!(error_cell.is_some());
    }

    #[test]
    fn active_run_replaces_shortcuts_with_compact_interrupt_meter() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        app.run_started_at = Some(Instant::now() - Duration::from_secs(12));
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("──────"));
        assert!(
            rendered.contains("Working · 12s · Enter queue · Alt+↑ now · ↑ edit · Esc interrupt")
        );
        assert!(rendered.contains("[connected]"));
        assert!(!rendered.contains("Shift+Tab mode"));
        let footer_y = terminal.size().unwrap().height - 1;
        let buffer = terminal.backend().buffer();
        assert!((2..8).all(|x| buffer.cell((x, footer_y)).unwrap().fg == MUTED));
        assert_eq!(buffer.cell((4, footer_y)).unwrap().fg, MUTED);
    }

    #[test]
    fn pending_turns_render_above_the_composer_and_remain_clickable() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.active_run = Some("run-1".to_owned());
        app.queued_messages = vec![
            queued_message("queue-1", "first queued request", 1),
            queued_message("queue-2", "second queued request", 2),
        ];

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Queued 1/2  first queued request"));
        assert!(rendered.contains("Queued 2/2  second queued request"));
        assert!(rendered.contains("Queue full 2/2"));
        assert!(app.hits.iter().any(|region| matches!(
            &region.action,
            HitAction::QueuedMessage(id) if id == "queue-1"
        )));
        assert!(app.hits.iter().any(|region| matches!(
            &region.action,
            HitAction::QueuedMessage(id) if id == "queue-2"
        )));
    }

    #[test]
    fn transcript_selection_highlights_and_copy_notice_sits_above_composer() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let transcript_y = app.transcript_viewport.y;
        app.begin_transcript_selection(TranscriptPoint { row: 0, column: 2 });
        app.update_transcript_selection(TranscriptPoint { row: 0, column: 6 });
        app.show_copy_notice(5);

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        assert_eq!(buffer.cell((2, transcript_y)).unwrap().bg, PANEL_BRIGHT);
        let rendered = buffer
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Copied to clipboard · 5 characters"));
    }

    #[test]
    fn slash_commands_use_an_open_rule_tray() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.composer.insert_text("/");
        app.update_slash_sheet();

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        let tray_top = 17;
        let tray_bottom = 25;
        assert_eq!(buffer.cell((0, tray_top)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((99, tray_top)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((0, tray_bottom)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((99, tray_bottom)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((2, tray_top + 1)).unwrap().symbol(), "•");
        assert_eq!(buffer.cell((2, tray_top + 1)).unwrap().fg, INPUT);
        assert_eq!(buffer.cell((50, tray_top + 1)).unwrap().bg, PANEL_BRIGHT);
        let rendered = buffer
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("/new"));
        let options = &app.sheet.as_ref().unwrap().options;
        assert!(options.iter().any(|option| option.label == "/status"));
        assert!(options.iter().any(|option| option.label == "/gateway"));
        assert!(options.iter().any(|option| option.label == "/clear"));

        app.composer.clear();
        app.composer.insert_text("/mo");
        app.update_slash_sheet();
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        let narrowed_top = 22;
        assert_eq!(buffer.cell((5, narrowed_top + 1)).unwrap().symbol(), "m");
        assert_eq!(buffer.cell((6, narrowed_top + 1)).unwrap().symbol(), "o");
        assert_eq!(buffer.cell((5, narrowed_top + 1)).unwrap().fg, MINT);
        assert_eq!(buffer.cell((6, narrowed_top + 1)).unwrap().fg, MINT);
        assert_eq!(buffer.cell((7, narrowed_top + 1)).unwrap().fg, INPUT);
    }

    #[test]
    fn selection_sheets_share_the_open_tray_with_an_inset_title() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.open_modes();

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        let tray_top = 21;
        let tray_bottom = 25;
        assert_eq!(buffer.cell((0, tray_top)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((1, tray_top)).unwrap().symbol(), " ");
        assert_eq!(buffer.cell((2, tray_top)).unwrap().symbol(), "E");
        assert_eq!(buffer.cell((99, tray_top)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((0, tray_bottom)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((99, tray_bottom)).unwrap().symbol(), "─");
        assert_eq!(buffer.cell((2, tray_top + 2)).unwrap().symbol(), "•");
        assert_eq!(buffer.cell((50, tray_top + 2)).unwrap().bg, PANEL_BRIGHT);
        let rendered = buffer
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("↑↓ navigate · Enter select · Esc close"));
    }

    #[test]
    fn armed_session_deletion_uses_a_red_row_and_confirmation_shortcut() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.sheet = Some(Sheet {
            kind: SheetKind::Sessions,
            title: "Open sessions".to_owned(),
            options: vec![MenuOption {
                label: "Design pass".to_owned(),
                detail: "~/hames · fixture · auto".to_owned(),
                action: MenuAction::Resume("old-session".to_owned()),
            }],
            selected: 0,
            pending_delete: None,
        });

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let idle = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(idle.contains("Enter resume · Ctrl+D remove · Esc close"));

        app.sheet.as_mut().unwrap().pending_delete = Some(0);
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        let rendered = buffer
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Press Ctrl+D again to delete this entry"));
        assert!(rendered.contains("↑↓ cancel · Esc close"));
        assert!(!rendered.contains("Design pass"));
        assert_eq!(buffer.cell((4, 24)).unwrap().symbol(), "P");
        assert_eq!(buffer.cell((10, 24)).unwrap().symbol(), "C");
        assert_eq!(buffer.cell((50, 24)).unwrap().bg, DELETE_BG);
    }

    #[test]
    fn centered_modals_use_square_corners() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Session);

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        assert_eq!(buffer.cell((11, 8)).unwrap().symbol(), "┌");
        assert_eq!(buffer.cell((88, 8)).unwrap().symbol(), "┐");
        assert_eq!(buffer.cell((11, 8)).unwrap().fg, INPUT);
        assert_ne!(buffer.cell((11, 8)).unwrap().fg, MINT);
    }

    #[test]
    fn permission_actions_use_the_lower_tray_with_inset_hit_targets() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Approval(ApprovalModal {
            approval_id: "approval-1".to_owned(),
            request_hash: "hash".to_owned(),
            name: "write_file".to_owned(),
            reason: "manual mode".to_owned(),
            arguments: "{}".to_owned(),
            allow_session: false,
            selected: 0,
            detail_scroll: 0,
        }));

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        assert_eq!(buffer.cell((1, 24)).unwrap().bg, Color::Reset);
        assert_eq!(buffer.cell((2, 24)).unwrap().bg, MINT);
        assert!(
            buffer
                .content()
                .iter()
                .map(|cell| cell.symbol())
                .collect::<String>()
                .contains("Permission required")
        );
        assert!(app.hits.iter().any(|region| {
            region.x == 2 && region.y == 24 && matches!(region.action, HitAction::Approval(0))
        }));
    }

    #[test]
    fn permission_actions_remain_visible_at_the_minimum_terminal_height() {
        let backend = TestBackend::new(76, 10);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Approval(ApprovalModal {
            approval_id: "approval-1".to_owned(),
            request_hash: "hash".to_owned(),
            name: "shell".to_owned(),
            reason: "A deliberately long permission reason that must not push actions off screen"
                .to_owned(),
            arguments: json!({"command": "a very long command that still needs visible controls"})
                .to_string(),
            allow_session: true,
            selected: 0,
            detail_scroll: 0,
        }));

        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer();
        assert_eq!(buffer.cell((2, 4)).unwrap().bg, MINT);
        assert!(app.hits.iter().any(|region| {
            region.x == 2 && region.y == 4 && matches!(region.action, HitAction::Approval(0))
        }));
    }

    #[test]
    fn permission_details_wrap_without_omitting_content() {
        let approval = ApprovalModal {
            approval_id: "approval-1".to_owned(),
            request_hash: "hash".to_owned(),
            name: "shell".to_owned(),
            reason: "This complete permission reason must remain visible across cleanly wrapped whole words"
                .to_owned(),
            arguments: json!({
                "command": "python3 a_command.py --retain every individual argument",
                "path": "/a/deliberately/long/location/that/must/remain/complete"
            })
            .to_string(),
            allow_session: true,
            selected: 0,
            detail_scroll: 0,
        };

        let rendered = approval_detail_lines(&approval, 32)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!rendered.contains('…'));
        for expected in [
            "complete",
            "permission",
            "visible",
            "python3",
            "individual",
            "argument",
            "remain",
        ] {
            assert!(
                rendered.contains(expected),
                "missing {expected:?}: {rendered}"
            );
        }
        let compact = rendered.split_whitespace().collect::<String>();
        assert!(compact.contains("/a/deliberately/long/location/that/must/remain/complete"));
    }

    #[test]
    fn memory_browser_focuses_an_active_record_and_expands_its_text() {
        let browser = MemoryBrowser {
            records: vec![memory_record()],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        };
        let rendered = memory_browser_body(&browser)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("1 active memories"));
        assert!(rendered.contains("Keep the interface calm"));
        assert!(rendered.contains("user:local · prefers_ui"));
        assert!(rendered.contains("Subdued and polished"));

        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.modal = Some(Modal::Memory(browser));
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        assert_eq!(
            terminal.backend().buffer().cell((5, 5)).unwrap().bg,
            Color::Reset
        );
    }

    #[test]
    fn memory_browser_shows_a_single_contiguous_delete_confirmation() {
        let browser = MemoryBrowser {
            records: vec![memory_record()],
            selected: 0,
            detail_scroll: 0,
            pending_delete: Some(0),
        };
        let lines = memory_browser_body(&browser);
        let rendered = lines.iter().map(line_text).collect::<Vec<_>>().join("\n");
        assert!(rendered.contains("Press Ctrl+D again to delete this memory"));
        assert!(rendered.contains("Ctrl+D confirm delete · ↑↓ cancel · Esc close"));
        assert_eq!(lines[2].spans[0].style.bg, Some(DELETE_BG));
    }

    #[test]
    fn scar_browser_expands_diagnosis_and_uses_explicit_deletion_language() {
        let mut browser = ScarBrowser {
            records: vec![scar_record()],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        };
        let rendered = scar_browser_body(&browser)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("Retried without inspecting the failure"));
        assert!(rendered.contains("Inspect the first failure before retrying"));
        assert!(rendered.contains("2 clean guards · 1 regressions"));
        assert!(rendered.contains("E edit · Ctrl+D delete"));

        browser.pending_delete = Some(0);
        let lines = scar_browser_body(&browser);
        assert!(line_text(&lines[2]).contains("permanently delete this Scar"));
        assert_eq!(lines[2].spans[0].style.bg, Some(DELETE_BG));
    }

    #[test]
    fn scar_editor_keeps_a_visible_caret_and_immutable_boundary() {
        let browser = ScarBrowser {
            records: vec![scar_record()],
            selected: 0,
            detail_scroll: 0,
            pending_delete: None,
        };
        let editor = ScarEditor::new(browser).unwrap();
        let rendered = scar_editor_body(&editor)
            .iter()
            .map(line_text)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("Retry loop▏"));
        assert!(
            rendered.contains("evidence, trigger signature, and repair history remain immutable")
        );
        assert!(rendered.contains("Ctrl+S save"));
    }

    #[test]
    fn activity_rows_hide_phantoms_and_describe_memory_deletion_semantically() {
        let mut app = App::new(session(), Vec::new(), true);
        app.transcript.push(TranscriptItem::Activity {
            run_id: "run-memory".to_owned(),
            collapsed: false,
            rows: vec![
                ActivityRow {
                    index: 0,
                    tool_call_id: None,
                    name: String::new(),
                    arguments: json!(null),
                    argument_parts: String::new(),
                    phase: ActivityPhase::Checking,
                    summary: String::new(),
                    content: String::new(),
                    structured_data: json!(null),
                    truncated: false,
                    duration_seconds: 0.0,
                },
                ActivityRow {
                    index: 1,
                    tool_call_id: Some("forget-1".to_owned()),
                    name: "memory_forget".to_owned(),
                    arguments: json!({"memory_id": "8f9b40f1-ec06-4706-841b-8fd60d60be85"}),
                    argument_parts: String::new(),
                    phase: ActivityPhase::Completed,
                    summary: "deleted memory 8f9b40f1-ec06-4706-841b-8fd60d60be85".to_owned(),
                    content: String::new(),
                    structured_data: json!(null),
                    truncated: false,
                    duration_seconds: 0.003,
                },
            ],
        });
        app.transcript.push(TranscriptItem::Assistant {
            run_id: "run-memory".to_owned(),
            content: String::new(),
            live: true,
            durable: false,
        });

        let rendered = transcript_lines(&app, 90)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("◆ Work"));
        assert!(rendered.contains("1 action · Completed  ▾"));
        assert!(rendered.contains("✓ Forgot  memory 8f9b40f1"));
        assert!(!rendered.contains("◆ Run"));
        assert!(!rendered.contains("✦ Hames"));
        assert!(!rendered.contains("ec06-4706"));

        app.toggle_activity(0);
        let collapsed = transcript_lines(&app, 90)
            .iter()
            .map(|item| line_text(&item.line))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(collapsed.contains("◆ Work · 1 action · Completed  ▸"));
        assert!(!collapsed.contains("✓ Forgot"));
    }

    #[test]
    fn test_backend_renders_minimum_size_warning() {
        let backend = TestBackend::new(55, 9);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Needs at least 56 × 10"));
    }

    #[test]
    fn composer_expands_to_eight_rows_then_renders_a_scrollbar() {
        let backend = TestBackend::new(100, 32);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.composer
            .insert_text("one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten");
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains('█'));
        let scrollbar = app
            .hits
            .iter()
            .find(|region| {
                matches!(
                    region.action,
                    crate::tui::app::HitAction::Scrollbar {
                        target: crate::tui::app::ScrollTarget::Composer,
                        ..
                    }
                ) && region.height == 8
            })
            .unwrap();
        assert_eq!(
            terminal
                .backend()
                .buffer()
                .cell((scrollbar.x, scrollbar.y + scrollbar.height - 1))
                .unwrap()
                .symbol(),
            "█"
        );
    }

    #[test]
    fn terminal_theme_maps_custom_rgb_colors_to_terminal_native_colors() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new(session(), Vec::new(), true);
        app.theme = crate::tui::app::ThemeKind::Terminal;
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        assert!(
            terminal
                .backend()
                .buffer()
                .content()
                .iter()
                .all(|cell| !matches!(cell.fg, Color::Rgb(_, _, _))
                    && !matches!(cell.bg, Color::Rgb(_, _, _)))
        );
    }

    fn memory_record() -> MemoryRecord {
        MemoryRecord {
            id: "memory-1".to_owned(),
            layer: "relationship".to_owned(),
            status: "active".to_owned(),
            visibility: "global".to_owned(),
            subject: "user:local".to_owned(),
            predicate: "prefers_ui".to_owned(),
            value: json!("Subdued and polished"),
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

    fn scar_record() -> Scar {
        Scar {
            id: "scar-123456789".to_owned(),
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

    fn session() -> Session {
        Session {
            id: "session-123456789".to_owned(),
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

    fn queued_message(id: &str, content: &str, position: usize) -> QueuedMessage {
        QueuedMessage {
            id: id.to_owned(),
            session_id: "session-123456789".to_owned(),
            content: content.to_owned(),
            remember: false,
            paste_spans: Vec::new(),
            created_at: "2026-08-24T00:00:00Z".to_owned(),
            position,
        }
    }
}
