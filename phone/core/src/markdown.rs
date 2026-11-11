use pulldown_cmark::{Alignment, CodeBlockKind, Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use std::ops::Range;

#[derive(uniffi::Record, Clone, Debug, PartialEq, Default)]
pub struct Span {
    pub text: String,
    pub bold: bool,
    pub italic: bool,
    pub code: bool,
    pub strike: bool,
    pub link: Option<String>,
    pub html: bool,
}

#[derive(uniffi::Enum, Clone, Debug, PartialEq)]
pub enum Align {
    None,
    Left,
    Center,
    Right,
}

#[derive(uniffi::Enum, Clone, Debug, PartialEq)]
pub enum Block {
    Heading { level: u8, spans: Vec<Span>, quote: u8 },
    Paragraph { spans: Vec<Span>, quote: u8 },
    Item {
        ordered: bool,
        number: u64,
        depth: u8,
        checked: Option<bool>,
        spans: Vec<Span>,
        quote: u8,
    },
    Code { lang: Option<String>, text: String, quote: u8 },
    Table {
        align: Vec<Align>,
        header: Vec<Vec<Span>>,
        rows: Vec<Vec<Vec<Span>>>,
    },
    Html { html: String },
    Svg { svg: String, animated: bool },
    Image { url: String, alt: String },
    Rule,
}

/// Parses CommonMark with tables, strikethrough and task lists into a flat
/// list of blocks. Never panics; on an internal failure the input comes back
/// as one plain paragraph.
#[uniffi::export]
pub fn parse_markdown(text: String) -> Vec<Block> {
    let src = text.clone();
    match std::panic::catch_unwind(move || parse(&src)) {
        Ok(blocks) => blocks,
        Err(_) => vec![Block::Paragraph {
            spans: vec![Span { text, ..Span::default() }],
            quote: 0,
        }],
    }
}

#[derive(Clone)]
struct ItemInfo {
    ordered: bool,
    number: u64,
    depth: u8,
    checked: Option<bool>,
}

enum Kind {
    Heading(u8),
    Paragraph,
    Item(ItemInfo),
    Cell,
}

struct ListCtx {
    ordered: bool,
    next: u64,
}

#[derive(Default)]
struct TableState {
    align: Vec<Align>,
    header: Vec<Vec<Span>>,
    rows: Vec<Vec<Vec<Span>>>,
    row: Vec<Vec<Span>>,
}

struct Builder<'a> {
    src: &'a str,
    blocks: Vec<Block>,
    quote: u8,
    lists: Vec<ListCtx>,
    item_pending: Option<ItemInfo>,
    cur: Option<(Kind, Vec<Span>)>,
    bold: u32,
    italic: u32,
    strike: u32,
    links: Vec<String>,
    code: Option<(Option<String>, String)>,
    html: Option<String>,
    image: Option<(String, String)>,
    table: Option<TableState>,
}

fn parse(src: &str) -> Vec<Block> {
    let opts = Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TASKLISTS;
    let mut b = Builder {
        src,
        blocks: Vec::new(),
        quote: 0,
        lists: Vec::new(),
        item_pending: None,
        cur: None,
        bold: 0,
        italic: 0,
        strike: 0,
        links: Vec::new(),
        code: None,
        html: None,
        image: None,
        table: None,
    };
    let mut skip_until: Option<usize> = None;
    for (event, range) in Parser::new_ext(src, opts).into_offset_iter() {
        if let Some(end) = skip_until {
            if range.start < end {
                continue;
            }
            skip_until = None;
        }
        if let Some(end) = b.svg_start(&event, &range) {
            skip_until = Some(end);
            continue;
        }
        b.event(event);
    }
    b.flush(true);
    b.blocks
}

fn level(l: HeadingLevel) -> u8 {
    match l {
        HeadingLevel::H1 => 1,
        HeadingLevel::H2 => 2,
        HeadingLevel::H3 => 3,
        HeadingLevel::H4 => 4,
        HeadingLevel::H5 => 5,
        HeadingLevel::H6 => 6,
    }
}

fn starts_with_ci(s: &str, prefix: &str) -> bool {
    s.get(..prefix.len())
        .map(|head| head.eq_ignore_ascii_case(prefix))
        .unwrap_or(false)
}

