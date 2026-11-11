package com.marcusrosado.diablo.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairCodeTest {
    @Test
    fun typedLettersInAnyCase() {
        assertEquals(PairCode("ABCDEFGH", null), PairCode.parse(" abcd-efgh "))
    }

    @Test
    fun appLinkCarriesTheHost() {
        val raw = "diablo://pair?host=http%3A%2F%2F100.64.0.1%3A8755&code=ABCDEFGH"
        assertEquals(PairCode("ABCDEFGH", "http://100.64.0.1:8755"), PairCode.parse(raw))
    }

    @Test
    fun hubPageUrlUsesItsOrigin() {
        val raw = "http://100.64.0.1:8755/app/pair.html?code=ABCDEFGH"
        assertEquals(PairCode("ABCDEFGH", "http://100.64.0.1:8755"), PairCode.parse(raw))
    }

    @Test
    fun rejectsLookalikesAndJunk() {
        assertNull(PairCode.parse("ABCD-EFG0"))
        assertNull(PairCode.parse("hello"))
        assertNull(PairCode.parse("https://example.com/?q=1"))
        assertNull(PairCode.parse("diablo://pair?host=javascript:alert(1)&code=ABCDEFGH")?.host)
    }
}
