package com.marcusrosado.diablo

import org.junit.Assert.assertEquals
import org.junit.Test

class PostureTest {
    // Window sizes in dp as a Pixel 10 Pro Fold reports them.
    @Test fun outerScreenUpright() = assertEquals(Posture.Portrait, postureFor(411, 914))
    @Test fun outerScreenSideways() = assertEquals(Posture.Landscape, postureFor(914, 411))
    @Test fun innerScreen() = assertEquals(Posture.Unfolded, postureFor(851, 882))
    @Test fun innerScreenSideways() = assertEquals(Posture.Unfolded, postureFor(882, 851))
    @Test fun squareSmallWindowStaysPortrait() = assertEquals(Posture.Portrait, postureFor(500, 500))
}
