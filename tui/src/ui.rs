//! All drawing lives here: the theme, the centered column, and one
//! widget per visual element. `app.rs` keeps state and logic; anything
//! visual goes through this module.
//!
//! To add a feature:
//! - a new kind of line in the ledger: add a `Kind` variant in app.rs,
//!     then a `render` arm and a theme entry if it needs one
//! - a new overlay: add a `draw_*` function and call it from `draw`
//! - a new color: add a constant below, never inline an index

use crate::app::{App, Kind, Mode, SLASH};
use crate::md;

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, Paragraph};
use ratatui::Frame;

// ---------------------------------------------------------------- theme

pub const ACCENT: u8 = 214;
pub const TEXT: u8 = 15;
pub const CODE: u8 = 110;
pub const PLAIN: u8 = 250;
pub const QUIET: u8 = 244;

const SPINNER: [char; 4] = ['|', '/', '-', '\\'];

/// width of the central column; the ledger, input, and overlays all
/// live inside it, the margins stay empty
pub const COLUMN: u16 = 100;

fn accent(dim: bool) -> Style {
    let style = Style::default().fg(Color::Indexed(ACCENT));
    if dim {
        style.add_modifier(Modifier::DIM)
    } else {
        style
    }
}

fn text(selected: bool) -> Style {
    let style = Style::default().fg(Color::Indexed(TEXT));
    if selected {
        style.add_modifier(Modifier::REVERSED)
    } else {
        style
    }
}

// ---------------------------------------------------------------- draw

impl App {
    pub fn draw(&mut self, frame: &mut Frame) {
        if let Mode::Picker { records, pos, query } = &self.mode {
            draw_picker(frame, records, *pos, query);
            return;
        }
        let area = column(frame.area());
        let rows = area.height as usize;

        let input_width = area.width.saturating_sub(4).max(20);
        let inner_width = input_width as usize - 2;
        let wrapped = wrap_input(&self.buffer, inner_width.saturating_sub(2).max(8), self.cursor);
        let input_h = (wrapped.rows.len() + 2).clamp(3, 10) as u16;
        let layout = Layout::vertical([
            Constraint::Length(1),
            Constraint::Min(1),
            Constraint::Length(1),
            Constraint::Length(input_h),
        ])
        .split(area);
        draw_header(self, frame, layout[0], area.width as usize);

        let width = area.width.saturating_sub(2).max(16) as usize;
        let visual = self.visual(width);
        let ledger_h = rows.saturating_sub(3 + input_h as usize).max(1);
        self.scroll = self.scroll.min(visual.len().saturating_sub(ledger_h));
        let end = visual.len() - self.scroll;
        let start = end.saturating_sub(ledger_h);
        let shown = &visual[start..end];
        self.visible = shown
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.to_string())
                    .collect::<String>()
            })
            .collect();
        self.visible_y = layout[1].y;
        frame.render_widget(Paragraph::new(shown.to_vec()), layout[1]);

        draw_status(self, frame, layout[2]);

        let box_x = area.x + (area.width - input_width) / 2;
        let box_y = layout[3].y;
        draw_slash(self, frame, box_x, box_y, input_width, inner_width);
        draw_input(frame, box_x, box_y, input_width, inner_width, &wrapped);
    }
}

fn column(full: Rect) -> Rect {
    let width = full.width.min(COLUMN);
    Rect {
        x: full.x + (full.width - width) / 2,
        width,
        ..full
    }
}

fn draw_header(app: &App, frame: &mut Frame, area: Rect, cols: usize) {
    let title = app
        .record
        .as_ref()
        .map(|record| record.title.clone())
        .unwrap_or_default();
    let mut header = format!(" diablo · {}", app.cwd.display());
    if !title.is_empty() && title != "new session" {
        let room = cols.saturating_sub(header.chars().count() + 4);
        header += &format!(" · {}", title.chars().take(room).collect::<String>());
    }
    frame.render_widget(
        Paragraph::new(Line::from(Span::styled(header, accent(false).add_modifier(Modifier::REVERSED)))),
        area,
    );
}

