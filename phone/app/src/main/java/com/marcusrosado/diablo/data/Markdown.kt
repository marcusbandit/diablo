package com.marcusrosado.diablo.data

import android.util.LruCache
import com.marcusrosado.diablo.core.parseMarkdown
import com.marcusrosado.diablo.render.Align
import com.marcusrosado.diablo.render.Block
import com.marcusrosado.diablo.render.MarkdownLite
import com.marcusrosado.diablo.render.Span
import com.marcusrosado.diablo.core.Align as CoreAlign
import com.marcusrosado.diablo.core.Block as CoreBlock
import com.marcusrosado.diablo.core.Span as CoreSpan

/**
 * Markdown to blocks through the Rust parser, cached by text. If the native library cannot
 * load, the small Kotlin parser keeps the app readable.
 */
object Markdown {
    private val cache = LruCache<String, List<Block>>(512)

    @Volatile
    var native = true
        private set

    fun parse(text: String): List<Block> {
        cache.get(text)?.let { return it }
        val blocks = if (native) {
            try {
                parseMarkdown(text).map(::block)
            } catch (error: Throwable) {
                native = false
                MarkdownLite.parse(text)
            }
        } else {
            MarkdownLite.parse(text)
        }
        cache.put(text, blocks)
        return blocks
    }

    private fun span(s: CoreSpan) = Span(s.text, s.bold, s.italic, s.code, s.strike, s.link, s.html)

    private fun spans(list: List<CoreSpan>) = list.map(::span)

    private fun align(a: CoreAlign) = when (a) {
        CoreAlign.NONE -> Align.None
        CoreAlign.LEFT -> Align.Left
        CoreAlign.CENTER -> Align.Center
        CoreAlign.RIGHT -> Align.Right
    }

    private fun block(b: CoreBlock): Block = when (b) {
        is CoreBlock.Heading -> Block.Heading(b.level.toInt(), spans(b.spans), b.quote.toInt())
        is CoreBlock.Paragraph -> Block.Paragraph(spans(b.spans), b.quote.toInt())
        is CoreBlock.Item -> Block.Item(b.ordered, b.number.toLong(), b.depth.toInt(), b.checked, spans(b.spans), b.quote.toInt())
        is CoreBlock.Code -> Block.Code(b.lang, b.text, b.quote.toInt())
        is CoreBlock.Table -> Block.Table(
            b.align.map(::align),
            b.header.map(::spans),
            b.rows.map { row -> row.map(::spans) },
        )
        is CoreBlock.Html -> Block.Html(b.html)
        is CoreBlock.Svg -> Block.Svg(b.svg, b.animated)
        is CoreBlock.Image -> Block.Image(b.url, b.alt)
        CoreBlock.Rule -> Block.Rule
    }
}
