package com.marcusrosado.diablo

/**
 * The three layouts the app knows about. Unfolded covers both orientations of the inner
 * screen, since that screen is close to square and one layout fits either way.
 */
enum class Posture {
    /** Outer screen, held upright. */
    Portrait,

    /** Outer screen, turned on its side: wide but short. */
    Landscape,

    /** Inner screen, either orientation. */
    Unfolded,
}

/**
 * Below this many dp on its shorter side a window counts as a phone screen.
 * 600 dp is the Material breakpoint between compact and medium.
 */
const val UNFOLDED_MIN_DP = 600

/**
 * Picks the posture from the window size alone. No hinge APIs needed: the outer screen is
 * narrow on one side whichever way it is held, and the inner screen is wide on both.
 */
fun postureFor(widthDp: Int, heightDp: Int): Posture = when {
    minOf(widthDp, heightDp) >= UNFOLDED_MIN_DP -> Posture.Unfolded
    widthDp > heightDp -> Posture.Landscape
    else -> Posture.Portrait
}