fn draw_status(
    app: &mut App, frame: &mut Frame, area: Rect) {
    let mut status = if app.busy {
        app.spinner = (app.spinner + 1) % SPINNER.len();
        if app.esc_armed {
            format!(
                " working {} {} tool calls · esc again to stop now",
                SPINNER[app.spinner], app.tool_calls
            )
        } else {
            format!(
                " working {} {} tool calls · esc esc interrupts",
                SPINNER[app.spinner], app.tool_calls
            )
        }
    } else {
        " ready · /new /relaunch /quit".to_string()
    };
    if !app.queued.is_empty() {
        status += &format!(" · {} queued", app.queued.len());
    }
    if app.scroll > 0 {
        status = format!("{status} · {} lines up, esc returns to live", app.scroll);
    }
    frame.render_widget(
        Paragraph::new(Line::from(Span::styled(status, Style::default().add_modifier(Modifier::DIM)))),
        area,
    );
}

fn draw_slash(app: &App, frame: &mut Frame, box_x: u16, box_y: u16, input_width: u16, inner_width: usize) {
    let matches = slash_matches(&app.buffer);
    if matches.is_empty() {
        return;
    }
    let at = app.slash_pos.min(matches.len() - 1);
    let height = matches.len() as u16 + 2;
    let area = Rect::new(box_x, box_y.saturating_sub(height), input_width, height);
    frame.render_widget(Clear, area);
    frame.render_widget(
        Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(accent(true)),
        area,
    );
    for (r, &i) in matches.iter().enumerate() {
        let (name, detail) = SLASH[i];
        frame.render_widget(
            Paragraph::new(Line::from(Span::styled(format!(" {name:<10} {detail}"), text(r == at)))),
            Rect::new(box_x + 1, area.y + 1 + r as u16, inner_width as u16, 1),
        );
    }
}

fn draw_input(
    frame: &mut Frame,
    box_x: u16,
    box_y: u16,
    input_width: u16,
    inner_width: usize,
    wrapped: &Wrapped,
) {
    frame.render_widget(
        Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(accent(true)),
        Rect::new(box_x, box_y, input_width, wrapped.rows.len() as u16 + 2),
    );
    let show = wrapped.rows.len().min(inner_width.max(2) / 1); // rows fit by input_h clamp
    let show = show.min(wrapped.rows.len());
    let skip = wrapped.rows.len().saturating_sub(show);
    for (r, row) in wrapped.rows[skip..].iter().enumerate() {
        let prefix = if r + skip == 0 { "▸ " } else { "  " };
        frame.render_widget(
            Paragraph::new(Line::from(Span::styled(
                format!("{prefix}{row}"),
                Style::default().fg(Color::Indexed(TEXT)),
            ))),
            Rect::new(box_x + 1, box_y + 1 + r as u16, inner_width as u16, 1),
        );
    }
    let (crow, ccol) = wrapped.cursor;
    if crow >= skip {
        frame.set_cursor_position((box_x + 3 + ccol as u16, box_y + 1 + (crow - skip) as u16));
    }
}

fn draw_picker(frame: &mut Frame, records: &[crate::api::SessionInfo], pos: usize, query: &str) {
    let rows = frame.area().height as usize;
    let shown = filtered(records, query);
    let header = if query.is_empty() {
        " resume a session: enter picks, esc starts fresh, type to filter, newest first".to_string()
    } else {
        format!(" filter: {query}  ({}/{})", shown.len(), records.len())
    };
    frame.render_widget(
        Paragraph::new(Line::from(Span::styled(header, accent(false).add_modifier(Modifier::REVERSED)))),
        Rect::new(frame.area().x, frame.area().y, frame.area().width, 1),
    );
    let height = rows.saturating_sub(2).max(1);
    let top = pos.saturating_sub(height - 1).min(shown.len().saturating_sub(height));
    for (drawn, index) in (top..shown.len().min(top + height)).enumerate() {
        let record = shown[index];
        let row = format!(
            " {:>4}  {:38}  {}",
            record.ago,
            record.title.chars().take(38).collect::<String>(),
            record.cwd,
        );
        frame.render_widget(
            Paragraph::new(Line::from(Span::styled(row, text(index == pos)))),
            Rect::new(frame.area().x, 1 + drawn as u16, frame.area().width, 1),
        );
    }
}

