package com.marcusrosado.diablo.render

internal object GalleryContent {
    val markdown = """
# Heading one
## Heading two
### Heading three
#### Heading four
##### Heading five

A paragraph with **bold**, *italic*, ***both***, `inline code`, ~~strikethrough~~ and a [link to the docs](https://developer.android.com/jetpack/compose). Libron carries the reading, Source Sans the titles.

- First bullet
- Second bullet with **bold**
  - Nested bullet
    - Deeper bullet
1. First step
2. Second step
3. Third step

- [x] Parser wired up
- [ ] Gallery reviewed

> A quote with *style*.
>> A quote inside the quote.

---

```kotlin
fun main() {
    println("Diablo")
}
```

![orbiting dots](${SAMPLE_GIF})
""".trimIndent()

    val table = """
| Host | Latency | Status |
|:-----|--------:|:------:|
| diablo-pi | 12 ms | **up** |
| banditbox | 3 ms | up |
| nemurenai | 48 ms | `sleeping` |
| a very long host name that should wrap inside the column width limit of the table | 120 ms | down |
""".trimIndent()

    val code = """
```rust
use std::collections::HashMap;

fn tally(words: &[&str]) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for w in words { *counts.entry(w.to_string()).or_insert(0) += 1; }
    counts
}
```
""".trimIndent()

    val inlineHtml = listOf<Block>(
        Block.Paragraph(
            listOf(
                Span("Water is H"),
                Span("<sub>", html = true), Span("2"), Span("</sub>", html = true),
                Span("O and this is "),
                Span("<b>", html = true), Span("html bold"), Span("</b>", html = true),
                Span(" next to "),
                Span("<i>", html = true), Span("html italic"), Span("</i>", html = true),
                Span(", then a break"),
                Span("<br>", html = true),
                Span("on a new line with "),
                Span("markdown bold", bold = true),
                Span(", "),
                Span("code", code = true),
                Span(" and a "),
                Span("link", link = "https://example.com/inline"),
                Span("."),
            ),
        ),
    )

    val htmlCard = """
<div style="border:1px solid rgba(242,241,238,.18);background:#1a1c20;padding:12px 14px">
  <div style="font:700 11px monospace;letter-spacing:.08em;color:#74aaff">STATUS CARD</div>
  <h3 style="margin:.3em 0 .2em;color:#f2f1ee">Deploy finished</h3>
  <p style="margin:0;color:rgba(242,241,238,.66)">Hub restarted on <b>diablo-pi</b> in 4.2 s. <a href="https://example.com/runs/42">Open the run</a>.</p>
  <table style="margin-top:10px"><tr><td>checks</td><td style="color:#3fd6b8">passed</td></tr><tr><td>warnings</td><td style="color:#d29922">2</td></tr></table>
</div>
""".trimIndent()

    val flame = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 140" width="120" height="140">
  <defs>
    <linearGradient id="g" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#d29922"/>
      <stop offset="1" stop-color="#ff5a36"/>
    </linearGradient>
  </defs>
  <path d="M60 8 C66 40 100 52 100 92 C100 120 82 134 60 134 C38 134 20 120 20 92 C20 70 34 62 40 44 C46 56 50 58 54 56 C58 40 56 24 60 8 Z" fill="url(#g)"/>
  <path d="M60 62 C64 80 82 88 82 106 C82 120 72 128 60 128 C48 128 38 120 38 106 C38 92 54 80 60 62 Z" fill="#ffd166"/>
</svg>
""".trimIndent()

    val smilRing = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <circle cx="100" cy="50" r="10" fill="#2d81ff"/>
  <circle cx="100" cy="50" r="10" fill="none" stroke="#74aaff" stroke-width="3">
    <animate attributeName="r" values="10;44" dur="1.6s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="1;0" dur="1.6s" repeatCount="indefinite"/>
  </circle>
</svg>
""".trimIndent()

    val cssSpinner = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <style>
    @keyframes spin { to { transform: rotate(360deg); } }
    .arm { transform-origin: 100px 50px; animation: spin 2s linear infinite; }
  </style>
  <circle cx="100" cy="50" r="36" fill="none" stroke="rgba(242,241,238,.18)" stroke-width="2"/>
  <g class="arm">
    <line x1="100" y1="50" x2="100" y2="14" stroke="#3fd6b8" stroke-width="3"/>
    <circle cx="100" cy="14" r="5" fill="#3fd6b8"/>
  </g>
  <circle cx="100" cy="50" r="4" fill="#f2f1ee"/>
</svg>
""".trimIndent()

    const val remoteGif =
        "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Rotating_earth_%28large%29.gif/120px-Rotating_earth_%28large%29.gif"

    val longHtml: String = buildString {
        append("<h3>A long document</h3>")
        for (i in 1..30) append("<p>Line $i of generated text that is long enough to need a second row on a narrow screen.</p>")
    }

    val objects = listOf(
        """{"kind":"readout","title":"Today","cells":[{"v":"72","k":"bpm"},{"v":"8.4k","k":"steps"},{"v":"6h 40m","k":"sleep"},{"v":"21.5","k":"deg C"},{"v":"3","k":"meetings"}]}""",
        """{"kind":"code","title":"hub.toml","code":"[hub]\nport = 8755\nhosts = [\"diablo-pi\", \"banditbox\"]\nlog = \"info\""}""",
        """{"kind":"table","title":"Sets","head":["Lift","Sets","Reps","Weight"],"rows":[["Squat","5","5","100 kg"],["Bench","3","8","70 kg"],["Row","4","8","65 kg"]]}""",
        """{"kind":"lights","lamps":[{"name":"Desk","on":true,"bri":254},{"name":"Ceiling","on":true,"bri":120},{"name":"Bed","on":false,"bri":0},{"name":"Window","on":true,"bri":40}]}""",
        """{"kind":"git","title":"diablo-monorepo","commits":[{"kind":"feat","text":"Rich content renderer","rtime":"2 min ago","rstate":"ok"},{"kind":"fix","text":"Hub reconnect backoff","rtime":"1 h ago","rstate":"running"},{"kind":"docs","text":"Phone architecture","rtime":"yesterday","rstate":"merged"}]}""",
        """{"kind":"artifact","title":"Workout plan","version":"3","format":"markdown","size":"2.1 kB","updated":"today 07:12"}""",
        """{"kind":"weather","city":"Copenhagen","temp":11,"wind":{"ms":6,"dir":"SW"}}""",
    )
}
