package com.marcusrosado.diablo.notify

import com.marcusrosado.diablo.render.Block
import com.marcusrosado.diablo.render.Span

/**
 * A reply as a notification can show it: readable plain text, one line per block. Tables,
 * code and pictures do not fit in a notification, so each becomes a short summary.
 */
object Plain {
    private val tag = Regex("<[^>]+>")
    private val br = Regex("<br\\s*/?>", RegexOption.IGNORE_CASE)
    private val space = Regex("[ \\t]+")

    fun of(blocks: List<Block>): String = blocks.mapNotNull(::line).joinToString("\n").trim()

    private fun line(block: Block): String? = when (block) {
        is Block.Heading -> text(block.spans)
        is Block.Paragraph -> text(block.spans)
        is Block.Item -> {
            val mark = when {
                block.checked == true -> "[x]"
                block.checked == false -> "[ ]"
                block.ordered -> "${block.number}."
                else -> "•"
            }
            "  ".repeat(block.depth) + "$mark ${text(block.spans)}"
        }
        is Block.Code -> {
            val lines = block.text.trimEnd().lines().size
            val lang = block.lang?.takeIf { it.isNotBlank() }?.let { "$it " }.orEmpty()
            "[${lang}code, $lines ${if (lines == 1) "line" else "lines"}]"
        }
        is Block.Table -> {
            val title = block.header.joinToString(", ") { text(it) }.takeIf { it.isNotBlank() }
            val rows = block.rows.size
            "[table, $rows ${if (rows == 1) "row" else "rows"}${title?.let { ": $it" }.orEmpty()}]"
        }
        is Block.Html -> squash(block.html.replace(tag, " ")).ifBlank { null }
        is Block.Svg -> "[drawing]"
        is Block.Image -> if (block.alt.isBlank()) "[picture]" else "[picture: ${block.alt}]"
        Block.Rule -> null
    }

    private fun text(spans: List<Span>): String =
        squash(spans.joinToString("") { if (it.html) it.text.replace(br, " ").replace(tag, "") else it.text })

    private fun squash(s: String) = s.replace(space, " ").trim()
}
