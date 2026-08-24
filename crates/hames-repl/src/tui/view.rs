use ratatui::Frame;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::prelude::Stylize;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Borders, Clear, Padding, Paragraph, Scrollbar, ScrollbarOrientation,
    ScrollbarState,
};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::app::{
    ActivityCategory, ActivityPhase, App, ComposerUnit, HitAction, HitRegion, Modal, ScrollTarget,
    SheetKind, ThemeKind, TranscriptItem, TranscriptViewport,
};

const MINT: Color = Color::Rgb(116, 226, 192);
const SKY: Color = Color::Rgb(112, 177, 255);
const LILAC: Color = Color::Rgb(193, 154, 255);
const CORAL: Color = Color::Rgb(255, 139, 116);
const GOLD: Color = Color::Rgb(240, 190, 92);
const MUTED: Color = Color::Rgb(86, 94, 108);
const INPUT: Color = Color::Rgb(156, 164, 178);
const RULE: Color = Color::Rgb(49, 56, 69);
const DELETE_BG: Color = Color::Rgb(78, 31, 39);
const PANEL: Color = Color::Rgb(19, 23, 31);
const PANEL_BRIGHT: Color = Color::Rgb(29, 35, 46);

pub fn draw(frame: &mut Frame<'_>, app: &mut App) {
    app.hits.clear();
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

    let header_height = 2;
    let composer_width = area.width.saturating_sub(5).max(1);
    let composer_height = composer_rows(app, composer_width).clamp(1, 8) + 2;
    let notice = app
        .copy_notice()
        .map(str::to_owned)
        .or_else(|| app.notice.clone());
    let notice_height = u16::from(notice.is_some());
    let sheet_height = app
        .sheet
        .as_ref()
        .map(|sheet| (sheet.options.len() as u16 + 2).clamp(3, 9))
        .unwrap_or(0);
    let bottom = composer_height + notice_height + sheet_height + 1;
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(1),
            Constraint::Length(bottom),
        ])
        .split(area);
    render_header(frame, app, rows[0]);
    render_transcript(frame, app, rows[1]);

    let footer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(sheet_height),
            Constraint::Length(notice_height),
            Constraint::Length(composer_height),
            Constraint::Length(1),
        ])
        .split(rows[2]);
    if sheet_height > 0 {
        render_sheet(frame, app, footer[0]);
    }
    if let Some(notice) = notice {
        frame.render_widget(
            Paragraph::new(Line::from(vec![
                Span::styled("  ◆ ", Style::default().fg(GOLD)),
                Span::styled(notice, Style::default().fg(MUTED)),
            ])),
            footer[1],
        );
    }
    render_composer(frame, app, footer[2]);
    render_status_bar(frame, app, footer[3]);
    render_modal(frame, app, area);
    apply_theme(frame, area, app.theme);
}