// ---------------------------------------------------------------- ledger

impl App {
    pub(crate) fn visual(&mut self, width: usize) -> Vec<Line<'static>> {
        let key = (width, self.lines.len(), self.gen);
        if self.cache.as_ref().map(|(cache_key, _)| *cache_key) != Some(key) {
            let mut out: Vec<Line<'static>> = Vec::new();
            for (kind, text) in &self.lines {
                match kind {
                    Kind::Diablo => {
                        for runs in md::render(text, width) {
                            out.push(md::to_ratatui(&runs));
                        }
                    }
                    Kind::Label => out.push(Line::from(Span::styled(
                        "  diablo  ".to_string(),
                        Style::default().fg(Color::Indexed(ACCENT)).add_modifier(Modifier::BOLD),
                    ))),
                    Kind::You => {
                        let mut runs = vec![("  you  ".to_string(), md::Style::Normal)];
                        runs.extend(md::inline_runs(text, md::Style::Normal));
                        for wrapped in md::wrap_runs(&runs, width, "       ") {
                            out.push(md::to_ratatui(&wrapped));
                        }
                    }
                    Kind::Thinking => {
                        let runs = vec![(format!("  ~ {}", text), md::Style::Dim)];
                        for wrapped in md::wrap_runs(&runs, width, "    ") {
                            out.push(md::to_ratatui(&wrapped));
                        }
                    }
                    Kind::Tool | Kind::Result | Kind::Dim | Kind::Notice => {
                        if matches!(kind, Kind::Tool | Kind::Result) {
                            let body = text.strip_prefix("· ").unwrap_or(text);
                            let (label, rest) = match body.split_once(": ") {
                                Some((label, rest)) => (label.to_string(), rest.to_string()),
                                None => ("tool".to_string(), body.to_string()),
                            };
                            let (command, output) = match rest.split_once("  = ") {
                                Some((command, output)) => (command.to_string(), Some(output.to_string())),
                                None => (rest, None),
                            };
                            match output {
                                Some(output) => draw_box(
                                    &label,
                                    &format!("{command}\n{output}"),
                                    width,
                                    md::Style::Head,
                                    &mut out,
                                ),
                                None => {
                                    let runs = vec![(format!("  {label}: {command}"), md::Style::Dim)];
                                    for wrapped in md::wrap_runs(&runs, width, "  ") {
                                        out.push(md::to_ratatui(&wrapped));
                                    }
                                }
                            }
                        } else {
                            let runs = vec![(text.clone(), md::Style::Dim)];
                            for wrapped in md::wrap_runs(&runs, width, "    ") {
                                out.push(md::to_ratatui(&wrapped));
                            }
                        }
                    }
                }
            }
            self.cache = Some((key, out));
        }
        self.cache
            .as_ref()
            .map(|(_, lines)| lines.clone())
            .unwrap_or_default()
    }
}

