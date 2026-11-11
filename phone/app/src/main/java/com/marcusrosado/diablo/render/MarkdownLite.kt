package com.marcusrosado.diablo.render

/**
 * A small Markdown to Block converter for the gallery and as a fallback when the native
 * parser cannot load. The real parser lives in the Rust core.
 */
object MarkdownLite {
    private val fence = Regex("^\\s*(`{3,}|~{3,})\\s*([\\w+-]*)\\s*$")
    private val heading = Regex("^(#{1,6})\\s+(.*?)\\s*#*\\s*$")
    private val rule = Regex("^\\s{0,3}([-*_])(\\s*\\1){2,}\\s*$")
    private val listItem = Regex("^(\\s*)([-*+]|\\d+[.)])\\s+(.*)$")
    private val task = Regex("^\\[([ xX])]\\s+(.*)$")
    private val image = Regex("^!\\[([^\\]]*)]\\(([^)\\s]+)[^)]*\\)\\s*$")
    private val tableSep = Regex("^\\s*\\|?\\s*:?-{1,}:?\\s*(\\|\\s*:?-{1,}:?\\s*)*\\|?\\s*$")
    private val htmlBlockStart = Regex("^<(div|table|details|section|figure|center|pre|p|ul|ol|blockquote)\\b", RegexOption.IGNORE_CASE)
    private val htmlTag = Regex("^</?([a-zA-Z][a-zA-Z0-9]*)(\\s[^>]*)?/?>")
    private val link = Regex("^\\[([^\\]]+)]\\(([^)\\s]+)\\)")
    private val inlineTags = setOf(
        "b", "i", "u", "s", "em", "strong", "sub", "sup", "br", "tt", "small", "big", "mark", "kbd", "span", "del", "ins",
    )

    fun parse(source: String): List<Block> {
        val lines = source.replace("\r\n", "\n").split('\n')
        val out = ArrayList<Block>()
        var i = 0
        var para = StringBuilder()
        var paraQuote = 0

        fun flushPara() {
            if (para.isNotEmpty()) {
                out += Block.Paragraph(inline(para.toString().trim()), paraQuote)
                para = StringBuilder()
            }
        }

        while (i < lines.size) {
            val (quote, line) = stripQuote(lines[i])

            val open = fence.matchEntire(line)
            if (open != null) {
                flushPara()
                val marker = open.groupValues[1]
                val lang = open.groupValues[2].ifEmpty { null }
                val body = ArrayList<String>()
                i++
                while (i < lines.size) {
                    val (_, inner) = stripQuote(lines[i])
                    if (inner.trim().startsWith(marker) && inner.trim().all { it == marker[0] }) break
                    body += inner
                    i++
                }
                i++
                val text = body.joinToString("\n")
                out += when (lang?.lowercase()) {
                    "svg" -> Block.Svg(text, isAnimatedSvg(text))
                    "html" -> Block.Html(text)
                    else -> Block.Code(lang, text, quote)
                }
                continue
            }

            if (line.isBlank()) {
                flushPara()
                i++
                continue
            }

            val h = heading.matchEntire(line)
            if (h != null) {
                flushPara()
                out += Block.Heading(h.groupValues[1].length, inline(h.groupValues[2]), quote)
                i++
                continue
            }

            if (rule.matches(line)) {
                flushPara()
                out += Block.Rule
                i++
                continue
            }

            val img = image.matchEntire(line.trim())
            if (img != null) {
                flushPara()
                out += Block.Image(img.groupValues[2], img.groupValues[1])
                i++
                continue
            }

            if (line.trimStart().startsWith("|") && i + 1 < lines.size && tableSep.matches(stripQuote(lines[i + 1]).second)
                && stripQuote(lines[i + 1]).second.contains('-')
            ) {
                flushPara()
                val sepCells = cells(stripQuote(lines[i + 1]).second)
                val align = sepCells.map {
                    val t = it.trim()
                    when {
                        t.startsWith(":") && t.endsWith(":") -> Align.Center
                        t.endsWith(":") -> Align.Right
                        t.startsWith(":") -> Align.Left
                        else -> Align.None
                    }
                }
                val header = cells(line).map { inline(it.trim()) }
                val rows = ArrayList<List<List<Span>>>()
                i += 2
                while (i < lines.size && stripQuote(lines[i]).second.trimStart().startsWith("|")) {
                    rows += cells(stripQuote(lines[i]).second).map { inline(it.trim()) }
                    i++
                }
                out += Block.Table(align, header, rows)
                continue
            }

            val item = listItem.matchEntire(line)
            if (item != null) {
                flushPara()
                val indent = item.groupValues[1].replace("\t", "    ").length
                val marker = item.groupValues[2]
                var text = item.groupValues[3]
                var checked: Boolean? = null
                task.matchEntire(text)?.let {
                    checked = it.groupValues[1] != " "
                    text = it.groupValues[2]
                }
                val ordered = marker.first().isDigit()
                out += Block.Item(
                    ordered = ordered,
                    number = if (ordered) marker.dropLast(1).toLong() else 0,
                    depth = indent / 2,
                    checked = checked,
                    spans = inline(text),
                    quote = quote,
                )
                i++
                continue
            }

            if (para.isEmpty() && htmlBlockStart.containsMatchIn(line.trimStart())) {
                val body = ArrayList<String>()
                while (i < lines.size && lines[i].isNotBlank()) {
                    body += lines[i]
                    i++
                }
                out += Block.Html(body.joinToString("\n"))
                continue
            }

            val last = out.lastOrNull()
            if (para.isEmpty() && last is Block.Item && line.startsWith(" ")) {
                out[out.lastIndex] = last.copy(spans = last.spans + Span(" ") + inline(line.trim()))
            } else {
                if (para.isEmpty()) paraQuote = quote else if (quote != paraQuote) {
                    flushPara()
                    paraQuote = quote
                }
                if (para.isNotEmpty()) para.append(' ')
                para.append(line.trim())
            }
            i++
        }
        flushPara()
        return out
    }

