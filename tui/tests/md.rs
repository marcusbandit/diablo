use diablo_tui::md::{inline_runs, render, wrap_runs, Run, Style};

#[test]
fn inline_styles_match_the_python_rules() {
    let runs = inline_runs("plain **bold** `code` [text](http://x) ~~gone~~ *em*", Style::Normal);
    let styles: Vec<Style> = runs.iter().map(|(_, style)| *style).collect();
    assert_eq!(
        styles,
        vec![
            Style::Normal,
            Style::Bold,
            Style::Normal,
            Style::Code,
            Style::Normal,
            Style::Link,
            Style::Dim,
            Style::Normal,
            Style::Dim,
            Style::Normal,
            Style::Em,
        ]
    );
}

#[test]
fn snake_case_survives_the_inline_parser() {
    let runs = inline_runs("run pytest on test_pool.py twice", Style::Normal);
    assert_eq!(runs, vec![("run pytest on test_pool.py twice".to_string(), Style::Normal)]);
}

#[test]
fn nested_code_inside_bold_leaks_no_backticks() {
    let runs = inline_runs("**run `npm test` twice**", Style::Normal);
    let joined: String = runs.iter().map(|(text, _)| text.clone()).collect();
    assert!(!joined.contains('`'));
    assert_eq!(runs[1], ("npm test".to_string(), Style::Code));
    assert_eq!(runs[0], ("run ".to_string(), Style::Bold));
}

#[test]
fn headings_bullets_and_rules() {
    let lines = render("# Plan\n\n- one\n- two\n\n---\n", 80);
    let flat: Vec<String> = lines
        .iter()
        .map(|segments| segments.iter().map(|(text, _)| text.clone()).collect())
        .collect();
    assert!(flat[0].contains("Plan"));
    assert!(flat.iter().any(|line| line.contains("• one")));
    assert!(flat.iter().any(|line| line.contains("• two")));
    assert!(flat.iter().any(|line| line.starts_with('─')));
}

#[test]
fn code_block_is_boxed() {
    let lines = render("```python\ndef add(a, b):\n    return a + b\n```\nafter", 60);
    let flat: Vec<String> = lines
        .iter()
        .map(|segments| segments.iter().map(|(text, _)| text.clone()).collect())
        .collect();
    assert!(flat.iter().any(|line| line.starts_with("┌") && line.ends_with("┐")));
    assert!(flat.iter().any(|line| line.starts_with("│ ") && line.contains("def add") && line.ends_with(" │")));
    assert!(flat.iter().any(|line| line.starts_with("└") && line.ends_with("┘")));
    assert_eq!(flat.last().unwrap(), "after");
}

#[test]
fn code_block_hugs_its_content() {
    let lines = render("```python\nshort = 1\n```\n", 60);
    let border: String = lines[0].iter().map(|(text, _)| text.clone()).collect();
    assert_eq!(border.chars().count(), "short = 1".len() + 4, "{border}");
    assert!(border.chars().count() < 60);
}

#[test]
fn wrap_runs_hangs_the_continuation() {
    let label: Run = ("  you  ".to_string(), Style::Normal);
    let body: Run = ("word ".repeat(30).trim_end().to_string(), Style::Normal);
    let lines = wrap_runs(&[label, body], 40, "       ");
    let first: String = lines[0].iter().map(|(text, _)| text.clone()).collect();
    assert!(first.starts_with("  you  "), "{first}");
    for line in lines.iter().skip(1) {
        assert_eq!(line[0].0, "       ");
    }
}

#[test]
fn table_aligns_columns() {
    let lines = render("| name | count |\n|---|---|\n| ledger | 3 |\n| map | 12 |", 80);
    let flat: Vec<String> = lines
        .iter()
        .map(|segments| segments.iter().map(|(text, _)| text.clone()).collect())
        .collect();
    assert!(flat[0].contains("│"), "first row: {}", flat[0]);
    assert!(flat[1].contains("ledger"));
    assert_eq!(flat.len(), 3);
}
