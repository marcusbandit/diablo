use diablo_phone::{parse_markdown, Align, Block, Span};

fn plain(text: &str) -> Span {
    Span { text: text.into(), ..Span::default() }
}

fn texts(spans: &[Span]) -> String {
    spans.iter().map(|s| s.text.as_str()).collect()
}

#[test]
fn heading_levels_and_inline_style() {
    let b = parse_markdown("# One\n\n### Three *x*".into());
    assert_eq!(b.len(), 2);
    assert_eq!(b[0], Block::Heading { level: 1, spans: vec![plain("One")], quote: 0 });
    match &b[1] {
        Block::Heading { level, spans, .. } => {
            assert_eq!(*level, 3);
            assert_eq!(spans.len(), 2);
            assert!(spans[1].italic);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn paragraph_styles_merge_and_split() {
    let b = parse_markdown("plain **bold *both*** `code` ~~gone~~ [link](http://x.y)".into());
    let Block::Paragraph { spans, quote } = &b[0] else { panic!("{b:?}") };
    assert_eq!(*quote, 0);
    assert_eq!(texts(spans), "plain bold both code gone link");
    let bold = spans.iter().find(|s| s.text == "bold ").unwrap();
    assert!(bold.bold && !bold.italic);
    let both = spans.iter().find(|s| s.text == "both").unwrap();
    assert!(both.bold && both.italic);
    assert!(spans.iter().any(|s| s.code && s.text == "code"));
    assert!(spans.iter().any(|s| s.strike && s.text == "gone"));
    let link = spans.iter().find(|s| s.link.is_some()).unwrap();
    assert_eq!(link.link.as_deref(), Some("http://x.y"));
    assert_eq!(link.text, "link");
}

#[test]
fn adjacent_text_events_merge_into_one_span() {
    let b = parse_markdown("a & b \\* c < d".into());
    let Block::Paragraph { spans, .. } = &b[0] else { panic!() };
    assert_eq!(spans.len(), 1);
    assert_eq!(spans[0].text, "a & b * c < d");
}

#[test]
fn soft_and_hard_breaks() {
    let b = parse_markdown("one\ntwo  \nthree".into());
    let Block::Paragraph { spans, .. } = &b[0] else { panic!() };
    assert_eq!(texts(spans), "one two\nthree");
}

#[test]
fn bullet_and_ordered_lists() {
    let b = parse_markdown("- a\n- b\n\n3. x\n4. y".into());
    let items: Vec<_> = b
        .iter()
        .map(|x| match x {
            Block::Item { ordered, number, depth, spans, .. } => (*ordered, *number, *depth, texts(spans)),
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(
        items,
        vec![
            (false, 0, 0, "a".to_string()),
            (false, 0, 0, "b".to_string()),
            (true, 3, 0, "x".to_string()),
            (true, 4, 0, "y".to_string()),
        ]
    );
}

#[test]
fn nested_lists_have_depth() {
    let b = parse_markdown("- a\n  - b\n    1. c\n  - d\n- e".into());
    let got: Vec<_> = b
        .iter()
        .map(|x| match x {
            Block::Item { ordered, depth, number, spans, .. } => (texts(spans), *depth, *ordered, *number),
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(
        got,
        vec![
            ("a".into(), 0, false, 0),
            ("b".into(), 1, false, 0),
            ("c".into(), 2, true, 1),
            ("d".into(), 1, false, 0),
            ("e".into(), 0, false, 0),
        ]
    );
}

#[test]
fn loose_list_items_with_extra_paragraph() {
    let b = parse_markdown("- a\n\n  more\n\n- b".into());
    assert!(matches!(&b[0], Block::Item { .. }));
    assert!(matches!(&b[1], Block::Paragraph { .. }));
    assert!(matches!(&b[2], Block::Item { .. }));
}

#[test]
fn task_lists() {
    let b = parse_markdown("- [x] done\n- [ ] todo\n- plain".into());
    let checked: Vec<_> = b
        .iter()
        .map(|x| match x {
            Block::Item { checked, .. } => *checked,
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(checked, vec![Some(true), Some(false), None]);
}

#[test]
fn blockquote_depth() {
    let b = parse_markdown("> a\n>\n> > b\n\nc".into());
    let q: Vec<_> = b
        .iter()
        .map(|x| match x {
            Block::Paragraph { quote, spans } => (texts(spans), *quote),
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(q, vec![("a".into(), 1), ("b".into(), 2), ("c".into(), 0)]);
}

#[test]
fn list_inside_quote() {
    let b = parse_markdown("> - a\n> - b".into());
    for x in b {
        match x {
            Block::Item { quote, .. } => assert_eq!(quote, 1),
            other => panic!("{other:?}"),
        }
    }
}

#[test]
fn fenced_and_indented_code() {
    let b = parse_markdown("```rust\nfn main() {}\n```\n\n    indented\n\n```\nnone\n```".into());
    assert_eq!(
        b[0],
        Block::Code { lang: Some("rust".into()), text: "fn main() {}".into(), quote: 0 }
    );
    assert_eq!(b[1], Block::Code { lang: None, text: "indented".into(), quote: 0 });
    assert_eq!(b[2], Block::Code { lang: None, text: "none".into(), quote: 0 });
}

#[test]
fn code_inside_quote_keeps_quote() {
    let b = parse_markdown("> ```py\n> x = 1\n> ```".into());
    assert_eq!(b[0], Block::Code { lang: Some("py".into()), text: "x = 1".into(), quote: 1 });
}

#[test]
fn rule() {
    let b = parse_markdown("a\n\n---\n\nb".into());
    assert_eq!(b.len(), 3);
    assert_eq!(b[1], Block::Rule);
}

#[test]
fn table_with_alignment() {
    let md = "| a | b | c | d |\n|:--|:-:|--:|---|\n| 1 | **2** | 3 | 4 |\n| x | y | z | w |";
    let b = parse_markdown(md.into());
    assert_eq!(b.len(), 1);
    let Block::Table { align, header, rows } = &b[0] else { panic!("{b:?}") };
    assert_eq!(align, &vec![Align::Left, Align::Center, Align::Right, Align::None]);
    assert_eq!(header.len(), 4);
    assert_eq!(texts(&header[0]), "a");
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].len(), 4);
    assert!(rows[0][1][0].bold);
    assert_eq!(texts(&rows[1][3]), "w");
}

#[test]
fn table_cell_with_image_uses_alt() {
    let b = parse_markdown("| a |\n|---|\n| ![pic](http://i/x.png) |".into());
    let Block::Table { rows, .. } = &b[0] else { panic!("{b:?}") };
    assert_eq!(texts(&rows[0][0]), "pic");
}

#[test]
fn inline_html_becomes_html_spans() {
    let b = parse_markdown("a <b>x</b><br>y<sub>2</sub>".into());
    let Block::Paragraph { spans, .. } = &b[0] else { panic!("{b:?}") };
    let html: Vec<_> = spans.iter().filter(|s| s.html).map(|s| s.text.as_str()).collect();
    assert_eq!(html, vec!["<b>", "</b>", "<br>", "<sub>", "</sub>"]);
    assert_eq!(texts(spans), "a <b>x</b><br>y<sub>2</sub>");
}

#[test]
fn html_block() {
    let b = parse_markdown("<div>\n<p>hi</p>\n</div>\n\nafter".into());
    assert_eq!(b[0], Block::Html { html: "<div>\n<p>hi</p>\n</div>".into() });
    assert!(matches!(&b[1], Block::Paragraph { .. }));
}

#[test]
fn svg_html_block_is_svg() {
    let b = parse_markdown("<svg width=\"10\" height=\"10\"><circle r=\"4\"/></svg>\n\nafter".into());
    assert_eq!(
        b[0],
        Block::Svg { svg: "<svg width=\"10\" height=\"10\"><circle r=\"4\"/></svg>".into(), animated: false }
    );
    assert!(matches!(&b[1], Block::Paragraph { .. }));
    assert_eq!(b.len(), 2);
}

#[test]
fn svg_uppercase_and_xml_prolog() {
    let md = "<?xml version=\"1.0\"?>\n<SVG viewBox=\"0 0 1 1\">\n<rect/>\n</SVG>";
    let b = parse_markdown(md.into());
    assert_eq!(b.len(), 1, "{b:?}");
    let Block::Svg { svg, animated } = &b[0] else { panic!("{b:?}") };
    assert!(svg.starts_with("<?xml"));
    assert!(svg.ends_with("</SVG>"));
    assert!(!animated);
}

#[test]
fn svg_with_blank_lines_and_multiline_open_tag() {
    let md = "<svg\n  width=\"5\"\n  height=\"5\">\n<g>\n\n<rect/>\n\n</g>\n</svg>\n\ntail";
    let b = parse_markdown(md.into());
    assert_eq!(b.len(), 2, "{b:?}");
    let Block::Svg { svg, .. } = &b[0] else { panic!("{b:?}") };
    assert!(svg.starts_with("<svg") && svg.ends_with("</svg>") && svg.contains("<rect/>"));
    assert!(matches!(&b[1], Block::Paragraph { .. }));
}

#[test]
fn nested_svg_ends_at_outer_close() {
    let md = "<svg><svg><g/></svg><rect/></svg>\n\nx";
    let b = parse_markdown(md.into());
    let Block::Svg { svg, .. } = &b[0] else { panic!("{b:?}") };
    assert_eq!(svg, "<svg><svg><g/></svg><rect/></svg>");
}

#[test]
fn unclosed_svg_does_not_panic_and_is_svg() {
    let b = parse_markdown("<svg width=\"1\">\n<rect/>".into());
    assert!(matches!(&b[0], Block::Svg { .. }), "{b:?}");
}

#[test]
fn svg_lookalikes_are_html() {
    let b = parse_markdown("<svgfoo>\nx\n</svgfoo>".into());
    assert!(matches!(&b[0], Block::Html { .. }), "{b:?}");
}

#[test]
fn svg_animation_detection() {
    let cases = [
        ("<svg><animate attributeName=\"x\"/></svg>", true),
        ("<svg><animateTransform type=\"rotate\"/></svg>", true),
        ("<svg><animateMotion/></svg>", true),
        ("<svg><set attributeName=\"x\" to=\"1\"/></svg>", true),
        ("<svg><style>@keyframes spin{}</style></svg>", true),
        ("<svg><rect style=\"animation: spin 1s\"/></svg>", true),
        ("<svg><rect/></svg>", false),
        ("<svg><settings/></svg>", false),
    ];
    for (svg, expected) in cases {
        let b = parse_markdown(svg.into());
        match &b[0] {
            Block::Svg { animated, .. } => assert_eq!(*animated, expected, "{svg}"),
            other => panic!("{svg}: {other:?}"),
        }
    }
}

#[test]
fn fenced_svg_and_html_blocks() {
    let b = parse_markdown("```svg\n<svg><animate/></svg>\n```\n\n```HTML\n<p>x</p>\n```".into());
    assert_eq!(b[0], Block::Svg { svg: "<svg><animate/></svg>".into(), animated: true });
    assert_eq!(b[1], Block::Html { html: "<p>x</p>".into() });
}

#[test]
fn image_only_paragraph_is_just_the_image() {
    let b = parse_markdown("![a cat](http://x/c.gif)".into());
    assert_eq!(b, vec![Block::Image { url: "http://x/c.gif".into(), alt: "a cat".into() }]);
}

#[test]
fn image_inside_paragraph_splits_it() {
    let b = parse_markdown("before ![alt *x*](u.png) after".into());
    assert_eq!(b.len(), 3, "{b:?}");
    assert_eq!(texts(match &b[0] { Block::Paragraph { spans, .. } => spans, o => panic!("{o:?}") }), "before ");
    assert_eq!(b[1], Block::Image { url: "u.png".into(), alt: "alt x".into() });
    assert_eq!(texts(match &b[2] { Block::Paragraph { spans, .. } => spans, o => panic!("{o:?}") }), " after");
}

#[test]
fn two_images_with_space_drop_the_blank_paragraph() {
    let b = parse_markdown("![a](1.png) ![b](2.png)".into());
    assert_eq!(b.len(), 2, "{b:?}");
    assert!(b.iter().all(|x| matches!(x, Block::Image { .. })));
}

#[test]
fn image_in_list_item_and_quote() {
    let b = parse_markdown("- item ![i](p.png) tail".into());
    assert!(matches!(&b[0], Block::Item { .. }));
    assert!(matches!(&b[1], Block::Image { .. }));
    assert!(matches!(&b[2], Block::Paragraph { .. }));
    let q = parse_markdown("> ![i](p.png)".into());
    assert_eq!(q, vec![Block::Image { url: "p.png".into(), alt: "i".into() }]);
}

#[test]
fn empty_and_whitespace_input() {
    assert!(parse_markdown(String::new()).is_empty());
    assert!(parse_markdown("   \n\n\t\n".into()).is_empty());
}

#[test]
fn nasty_inputs_do_not_panic() {
    let long_quote = ">".repeat(5000);
    let long_list = "- ".repeat(3000) + "x";
    let long_emph = "*".repeat(10_000);
    let deep_brackets = "[".repeat(5000) + &"]".repeat(5000);
    let weird = [
        "\0\0\0",
        "\u{feff}# bom",
        "<svg",
        "<svg>",
        "</svg>",
        "<?xml",
        "<?xml version=\"1.0\"?>",
        "<!-- <svg> -->",
        "<!DOCTYPE svg",
        "<svg\u{00e9}>",
        "| a |\n|---|\n| b | c | d |",
        "|\n|-|\n|",
        "![](",
        "![a]()",
        "![a](<>)",
        "[x]: http://a\n\n[x]",
        "```\nunclosed",
        "````\n```\n````",
        "~~~svg\n<svg>",
        "- [x]\n- [",
        "1. a\n   > b\n   > - c\n     ![i](x)",
        "\u{202e}rtl \u{1f600} emoji \u{200d}",
        "<b><i></b></i>",
        "<script>alert(1)</script>",
        "# \n#\n## ##",
        "***\n---\n___",
        "999999999999999999999999. a",
        "18446744073709551615. a\n18446744073709551615. b",
        "- a\n\n\n\n- b\n\n\n  > c",
        "<svg><svg><svg>",
        "<SVG\n",
        "é<svg>é</svg>é",
    ];
    for w in weird {
        let _ = parse_markdown(w.to_string());
    }
    for big in [long_quote, long_list, long_emph, deep_brackets] {
        let _ = parse_markdown(big);
    }
}

#[test]
fn ordered_number_overflow_is_safe() {
    let b = parse_markdown("18446744073709551615. a\n\n18446744073709551615. b".into());
    assert!(!b.is_empty());
}

#[test]
fn pseudo_random_bytes_do_not_panic() {
    let alphabet: Vec<char> = "<>/!?-*_`~[]()#>|:\n \t\"'&;=xsvgab0123.+\u{e9}\u{1f600}".chars().collect();
    let mut seed: u64 = 0x9e3779b97f4a7c15;
    for _ in 0..400 {
        let mut s = String::new();
        for _ in 0..120 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            s.push(alphabet[(seed % alphabet.len() as u64) as usize]);
        }
        let _ = parse_markdown(s);
    }
}