    fun isAnimatedSvg(svg: String): Boolean {
        val s = svg.lowercase()
        return "<animate" in s || "<set " in s || "@keyframes" in s || "animation:" in s
    }

    private fun stripQuote(raw: String): Pair<Int, String> {
        var depth = 0
        var s = raw
        while (true) {
            val t = s.trimStart()
            if (!t.startsWith(">") || s.length - t.length > 3) break
            depth++
            s = t.drop(1).removePrefix(" ")
        }
        return depth to s
    }

    private fun cells(row: String): List<String> {
        var t = row.trim()
        if (t.startsWith("|")) t = t.drop(1)
        if (t.endsWith("|") && !t.endsWith("\\|")) t = t.dropLast(1)
        val out = ArrayList<String>()
        val cur = StringBuilder()
        var k = 0
        while (k < t.length) {
            val c = t[k]
            if (c == '\\' && k + 1 < t.length && t[k + 1] == '|') {
                cur.append('|')
                k += 2
                continue
            }
            if (c == '|') {
                out += cur.toString()
                cur.clear()
            } else cur.append(c)
            k++
        }
        out += cur.toString()
        return out
    }

    private data class Style(
        val bold: Boolean = false,
        val italic: Boolean = false,
        val strike: Boolean = false,
        val link: String? = null,
    )

    fun inline(text: String): List<Span> {
        val out = ArrayList<Span>()
        parseInline(text, Style(), out)
        return merge(out)
    }

    private fun merge(spans: List<Span>): List<Span> {
        val out = ArrayList<Span>()
        for (s in spans) {
            val last = out.lastOrNull()
            if (last != null && !last.html && !s.html && last.copy(text = "") == s.copy(text = "")) {
                out[out.lastIndex] = last.copy(text = last.text + s.text)
            } else out += s
        }
        return out
    }

    private fun parseInline(text: String, style: Style, out: MutableList<Span>) {
        val buf = StringBuilder()
        fun flush() {
            if (buf.isNotEmpty()) {
                out += Span(buf.toString(), style.bold, style.italic, false, style.strike, style.link)
                buf.clear()
            }
        }
        var i = 0
        while (i < text.length) {
            val c = text[i]
            when {
                c == '\\' && i + 1 < text.length && text[i + 1] in "\\`*_{}[]()#+-.!|<>~" -> {
                    buf.append(text[i + 1])
                    i += 2
                }
                c == '`' -> {
                    val end = text.indexOf('`', i + 1)
                    if (end > i) {
                        flush()
                        out += Span(text.substring(i + 1, end), style.bold, style.italic, true, style.strike, style.link)
                        i = end + 1
                    } else {
                        buf.append(c); i++
                    }
                }
                text.startsWith("**", i) || text.startsWith("__", i) -> {
                    val marker = text.substring(i, i + 2)
                    var end = text.indexOf(marker, i + 2)
                    while (end > 0 && end + 2 < text.length && text[end + 2] == marker[0]) end++
                    if (end > i + 2) {
                        flush()
                        parseInline(text.substring(i + 2, end), style.copy(bold = true), out)
                        i = end + 2
                    } else {
                        buf.append(marker); i += 2
                    }
                }
                text.startsWith("~~", i) -> {
                    val end = text.indexOf("~~", i + 2)
                    if (end > i + 2) {
                        flush()
                        parseInline(text.substring(i + 2, end), style.copy(strike = true), out)
                        i = end + 2
                    } else {
                        buf.append("~~"); i += 2
                    }
                }
                (c == '*' || (c == '_' && (i == 0 || !text[i - 1].isLetterOrDigit()))) -> {
                    val end = findClose(text, c, i + 1)
                    if (end > i + 1) {
                        flush()
                        parseInline(text.substring(i + 1, end), style.copy(italic = true), out)
                        i = end + 1
                    } else {
                        buf.append(c); i++
                    }
                }
                c == '[' -> {
                    val m = link.find(text.substring(i))
                    if (m != null) {
                        flush()
                        parseInline(m.groupValues[1], style.copy(link = m.groupValues[2]), out)
                        i += m.value.length
                    } else {
                        buf.append(c); i++
                    }
                }
                c == '<' -> {
                    val m = htmlTag.find(text.substring(i))
                    if (m != null && m.groupValues[1].lowercase() in inlineTags) {
                        flush()
                        out += Span(m.value, html = true)
                        i += m.value.length
                    } else {
                        buf.append(c); i++
                    }
                }
                else -> {
                    buf.append(c); i++
                }
            }
        }
        flush()
    }

    private fun findClose(text: String, marker: Char, from: Int): Int {
        var k = from
        while (k < text.length) {
            if (text[k] == '\\') { k += 2; continue }
            if (text[k] == marker && (marker == '*' || k + 1 >= text.length || !text[k + 1].isLetterOrDigit())) {
                if (!(marker == '*' && text.startsWith("**", k))) return k
                k += 2
                continue
            }
            k++
        }
        return -1
    }
}
