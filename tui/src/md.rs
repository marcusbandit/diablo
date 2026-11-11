use ratatui::text::Line;


#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Style {
    Normal,
    Bold,
    Em,
    Code,
    CodeBlock,
    CodeKeyword,
    CodeString,
    CodeComment,
    CodeNumber,
    Link,
    Head,
    Quote,
    Dim,
}

pub type Run = (String, Style);

pub fn inline_runs(text: &str, base: Style) -> Vec<Run> {
    let chars: Vec<char> = text.chars().collect();
    let mut runs = Vec::new();
    let mut plain = String::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '`' {
            if let Some(end) = find_char(&chars, i + 1, '`') {
                flush(&mut runs, &mut plain, base);
                runs.push((chars[i + 1..end].iter().collect(), Style::Code));
                i = end + 1;
                continue;
            }
        }
        if c == '*' && i + 1 < chars.len() && chars[i + 1] == '*' {
            if let Some(end) = find_seq(&chars, i + 2, "**") {
                flush(&mut runs, &mut plain, base);
                let inner: String = chars[i + 2..end].iter().collect();
                runs.extend(inline_runs(&inner, Style::Bold));
                i = end + 2;
                continue;
            }
        }
        if c == '*' {
            if let Some(end) = find_char(&chars, i + 1, '*') {
                let inner: String = chars[i + 1..end].iter().collect();
                if !inner.trim().is_empty() && !inner.contains(char::is_whitespace) {
                    flush(&mut runs, &mut plain, base);
                    runs.push((inner, Style::Em));
                    i = end + 1;
                    continue;
                }
            }
        }
        if c == '~' && i + 1 < chars.len() && chars[i + 1] == '~' {
            if let Some(end) = find_seq(&chars, i + 2, "~~") {
                flush(&mut runs, &mut plain, base);
                let inner: String = chars[i + 2..end].iter().collect();
                runs.extend(inline_runs(&inner, Style::Dim));
                i = end + 2;
                continue;
            }
        }
        if c == '[' {
            if let Some((label_end, url)) = parse_link(&chars, i) {
                flush(&mut runs, &mut plain, base);
                let label: String = chars[i + 1..label_end].iter().collect();
                runs.push((label, Style::Link));
                runs.push((format!(" ({url})"), Style::Dim));
                i = url_end_index(&chars, label_end);
                continue;
            }
        }
        plain.push(c);
        i += 1;
    }
    if !plain.is_empty() {
        runs.push((plain, base));
    }
    if runs.is_empty() && !text.is_empty() {
        runs.push((text.to_string(), base));
    }
    runs
}

fn flush(runs: &mut Vec<Run>, plain: &mut String, base: Style) {
    if !plain.is_empty() {
        runs.push((std::mem::take(plain), base));
    }
}

fn find_char(chars: &[char], from: usize, needle: char) -> Option<usize> {
    (from..chars.len()).find(|&index| chars[index] == needle)
}

fn find_seq(chars: &[char], from: usize, needle: &str) -> Option<usize> {
    let needle_chars: Vec<char> = needle.chars().collect();
    (from..=chars.len().saturating_sub(needle_chars.len())).find(|&index| {
        chars[index..index + needle_chars.len()] == needle_chars[..]
    })
}

fn parse_link(chars: &[char], open: usize) -> Option<(usize, String)> {
    let close = find_char(chars, open + 1, ']')?;
    if chars.get(close + 1) != Some(&'(') {
        return None;
    }
    let url_close = find_char(chars, close + 2, ')')?;
    Some((close, chars[close + 2..url_close].iter().collect()))
}

fn url_end_index(chars: &[char], label_end: usize) -> usize {
    find_char(chars, label_end, ')').map(|end| end + 1).unwrap_or(label_end)
}

