package com.marcusrosado.diablo

import android.app.Application
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import com.marcusrosado.diablo.core.Store
import com.marcusrosado.diablo.render.buildImageLoader
import java.io.File

class DiabloApp : Application(), SingletonImageLoader.Factory {
    /** One store for the whole process: the screens and the background listener share it. */
    val store: Store by lazy { Store.open(File(filesDir, "diablo.db").path) }

    override fun newImageLoader(context: PlatformContext): ImageLoader = buildImageLoader(context)
}