fn skip_prolog(mut s: &str) -> &str {
    loop {
        s = s.trim_start();
        let next = if s.starts_with("<?") {
            s.find("?>").map(|i| i + 2)
        } else if s.starts_with("<!--") {
            s.find("-->").map(|i| i + 3)
        } else if starts_with_ci(s, "<!doctype") {
            s.find('>').map(|i| i + 1)
        } else {
            None
        };
        match next.and_then(|n| s.get(n..)) {
            Some(rest) => s = rest,
            None => return s,
        }
    }
}

fn open_svg_tag(s: &str) -> bool {
    starts_with_ci(s, "<svg")
        && s.get(4..)
            .and_then(|r| r.chars().next())
            .map(|c| c.is_whitespace() || c == '>' || c == '/')
            .unwrap_or(false)
}

fn svg_end(s: &str) -> Option<usize> {
    let lower = s.to_ascii_lowercase();
    let mut depth = 0i32;
    let mut i = 0;
    while let Some(rel) = lower.get(i..).and_then(|r| r.find("<")) {
        let at = i + rel;
        let rest = lower.get(at..)?;
        if rest.starts_with("</svg") {
            depth -= 1;
            if depth <= 0 {
                let close = rest.find('>')?;
                return Some(at + close + 1);
            }
        } else if open_svg_tag(rest) {
            depth += 1;
        }
        i = at + 1;
    }
    None
}

pub(crate) fn is_animated(svg: &str) -> bool {
    let lower = svg.to_ascii_lowercase();
    if lower.contains("<animate") || lower.contains("@keyframes") || lower.contains("animation:") {
        return true;
    }
    let mut i = 0;
    while let Some(rel) = lower.get(i..).and_then(|r| r.find("<set")) {
        let at = i + rel + 4;
        if lower
            .get(at..)
            .and_then(|r| r.chars().next())
            .map(|c| c.is_whitespace() || c == '/')
            .unwrap_or(false)
        {
            return true;
        }
        i = at;
    }
    false
}

fn blank(spans: &[Span]) -> bool {
    spans.iter().all(|s| !s.html && s.text.trim().is_empty())
}

fn trim_nl(mut s: String) -> String {
    if s.ends_with('\n') {
        s.pop();
        if s.ends_with('\r') {
            s.pop();
        }
    }
    s
}

impl<'a> Builder<'a> {
    fn svg_start(&mut self, event: &Event, range: &Range<usize>) -> Option<usize> {
        if self.quote > 0 || self.code.is_some() || self.html.is_some() || self.image.is_some() {
            return None;
        }
        match event {
            Event::Start(Tag::HtmlBlock) | Event::Start(Tag::Paragraph) => {}
            _ => return None,
        }
        let tail = self.src.get(range.start..)?;
        let trimmed = tail.trim_start();
        let body = skip_prolog(trimmed);
        if !open_svg_tag(body) {
            return None;
        }
        let end_rel = svg_end(body)
            .map(|e| trimmed.len() - body.len() + e)
            .unwrap_or_else(|| range.end.saturating_sub(range.start).min(tail.len()));
        let slice = trimmed.get(..end_rel)?.trim().to_string();
        let abs_end = range.start + (tail.len() - trimmed.len()) + end_rel;
        self.flush(true);
        self.item_pending = None;
        let animated = is_animated(&slice);
        self.blocks.push(Block::Svg { svg: slice, animated });
        Some(abs_end.max(range.start + 1))
    }

    fn flush(&mut self, force: bool) {
        if matches!(self.cur, Some((Kind::Cell, _))) {
            return;
        }
        if let Some((kind, spans)) = self.cur.take() {
            let quote = self.quote;
            match kind {
                Kind::Heading(level) => self.blocks.push(Block::Heading { level, spans, quote }),
                Kind::Paragraph => {
                    if !blank(&spans) {
                        self.blocks.push(Block::Paragraph { spans, quote });
                    }
                }
                Kind::Item(i) => {
                    if force || !blank(&spans) {
                        self.blocks.push(Block::Item {
                            ordered: i.ordered,
                            number: i.number,
                            depth: i.depth,
                            checked: i.checked,
                            spans,
                            quote,
                        });
                    }
                }
                Kind::Cell => {}
            }
        }
    }

    fn start_inline(&mut self, kind: Kind) {
        self.flush(true);
        self.cur = Some((kind, Vec::new()));
    }