pub fn wrap_runs(runs: &[Run], width: usize, hang: &str) -> Vec<Vec<Run>> {
    let width = width.max(8);
    let room = width.saturating_sub(hang.chars().count()).max(8);
    let mut tokens: Vec<Run> = Vec::new();
    for (text, style) in runs {
        let mut current = String::new();
        for c in text.chars() {
            if c.is_whitespace() {
                if !current.is_empty() {
                    tokens.push((std::mem::take(&mut current), *style));
                }
                tokens.push((c.to_string(), *style));
            } else {
                current.push(c);
            }
        }
        if !current.is_empty() {
            tokens.push((current, *style));
        }
    }
    let mut lines: Vec<Vec<Run>> = Vec::new();
    let mut current: Vec<Run> = Vec::new();
    let mut length = 0usize;
    for (token, style) in tokens {
        let mut token = token;
        while token.chars().count() > room {
            if !current.is_empty() {
                lines.push(std::mem::take(&mut current));
                length = 0;
            }
            let cut: String = token.chars().take(room).collect();
            let rest: String = token.chars().skip(room).collect();
            lines.push(vec![(cut, style)]);
            token = rest;
        }
        if length + token.chars().count() > room && !current.is_empty() {
            while current.last().map(|(text, _)| text == " ").unwrap_or(false) {
                current.pop();
            }
            lines.push(std::mem::take(&mut current));
            length = 0;
            if token == " " {
                continue;
            }
        }
        if current.is_empty() && !lines.is_empty() && !hang.is_empty() {
            current.push((hang.to_string(), Style::Normal));
            length = hang.chars().count();
        }
        length += token.chars().count();
        current.push((token, style));
    }
    if !current.is_empty() {
        lines.push(current);
    }
    if lines.is_empty() {
        lines.push(vec![(String::new(), Style::Normal)]);
    }
    lines
}

fn wrap_plain(text: &str, width: usize) -> Vec<String> {
    textwrap::wrap(text, width.max(8).saturating_sub(1))
        .into_iter()
        .map(|line| line.to_string())
        .collect()
}

