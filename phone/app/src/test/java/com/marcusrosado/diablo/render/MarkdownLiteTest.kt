package com.marcusrosado.diablo.render

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownLiteTest {
    @Test
    fun headingAndParagraph() {
        val blocks = MarkdownLite.parse("## Title\n\nOne line\nsecond line")
        assertEquals(Block.Heading(2, listOf(Span("Title"))), blocks[0])
        assertEquals(Block.Paragraph(listOf(Span("One line second line"))), blocks[1])
    }

    @Test
    fun inlineStyles() {
        val spans = MarkdownLite.inline("a **bold *both*** `code` ~~gone~~ [site](https://x.dev)")
        assertEquals(Span("a "), spans[0])
        assertEquals(Span("bold ", bold = true), spans[1])
        assertEquals(Span("both", bold = true, italic = true), spans[2])
        assertEquals(Span("code", code = true), spans[4])
        assertEquals(Span("gone", strike = true), spans[6])
        assertEquals(Span("site", link = "https://x.dev"), spans[8])
    }

    @Test
    fun inlineHtmlBecomesHtmlSpans() {
        val spans = MarkdownLite.inline("H<sub>2</sub>O<br>next")
        assertTrue(spans.any { it.html && it.text == "<sub>" })
        assertTrue(spans.any { it.html && it.text == "<br>" })
        assertFalse(MarkdownLite.inline("a < b and <notatag>").any { it.html })
    }

    @Test
    fun listsAndTasks() {
        val blocks = MarkdownLite.parse("- one\n  - nested\n1. first\n2. second\n- [x] done\n- [ ] todo")
        assertEquals(Block.Item(false, 0, 0, null, listOf(Span("one"))), blocks[0])
        assertEquals(1, (blocks[1] as Block.Item).depth)
        assertEquals(Block.Item(true, 1, 0, null, listOf(Span("first"))), blocks[2])
        assertEquals(2L, (blocks[3] as Block.Item).number)
        assertEquals(true, (blocks[4] as Block.Item).checked)
        assertEquals(false, (blocks[5] as Block.Item).checked)
    }

    @Test
    fun nestedQuotes() {
        val blocks = MarkdownLite.parse("> outer\n>> inner\n\nplain")
        assertEquals(1, (blocks[0] as Block.Paragraph).quote)
        assertEquals(2, (blocks[1] as Block.Paragraph).quote)
        assertEquals(0, (blocks[2] as Block.Paragraph).quote)
    }

    @Test
    fun fences() {
        val blocks = MarkdownLite.parse(
            "```kotlin\nval x = 1\n```\n```svg\n<svg><animate/></svg>\n```\n```svg\n<svg/>\n```\n```html\n<b>hi</b>\n```",
        )
        assertEquals(Block.Code("kotlin", "val x = 1"), blocks[0])
        assertEquals(Block.Svg("<svg><animate/></svg>", true), blocks[1])
        assertEquals(Block.Svg("<svg/>", false), blocks[2])
        assertEquals(Block.Html("<b>hi</b>"), blocks[3])
    }

    @Test
    fun ruleImageAndHtmlBlock() {
        val blocks = MarkdownLite.parse("---\n\n![logo](data:image/gif;base64,AAA)\n\n<div class=\"a\">\nhello\n</div>")
        assertEquals(Block.Rule, blocks[0])
        assertEquals(Block.Image("data:image/gif;base64,AAA", "logo"), blocks[1])
        assertEquals(Block.Html("<div class=\"a\">\nhello\n</div>"), blocks[2])
    }

    @Test
    fun tableWithAlignment() {
        val blocks = MarkdownLite.parse("| a | b | c |\n|:--|:-:|--:|\n| 1 | **2** | 3 |\n| x | y | z |")
        val table = blocks.single() as Block.Table
        assertEquals(listOf(Align.Left, Align.Center, Align.Right), table.align)
        assertEquals(3, table.header.size)
        assertEquals(2, table.rows.size)
        assertEquals(listOf(Span("2", bold = true)), table.rows[0][1])
    }
}