    fn ensure_cur(&mut self) {
        if self.cur.is_none() {
            let kind = match self.item_pending.take() {
                Some(info) => Kind::Item(info),
                None => Kind::Paragraph,
            };
            self.cur = Some((kind, Vec::new()));
        }
    }

    fn add(&mut self, text: &str, code: bool, html: bool) {
        if text.is_empty() {
            return;
        }
        if let Some((_, alt)) = self.image.as_mut() {
            if !html {
                alt.push_str(text);
            }
            return;
        }
        self.ensure_cur();
        let span = Span {
            text: text.to_string(),
            bold: !html && self.bold > 0,
            italic: !html && self.italic > 0,
            code,
            strike: !html && self.strike > 0,
            link: if html { None } else { self.links.last().cloned() },
            html,
        };
        if let Some((_, spans)) = self.cur.as_mut() {
            if let Some(last) = spans.last_mut() {
                if !html
                    && !last.html
                    && last.bold == span.bold
                    && last.italic == span.italic
                    && last.code == span.code
                    && last.strike == span.strike
                    && last.link == span.link
                {
                    last.text.push_str(&span.text);
                    return;
                }
            }
            spans.push(span);
        }
    }

    fn finish_image(&mut self, url: String, alt: String) {
        let in_cell = matches!(self.cur, Some((Kind::Cell, _)));
        if in_cell {
            let text = if alt.is_empty() { url } else { alt };
            self.add(&text, false, false);
            return;
        }
        self.flush(false);
        self.item_pending = None;
        self.blocks.push(Block::Image { url, alt });
        self.cur = Some((Kind::Paragraph, Vec::new()));
    }

    fn event(&mut self, event: Event) {
        match event {
            Event::Start(tag) => self.start(tag),
            Event::End(tag) => self.end(tag),
            Event::Text(t) => {
                if let Some((_, text)) = self.code.as_mut() {
                    text.push_str(&t);
                } else if let Some(h) = self.html.as_mut() {
                    h.push_str(&t);
                } else {
                    self.add(&t, false, false);
                }
            }
            Event::Code(t) => self.add(&t, true, false),
            Event::Html(t) => {
                if let Some(h) = self.html.as_mut() {
                    h.push_str(&t);
                } else {
                    self.add(&t, false, true);
                }
            }
            Event::InlineHtml(t) => self.add(&t, false, true),
            Event::SoftBreak => self.add(" ", false, false),
            Event::HardBreak => self.add("\n", false, false),
            Event::Rule => {
                self.flush(true);
                self.item_pending = None;
                self.blocks.push(Block::Rule);
            }
            Event::TaskListMarker(done) => {
                if let Some(info) = self.item_pending.as_mut() {
                    info.checked = Some(done);
                } else if let Some((Kind::Item(info), _)) = self.cur.as_mut() {
                    info.checked = Some(done);
                }
            }
            _ => {}
        }
    }

