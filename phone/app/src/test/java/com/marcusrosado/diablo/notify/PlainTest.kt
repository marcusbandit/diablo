package com.marcusrosado.diablo.notify

import com.marcusrosado.diablo.render.MarkdownLite
import org.junit.Assert.assertEquals
import org.junit.Test

class PlainTest {
    private fun plain(md: String) = Plain.of(MarkdownLite.parse(md))

    @Test
    fun inlineMarkupIsDropped() {
        assertEquals("Done. The build is green, see PR.", plain("**Done.** The build is *green*, see [PR](https://x.dev)."))
    }

    @Test
    fun headingsAndListsStayReadable() {
        assertEquals(
            "Report\n• one\n  • nested\n1. first\n[x] done",
            plain("## Report\n\n- one\n  - nested\n\n1. first\n\n- [x] done"),
        )
    }

    @Test
    fun codeBecomesASummary() {
        assertEquals("Here:\n[kotlin code, 3 lines]", plain("Here:\n\n```kotlin\nval a = 1\nval b = 2\nprintln(a + b)\n```"))
        assertEquals("[code, 1 line]", plain("```\nls\n```"))
    }

    @Test
    fun tableBecomesASummary() {
        assertEquals(
            "[table, 2 rows: Set, Reps]",
            plain("| Set | Reps |\n|---|---|\n| 1 | 10 |\n| 2 | 8 |"),
        )
    }

    @Test
    fun inlineHtmlKeepsItsText() {
        assertEquals("H2O next", plain("H<sub>2</sub>O<br>next"))
    }

    @Test
    fun picturesAndRules() {
        assertEquals("[picture: graph]", plain("![graph](https://x.dev/a.png)\n\n---"))
    }
}