pub(crate) fn draw_box(label: &str, content: &str, width: usize, style: md::Style, out: &mut Vec<Line<'static>>) {
    let indent = 2;
    let inner_max = width.saturating_sub(indent + 6).max(12);
    let trimmed = content.trim();
    let longest = trimmed.lines().map(|l| l.chars().count()).max().unwrap_or(0);
    let inner = longest.clamp(4, inner_max);
    let needs_wrap = longest > inner_max;
    let label_w = label.chars().count();
    let top_dashes = inner.saturating_sub(label_w + 1);
    out.push(Line::from(Span::styled(
        format!("{}╭─ {} {}╮", " ".repeat(indent), label, "─".repeat(top_dashes)),
        md::style_of(style),
    )));
    let body_rows: Vec<String> = if needs_wrap {
        md::wrap_runs(&[(trimmed.to_string(), style)], inner, "")
            .iter()
            .map(|runs| runs.iter().map(|(t, _)| t.clone()).collect::<String>())
            .collect()
    } else {
        trimmed.lines().map(|l| l.to_string()).collect()
    };
    for row in body_rows {
        let pad = inner.saturating_sub(row.chars().count());
        out.push(Line::from(Span::styled(
            format!("{}│ {}{} │", " ".repeat(indent), row, " ".repeat(pad)),
            md::style_of(style),
        )));
    }
    out.push(Line::from(Span::styled(
        format!("{}╰{}╯", " ".repeat(indent), "─".repeat(inner + 2)),
        md::style_of(style),
    )));
}

// ---------------------------------------------------------------- helpers

pub(crate) fn slash_matches(text: &str) -> Vec<usize> {
    let q = text.trim_start().to_lowercase();
    if !q.starts_with('/') {
        return Vec::new();
    }
    let q = q.trim_start_matches('/');
    SLASH
        .iter()
        .enumerate()
        .filter(|(_, (name, _))| name[1..].starts_with(q) || name[1..].contains(&format!(" {q}")))
        .map(|(i, _)| i)
        .collect()
}

pub(crate) fn filtered<'a>(
    records: &'a [crate::api::SessionInfo],
    query: &str,
) -> Vec<&'a crate::api::SessionInfo> {
    if query.is_empty() {
        return records.iter().collect();
    }
    let needle = query.to_lowercase();
    records
        .iter()
        .filter(|record| {
            record.title.to_lowercase().contains(&needle) || record.cwd.to_lowercase().contains(&needle)
        })
        .collect()
}

pub(crate) struct Wrapped {
    pub rows: Vec<String>,
    pub cursor: (usize, usize),
}

pub(crate) fn wrap_input(buffer: &str, width: usize, cursor_pos: usize) -> Wrapped {
    let width = width.max(8);
    let mut rows: Vec<Vec<char>> = Vec::new();
    let mut idx_rows: Vec<Vec<usize>> = Vec::new();
    let mut cur: Vec<char> = Vec::new();
    let mut cur_idx: Vec<usize> = Vec::new();
    for (i, c) in buffer.chars().enumerate() {
        if c == '\n' {
            rows.push(std::mem::take(&mut cur));
            idx_rows.push(std::mem::take(&mut cur_idx));
            continue;
        }
        cur.push(c);
        cur_idx.push(i);
        if cur.len() >= width {
            match cur.iter().rposition(|ch| *ch == ' ') {
                Some(pos) if pos > 0 && pos + 1 < cur.len() => {
                    let rest_c: Vec<char> = cur.split_off(pos + 1);
                    let rest_i: Vec<usize> = cur_idx.split_off(pos + 1);
                    cur.pop();
                    rows.push(std::mem::take(&mut cur));
                    idx_rows.push(std::mem::take(&mut cur_idx));
                    cur = rest_c;
                    cur_idx = rest_i;
                }
                _ => {
                    rows.push(std::mem::take(&mut cur));
                    idx_rows.push(std::mem::take(&mut cur_idx));
                }
            }
        }
    }
    rows.push(cur);
    idx_rows.push(cur_idx);
    let mut cursor = (rows.len().saturating_sub(1), rows.last().map(Vec::len).unwrap_or(0));
    for (r, row_idx) in idx_rows.iter().enumerate() {
        if let Some(col) = row_idx.iter().position(|&i| i == cursor_pos) {
            cursor = (r, col);
            break;
        }
        if !row_idx.is_empty() && row_idx[0] > cursor_pos {
            cursor = (r, 0);
            break;
        }
    }
    let text_rows = rows.iter().map(|row| row.iter().collect::<String>()).collect();
    Wrapped { rows: text_rows, cursor }
}