fn render_header(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let activity = current_activity(app);
    let left = Line::from(vec![
        Span::styled(" ◈ Hames", Style::default().fg(MINT).bold()),
        Span::styled(
            format!(" · {}", app.session.agent_id),
            Style::default().fg(MUTED),
        ),
    ]);
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
    frame.render_widget(Paragraph::new(left).block(block.clone()), area);
    frame.render_widget(
        Paragraph::new(session),
        Rect::new(
            area.x + 20.min(area.width),
            area.y,
            area.width.saturating_sub(20),
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

fn current_activity(app: &App) -> &'static str {
    if app.active_run.is_none() {
        return "Ready";
    }
    for item in app.transcript.iter().rev() {
        match item {
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
    thought: Option<usize>,
}

fn render_transcript(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
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
    for (offset, item) in lines[start..end].iter().enumerate() {
        if let Some(index) = item.thought {
            app.hits.push(HitRegion {
                x: area.x,
                y: area.y + u16::try_from(offset).unwrap_or(0),
                width: area.width.saturating_sub(1),
                height: 1,
                action: HitAction::ToggleThought(index),
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

fn transcript_lines(app: &App, width: usize) -> Vec<RenderLine<'static>> {
    let mut lines = Vec::new();
    for (index, item) in app.transcript.iter().enumerate() {
        match item {
            TranscriptItem::User {
                content,
                paste_spans,
            } => {
                lines.push(RenderLine {
                    line: Line::from(Span::styled("You", Style::default().fg(SKY).bold())),
                    thought: None,
                });
                let display = pasted_display(content, paste_spans);
                push_wrapped(
                    &mut lines,
                    &display,
                    width,
                    "  ",
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
                    sheen_line("◈ Thinking", app.tick, LILAC)
                } else {
                    let mut spans = vec![
                        Span::styled("◈ ", Style::default().fg(LILAC)),
                        Span::styled(
                            thought_label(*duration_seconds),
                            Style::default().fg(LILAC).bold(),
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
                    thought: interactive.then_some(index),
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
            TranscriptItem::Assistant { content, live, .. } => {
                lines.push(RenderLine {
                    line: if *live {
                        sheen_line("✦ Hames", app.tick, MINT)
                    } else {
                        Line::from(Span::styled("✦ Hames", Style::default().fg(MINT).bold()))
                    },
                    thought: None,
                });
                push_wrapped(
                    &mut lines,
                    content,
                    width,
                    "  ",
                    Style::default().fg(Color::White),
                );
            }
            TranscriptItem::Activity { rows, .. } => {
                let mut category = None;
                for row in rows {
                    if category != Some(row.category()) {
                        category = Some(row.category());
                        let color = category_color(row.category());
                        lines.push(RenderLine {
                            line: Line::from(vec![
                                Span::styled("◆ ", Style::default().fg(color)),
                                Span::styled(
                                    row.category().label(),
                                    Style::default().fg(color).bold(),
                                ),
                            ]),
                            thought: None,
                        });
                    }
                    let glyph = match row.phase {
                        ActivityPhase::Preparing => "·",
                        ActivityPhase::Checking | ActivityPhase::Approval => "○",
                        ActivityPhase::Running => "◐",
                        ActivityPhase::Completed => "✓",
                        ActivityPhase::Failed | ActivityPhase::Cancelled => "×",
                        ActivityPhase::Rejected => "!",
                    };
                    let color = phase_color(row.phase);
                    let mut detail = row.target();
                    if !row.summary.is_empty() && row.summary != detail {
                        detail.push_str(" · ");
                        detail.push_str(&row.summary.replace('\n', " "));
                    }
                    let prefix = format!("  {glyph} {}  ", row.verb());
                    let body_width = width.saturating_sub(UnicodeWidthStr::width(prefix.as_str()));
                    let fitted = fit(&detail, body_width);
                    let line = if matches!(
                        row.phase,
                        ActivityPhase::Preparing | ActivityPhase::Checking | ActivityPhase::Running
                    ) {
                        let mut spans = vec![Span::styled(
                            format!("  {glyph} "),
                            Style::default().fg(color),
                        )];
                        spans.extend(sheen_spans(row.verb(), app.tick, color));
                        spans.push(Span::raw("  "));
                        spans.push(Span::styled(fitted, Style::default().fg(MUTED)));
                        Line::from(spans)
                    } else {
                        Line::from(vec![
                            Span::styled(
                                format!("  {glyph} {}  ", row.verb()),
                                Style::default().fg(color),
                            ),
                            Span::styled(fitted, Style::default().fg(MUTED)),
                        ])
                    };
                    lines.push(RenderLine {
                        line,
                        thought: None,
                    });
                }
            }
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
                });
            }
        }
        lines.push(RenderLine {
            line: Line::from(""),
            thought: None,
        });
    }
    if lines.is_empty() {
        lines.extend([
            RenderLine {
                line: Line::from(Span::styled(
                    "  A fresh canvas.",
                    Style::default().fg(MUTED),
                )),
                thought: None,
            },
            RenderLine {
                line: Line::from(Span::styled(
                    "  Ask Hames to explore, change, or run something.",
                    Style::default().fg(MUTED),
                )),
                thought: None,
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
            let lead = format!(" {:<20}", "Press");
            let prompt = " Ctrl+D again to delete this entry";
            let used = 3 + UnicodeWidthStr::width(lead.as_str()) + UnicodeWidthStr::width(prompt);
            spans.extend([
                Span::styled(lead, Style::default().fg(INPUT).patch(row_style)),
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

fn render_status_bar(frame: &mut Frame<'_>, app: &App, area: Rect) {
    let left = if app.sheet.is_some() {
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
    if sheet.kind == SheetKind::Sessions && sheet.pending_delete.is_some() {
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
    }
    spans.extend([
        Span::styled("Esc", Style::default().fg(INPUT).bold()),
        Span::styled(" close", Style::default().fg(MUTED)),
    ]);
    Line::from(spans)
}

fn activity_bar(app: &App) -> Line<'static> {
    const TRACK: [&str; 6] = ["━"; 6];
    let shine = usize::try_from(app.tick / 8).unwrap_or(0) % TRACK.len();
    let mut spans = vec![Span::raw("  ")];
    for (index, segment) in TRACK.iter().enumerate() {
        let distance = index.abs_diff(shine);
        let color = match distance {
            0 => Color::White,
            1 => INPUT,
            _ => MUTED,
        };
        spans.push(Span::styled(*segment, Style::default().fg(color)));
    }
    spans.push(Span::styled(
        format!("  {}", current_activity(app)),
        Style::default().fg(INPUT),
    ));
    let elapsed = app
        .run_started_at
        .map(|started| format_elapsed(started.elapsed().as_secs()))
        .unwrap_or_else(|| "0s".to_owned());
    spans.push(Span::styled(
        format!(" · {elapsed} · Esc interrupt"),
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

fn composer_rows(app: &App, width: u16) -> u16 {
    let (lines, _, _) = composer_lines(app, usize::from(width.max(1)));
    u16::try_from(lines.len()).unwrap_or(u16::MAX)
}

fn render_modal(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let Some(modal) = &app.modal else {
        return;
    };
    let (title, body, width, height) = match modal {
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
        Modal::Approval(approval) => {
            let mut lines = vec![
                Line::from(vec![
                    Span::styled("Action  ", Style::default().fg(MUTED)),
                    Span::styled(
                        approval.name.clone(),
                        Style::default().fg(Color::White).bold(),
                    ),
                ]),
                Line::from(vec![
                    Span::styled("Reason  ", Style::default().fg(MUTED)),
                    Span::styled(approval.reason.clone(), Style::default().fg(GOLD)),
                ]),
                Line::from(""),
            ];
            for line in approval.arguments.lines().take(5) {
                lines.push(Line::from(Span::styled(
                    format!("  {line}"),
                    Style::default().fg(Color::Rgb(180, 187, 201)),
                )));
            }
            lines.push(Line::from(""));
            let choices = if approval.allow_session {
                [" Allow session ", " Allow once ", " Deny "].as_slice()
            } else {
                [" Allow once ", " Deny "].as_slice()
            };
            let mut spans = Vec::new();
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
                spans.push(Span::raw("  "));
            }
            lines.push(Line::from(spans));
            ("Permission required", lines, 76, 12)
        }
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
                help_line("/new /clear /resume", "session continuity"),
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
        Modal::Session => (
            "Session continuity",
            vec![
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
            ],
            78,
            12,
        ),
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
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Paragraph::new(body)
            .wrap(ratatui::widgets::Wrap { trim: false })
            .block(
                Block::default()
                    .title(format!(" {title} "))
                    .borders(Borders::ALL)
                    .border_type(BorderType::Plain)
                    .border_style(Style::default().fg(RULE))
                    .style(Style::default().bg(PANEL)),
            ),
        popup,
    );
    match modal {
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
                    x: popup.x + 2 + u16::try_from(index * 18).unwrap_or(0),
                    y,
                    width: 16,
                    height: 1,
                    action: HitAction::Approval(index),
                });
            }
        }
        _ => app.hits.push(HitRegion {
            x: popup.x,
            y: popup.y,
            width: popup.width,
            height: popup.height,
            action: HitAction::CloseModal,
        }),
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

fn sheen_line(label: &str, tick: u64, color: Color) -> Line<'static> {
    Line::from(sheen_spans(label, tick, color))
}

fn sheen_spans(label: &str, tick: u64, color: Color) -> Vec<Span<'static>> {
    let chars: Vec<char> = label.chars().collect();
    let highlight = usize::try_from(tick).unwrap_or(0) % chars.len().max(1);
    chars
        .into_iter()
        .enumerate()
        .map(|(index, character)| {
            Span::styled(
                character.to_string(),
                Style::default()
                    .fg(if index.abs_diff(highlight) <= 1 {
                        Color::White
                    } else {
                        color
                    })
                    .add_modifier(Modifier::BOLD),
            )
        })
        .collect()
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

fn pasted_display(content: &str, spans: &[crate::api::PasteSpan]) -> String {
    if spans.is_empty() {
        return content.to_owned();
    }
    let mut result = String::new();
    let mut cursor = 0;
    for span in spans {
        if span.start_byte < cursor || span.end_byte > content.len() {
            continue;
        }
        result.push_str(&content[cursor..span.start_byte]);
        result.push_str(&format!(
            "[Pasted Text · {} lines · {}]",
            span.line_count,
            size_label(span.byte_count)
        ));
        cursor = span.end_byte;
    }
    result.push_str(&content[cursor..]);
    result
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

fn category_color(category: ActivityCategory) -> Color {
    match category {
        ActivityCategory::Explore => SKY,
        ActivityCategory::Change => CORAL,
        ActivityCategory::Run => GOLD,
        ActivityCategory::Delegate => MINT,
        ActivityCategory::Skills => LILAC,
        ActivityCategory::Memory => MINT,
        ActivityCategory::Scars => CORAL,
        ActivityCategory::Plugin => LILAC,
    }
}

fn phase_color(phase: ActivityPhase) -> Color {
    match phase {
        ActivityPhase::Completed => MINT,
        ActivityPhase::Failed | ActivityPhase::Cancelled => CORAL,
        ActivityPhase::Rejected | ActivityPhase::Approval => GOLD,
        _ => SKY,
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
        SKY => Color::Blue,
        LILAC => Color::Magenta,
        CORAL => Color::Red,
        GOLD => Color::Yellow,
        MUTED => Color::DarkGray,
        INPUT => Color::Gray,
        PANEL => Color::Black,
        PANEL_BRIGHT => Color::DarkGray,
        DELETE_BG => Color::Red,
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
    use ratatui::style::Color;

    use super::{
        DELETE_BG, GOLD, INPUT, MINT, MUTED, PANEL_BRIGHT, RULE, SKY, draw, format_elapsed,
        mode_color, mode_outline, pasted_display, scrollbar_position, sheet_text_color,
        thought_label,
    };
    use crate::api::{PasteSpan, Session};
    use crate::tui::app::{
        App, HitAction, MenuAction, MenuOption, Modal, Sheet, SheetKind, TranscriptItem,
        TranscriptPoint,
    };

    #[test]
    fn thought_duration_uses_significance_threshold_and_readable_units() {
        assert_eq!(thought_label(9.4), "Thought");
        assert_eq!(thought_label(10.0), "Thought (10s)");
        assert_eq!(thought_label(68.0), "Thought (1m 8s)");
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
    fn transcript_replaces_paste_bytes_with_durable_capsule() {
        let content = "before é\nafter";
        let start = "before ".len();
        let end = start + "é\n".len();
        assert_eq!(
            pasted_display(
                content,
                &[PasteSpan {
                    start_byte: start,
                    end_byte: end,
                    line_count: 2,
                    byte_count: end - start
                }]
            ),
            "before [Pasted Text · 2 lines · 3 B]after"
        );
    }

    #[test]
    fn test_backend_renders_adaptive_header_and_composer() {
        let backend = TestBackend::new(100, 30);
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
        assert!(rendered.contains("◈ Hames"));
        assert!(rendered.contains("New session · Ready"));
        assert!(rendered.contains("[connected]"));
        assert!(rendered.contains("Message Hames"));
        assert!(rendered.contains("─ fixture (medium) · Auto"));
        assert!(rendered.contains("A fresh canvas"));
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
        assert!(rendered.contains("━━━━━━"));
        assert!(rendered.contains("Working · 12s · Esc interrupt"));
        assert!(rendered.contains("[connected]"));
        assert!(!rendered.contains("Shift+Tab mode"));
        let footer_y = terminal.size().unwrap().height - 1;
        let buffer = terminal.backend().buffer();
        assert_eq!(buffer.cell((2, footer_y)).unwrap().fg, Color::White);
        assert_eq!(buffer.cell((3, footer_y)).unwrap().fg, INPUT);
        assert_eq!(buffer.cell((4, footer_y)).unwrap().fg, MUTED);
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
        assert!(rendered.contains("Press"));
        assert!(rendered.contains("Ctrl+D again to delete this entry"));
        assert!(rendered.contains("↑↓ cancel · Esc close"));
        assert!(!rendered.contains("Design pass"));
        assert_eq!(buffer.cell((25, 24)).unwrap().symbol(), "C");
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
        assert_eq!(buffer.cell((11, 9)).unwrap().symbol(), "┌");
        assert_eq!(buffer.cell((88, 9)).unwrap().symbol(), "┐");
        assert_eq!(buffer.cell((11, 9)).unwrap().fg, RULE);
        assert_ne!(buffer.cell((11, 9)).unwrap().fg, MINT);
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
}