pub fn render(text: &str, width: usize) -> Vec<Vec<Run>> {
    let width = width.max(16);
    let lines: Vec<&str> = text.lines().collect();
    let mut out: Vec<Vec<Run>> = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        let trimmed = line.trim();
        if trimmed.starts_with("```") {
            i += 1;
            let mut code: Vec<String> = Vec::new();
            while i < lines.len() && !lines[i].trim().starts_with("```") {
                code.push(lines[i].to_string());
                i += 1;
            }
            i += 1;
            let mut pieces: Vec<String> = Vec::new();
            for raw in code {
                let wrapped = textwrap::wrap(&raw, width.saturating_sub(8).max(8))
                    .into_iter()
                    .map(|piece| piece.to_string())
                    .collect::<Vec<_>>();
                pieces.extend(if wrapped.is_empty() { vec![String::new()] } else { wrapped });
            }
            let inner = pieces
                .iter()
                .map(|piece| piece.chars().count())
                .max()
                .unwrap_or(8)
                .clamp(8, width - 6);
            out.push(vec![(format!("┌{}┐", "─".repeat(inner + 2)), Style::Dim)]);
            for piece in pieces {
                let mut runs = vec![
                    ("│ ".to_string(), Style::Dim),
                ];
                runs.extend(wrap_runs(&code_runs(&piece), inner, "").into_iter().flatten());
                let used: usize = runs.iter().map(|(t, _)| t.chars().count()).sum();
                runs.push((" ".repeat(inner.saturating_sub(used)), Style::CodeBlock));
                runs.push((" │".to_string(), Style::Dim));
                out.push(runs);
            }
            out.push(vec![(format!("└{}┘", "─".repeat(inner + 2)), Style::Dim)]);
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix('#') {
            let level = rest.chars().take_while(|c| *c == '#').count();
            if (1..=6).contains(&level) && rest[level..].starts_with(' ') {
                let text = rest[level..].trim().to_string();
                out.push(vec![("▌ ".to_string(), Style::Head), (text, Style::Head)]);
                i += 1;
                continue;
            }
        }
        if is_hrule(trimmed) {
            out.push(vec![("─".repeat(width - 1), Style::Dim)]);
            i += 1;
            continue;
        }
        if trimmed.starts_with('|') && i + 1 < lines.len() && is_table_sep(lines[i + 1].trim()) {
            let mut rows: Vec<Vec<String>> = Vec::new();
            while i < lines.len() && lines[i].trim().starts_with('|') {
                let cells: Vec<String> = lines[i]
                    .trim()
                    .trim_matches('|')
                    .split('|')
                    .map(|cell| cell.trim().to_string())
                    .collect();
                if cells.iter().any(|cell| !cell.is_empty()) {
                    rows.push(cells);
                }
                i += 1;
            }
            if rows.len() >= 2 {
                rows.remove(1);
            }
            let cols = rows.iter().map(|row| row.len()).max().unwrap_or(1);
            let widths: Vec<usize> = (0..cols)
                .map(|c| {
                    rows.iter()
                        .map(|row| row.get(c).map(|cell| cell.chars().count()).unwrap_or(0))
                        .max()
                        .unwrap_or(0)
                        .min(24)
                })
                .collect();
            for (r, row) in rows.iter().enumerate() {
                let mut segments = vec![("  ".to_string(), Style::Normal)];
                for c in 0..cols {
                    let cell = row.get(c).cloned().unwrap_or_default();
                    segments.push((
                        format!("{:<width$}", cell, width = widths[c]),
                        if r == 0 { Style::Bold } else { Style::Normal },
                    ));
                    if c < cols - 1 {
                        segments.push((" │ ".to_string(), Style::Dim));
                    }
                }
                out.push(segments);
            }
            continue;
        }
        let (indent_len, marker) = if let Some(rest) = trimmed.strip_prefix("- ")
            .or_else(|| trimmed.strip_prefix("* "))
            .or_else(|| trimmed.strip_prefix("+ "))
        {
            let level = (line.len() - line.trim_start().len()) / 2;
            (4 * level.min(3) + 2, format!("• {rest}"))
        } else {
            match numbered(trimmed) {
                Some((number, rest)) => {
                    let level = (line.len() - line.trim_start().len()) / 2;
                    (4 * level.min(3) + 2, format!("{number}. {rest}"))
                }
                None => (0, String::new()),
            }
        };
        if marker != String::new() || indent_len > 0 {
            let hang = format!("{:^width$}", "", width = indent_len);
            out.extend(wrap_runs(&inline_runs(&marker, Style::Normal), width, &hang));
            i += 1;
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix('>') {
            let content = rest.trim_start();
            let mut runs = vec![("▌ ".to_string(), Style::Quote)];
            runs.extend(inline_runs(content, Style::Quote));
            out.extend(wrap_runs(&runs, width, "  "));
            i += 1;
            continue;
        }
        if trimmed.is_empty() {
            out.push(vec![(String::new(), Style::Normal)]);
            i += 1;
            continue;
        }
        let mut para = vec![line];
        while i + 1 < lines.len() {
            let next = lines[i + 1];
            let next_trim = next.trim();
            if next_trim.is_empty()
                || next_trim.starts_with("```")
                || next_trim.starts_with('#')
                || is_hrule(next_trim)
                || next_trim.starts_with('|')
                || next_trim.starts_with('>')
                || next_trim.starts_with("- ")
                || next_trim.starts_with("* ")
                || next_trim.starts_with("+ ")
                || numbered(next_trim).is_some()
            {
                break;
            }
            para.push(next);
            i += 1;
        }
        let joined = para.join(" ");
        out.extend(wrap_runs(&inline_runs(&joined, Style::Normal), width, ""));
        i += 1;
    }
    if out.is_empty() {
        out.push(vec![(String::new(), Style::Normal)]);
    }
    out
}

fn numbered(line: &str) -> Option<(String, &str)> {
    let digits: usize = line.chars().take_while(|c| c.is_ascii_digit()).count();
    if digits == 0 {
        return None;
    }
    let rest = line.get(digits..)?;
    let rest = rest.strip_prefix(". ")?;
    Some((line.get(..digits)?.to_string(), rest))
}

fn is_hrule(line: &str) -> bool {
    let trimmed = line.trim();
    if trimmed.len() < 3 {
        return false;
    }
    let chars: Vec<char> = trimmed.chars().collect();
    let first = chars[0];
    (first == '-' || first == '*' || first == '_')
        && chars.iter().all(|c| *c == first || c.is_whitespace())
        && chars.iter().any(|c| *c == first)
}

fn is_table_sep(line: &str) -> bool {
    let body = line.trim().trim_matches('|');
    if body.is_empty() {
        return false;
    }
    body.split('|')
        .all(|cell| {
            let cell = cell.trim();
            !cell.is_empty()
                && cell.chars().all(|c| c == '-' || c == ':' || c == ' ')
        })
}

const KEYWORDS: &[&str] = &[
    "as", "async", "await", "break", "case", "catch", "class", "const", "continue", "def", "default",
    "do", "elif", "else", "enum", "except", "fn", "for", "from", "function", "if", "impl", "import",
    "in", "is", "let", "loop", "match", "mod", "mut", "not", "or", "and", "pass", "pub", "raise",
    "return", "self", "static", "struct", "super", "then", "this", "trait", "try", "type", "use",
    "var", "while", "with", "yield",
];

pub fn code_runs(line: &str) -> Vec<Run> {
    let chars: Vec<char> = line.chars().collect();
    let mut runs: Vec<Run> = Vec::new();
    let mut plain = String::new();
    let mut i = 0;
    while i < chars.len() {
        let rest: String = chars[i..].iter().collect();
        if rest.starts_with('#') || rest.starts_with("//") {
            flush_runs(&mut runs, &mut plain);
            runs.push((rest, Style::CodeComment));
            return runs;
        }
        if c_like(&chars, i) {
            let close = find_seq(&chars, i + 1, "*/");
            let end = close.unwrap_or(chars.len());
            flush_runs(&mut runs, &mut plain);
            runs.push((chars[i..end + close.map(|c| c + 2).unwrap_or(end).min(chars.len()) - i].iter().collect(), Style::CodeComment));
            i = close.map(|c| c + 2).unwrap_or(chars.len());
            continue;
        }
        if chars[i] == '"' || chars[i] == '\'' {
            let quote = chars[i];
            let mut end = i + 1;
            while end < chars.len() && chars[end] != quote {
                if chars[end] == '\\' {
                    end += 1;
                }
                end += 1;
            }
            end = end.min(chars.len() - 1);
            flush_runs(&mut runs, &mut plain);
            runs.push((chars[i..=end].iter().collect(), Style::CodeString));
            i = end + 1;
            continue;
        }
        if chars[i].is_ascii_digit() {
            let mut end = i;
            while end < chars.len() && (chars[end].is_ascii_digit() || chars[end] == '.' || chars[end] == '_') {
                end += 1;
            }
            let before = i.checked_sub(1).map(|p| chars[p]);
            if !before.map(|c| c.is_alphanumeric() || c == '_').unwrap_or(false) {
                flush_runs(&mut runs, &mut plain);
                runs.push((chars[i..end].iter().collect(), Style::CodeNumber));
                i = end;
                continue;
            }
        }
        if chars[i].is_alphabetic() || chars[i] == '_' {
            let mut end = i;
            while end < chars.len() && (chars[end].is_alphanumeric() || chars[end] == '_') {
                end += 1;
            }
            let word: String = chars[i..end].iter().collect();
            if KEYWORDS.contains(&word.as_str()) {
                flush_runs(&mut runs, &mut plain);
                runs.push((word, Style::CodeKeyword));
                i = end;
                continue;
            }
            plain.push_str(&word);
            i = end;
            continue;
        }
        plain.push(chars[i]);
        i += 1;
    }
    flush_runs(&mut runs, &mut plain);
    runs
}

fn c_like(chars: &[char], i: usize) -> bool {
    chars[i] == '/' && chars.get(i + 1) == Some(&'*')
}

fn flush_runs(runs: &mut Vec<Run>, plain: &mut String) {
    if !plain.is_empty() {
        runs.push((std::mem::take(plain), Style::CodeBlock));
    }
}

pub fn to_ratatui(runs: &[Run]) -> Line<'static> {
    Line::from(
        runs.iter()
            .map(|(text, style)| ratatui::text::Span::styled(text.clone(), style_of(*style)))
            .collect::<Vec<_>>(),
    )
}

pub fn style_of(style: Style) -> ratatui::style::Style {
    use ratatui::style::{Color, Modifier, Style as Rs};
    match style {
        Style::Normal => Rs::default(),
        Style::Bold => Rs::default().add_modifier(Modifier::BOLD),
        Style::Em => Rs::default().add_modifier(Modifier::ITALIC),
        Style::Code => Rs::default().fg(Color::Indexed(110)),
        Style::CodeBlock => Rs::default().fg(Color::Indexed(250)),
        Style::CodeKeyword => Rs::default().fg(Color::Indexed(174)),
        Style::CodeString => Rs::default().fg(Color::Indexed(151)),
        Style::CodeComment => Rs::default().fg(Color::Indexed(244)).add_modifier(Modifier::ITALIC),
        Style::CodeNumber => Rs::default().fg(Color::Indexed(180)),
        Style::Link => Rs::default().fg(Color::Indexed(110)).add_modifier(Modifier::UNDERLINED),
        Style::Head => Rs::default().fg(Color::Indexed(214)).add_modifier(Modifier::BOLD),
        Style::Quote => Rs::default().add_modifier(Modifier::DIM),
        Style::Dim => Rs::default().add_modifier(Modifier::DIM),
    }
}