    fn start(&mut self, tag: Tag) {
        match tag {
            Tag::Paragraph => {
                self.flush(true);
                let kind = match self.item_pending.take() {
                    Some(info) => Kind::Item(info),
                    None => Kind::Paragraph,
                };
                self.cur = Some((kind, Vec::new()));
            }
            Tag::Heading { level: l, .. } => {
                self.item_pending = None;
                self.start_inline(Kind::Heading(level(l)));
            }
            Tag::BlockQuote(_) => {
                self.flush(true);
                self.quote = self.quote.saturating_add(1);
            }
            Tag::CodeBlock(kind) => {
                self.flush(true);
                self.item_pending = None;
                let lang = match kind {
                    CodeBlockKind::Fenced(info) => info
                        .split_whitespace()
                        .next()
                        .map(|s| s.to_string())
                        .filter(|s| !s.is_empty()),
                    CodeBlockKind::Indented => None,
                };
                self.code = Some((lang, String::new()));
            }
            Tag::HtmlBlock => {
                self.flush(true);
                self.item_pending = None;
                self.html = Some(String::new());
            }
            Tag::List(start) => {
                let had_cur = self.cur.is_some();
                self.flush(true);
                if !had_cur {
                    if let Some(info) = self.item_pending.take() {
                        self.blocks.push(Block::Item {
                            ordered: info.ordered,
                            number: info.number,
                            depth: info.depth,
                            checked: info.checked,
                            spans: Vec::new(),
                            quote: self.quote,
                        });
                    }
                }
                self.lists.push(ListCtx {
                    ordered: start.is_some(),
                    next: start.unwrap_or(0),
                });
            }
            Tag::Item => {
                self.flush(true);
                let depth = self.lists.len().saturating_sub(1).min(255) as u8;
                let (ordered, number) = match self.lists.last_mut() {
                    Some(l) if l.ordered => {
                        let n = l.next;
                        l.next = l.next.saturating_add(1);
                        (true, n)
                    }
                    _ => (false, 0),
                };
                self.item_pending = Some(ItemInfo { ordered, number, depth, checked: None });
            }
            Tag::Table(aligns) => {
                self.flush(true);
                self.item_pending = None;
                self.table = Some(TableState {
                    align: aligns
                        .into_iter()
                        .map(|a| match a {
                            Alignment::None => Align::None,
                            Alignment::Left => Align::Left,
                            Alignment::Center => Align::Center,
                            Alignment::Right => Align::Right,
                        })
                        .collect(),
                    ..TableState::default()
                });
            }
            Tag::TableCell => {
                self.cur = Some((Kind::Cell, Vec::new()));
            }
            Tag::Emphasis => self.italic += 1,
            Tag::Strong => self.bold += 1,
            Tag::Strikethrough => self.strike += 1,
            Tag::Link { dest_url, .. } => self.links.push(dest_url.to_string()),
            Tag::Image { dest_url, .. } => {
                self.image = Some((dest_url.to_string(), String::new()));
            }
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph => self.flush(false),
            TagEnd::Heading(_) => self.flush(true),
            TagEnd::BlockQuote(_) => {
                self.flush(true);
                self.quote = self.quote.saturating_sub(1);
            }
            TagEnd::CodeBlock => {
                if let Some((lang, text)) = self.code.take() {
                    let text = trim_nl(text);
                    let kind = lang.as_deref().map(|l| l.to_ascii_lowercase());
                    match kind.as_deref() {
                        Some("svg") => {
                            let animated = is_animated(&text);
                            self.blocks.push(Block::Svg { svg: text, animated });
                        }
                        Some("html") => self.blocks.push(Block::Html { html: text }),
                        _ => self.blocks.push(Block::Code { lang, text, quote: self.quote }),
                    }
                }
            }
            TagEnd::HtmlBlock => {
                if let Some(html) = self.html.take() {
                    let html = html.trim_end().to_string();
                    if !html.trim().is_empty() {
                        self.blocks.push(Block::Html { html });
                    }
                }
            }
            TagEnd::List(_) => {
                self.flush(true);
                self.lists.pop();
            }
            TagEnd::Item => {
                if self.cur.is_some() {
                    self.flush(true);
                } else if let Some(info) = self.item_pending.take() {
                    self.blocks.push(Block::Item {
                        ordered: info.ordered,
                        number: info.number,
                        depth: info.depth,
                        checked: info.checked,
                        spans: Vec::new(),
                        quote: self.quote,
                    });
                }
            }
            TagEnd::Emphasis => self.italic = self.italic.saturating_sub(1),
            TagEnd::Strong => self.bold = self.bold.saturating_sub(1),
            TagEnd::Strikethrough => self.strike = self.strike.saturating_sub(1),
            TagEnd::Link => {
                self.links.pop();
            }
            TagEnd::Image => {
                if let Some((url, alt)) = self.image.take() {
                    self.finish_image(url, alt);
                }
            }
            TagEnd::TableCell => {
                if let Some((Kind::Cell, spans)) = self.cur.take() {
                    if let Some(t) = self.table.as_mut() {
                        t.row.push(spans);
                    }
                }
            }
            TagEnd::TableHead => {
                if let Some(t) = self.table.as_mut() {
                    t.header = std::mem::take(&mut t.row);
                }
            }
            TagEnd::TableRow => {
                if let Some(t) = self.table.as_mut() {
                    let row = std::mem::take(&mut t.row);
                    t.rows.push(row);
                }
            }
            TagEnd::Table => {
                if let Some(t) = self.table.take() {
                    self.blocks.push(Block::Table { align: t.align, header: t.header, rows: t.rows });
                }
            }
            _ => {}
        }
    }
}
