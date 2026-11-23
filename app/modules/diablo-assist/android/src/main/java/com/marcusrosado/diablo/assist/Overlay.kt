package com.marcusrosado.diablo.assist

import android.content.Context
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager

/**
 * Draw over other apps: when the session closes while he is still thinking or speaking, his face
 * and the card stay floating over whatever is in front until he is done, then the same beat the
 * session gives a finished reply. A reply that waits on Marcus stays longer. Tapping him opens
 * the session again, typing.
 */
object Overlay {
  private const val LINGER_MS = 25_000L
  private val ui = Handler(Looper.getMainLooper())
  private var view: Floating? = null
  private val gone = Runnable { remove() }
  private val follow: (Talk.Now) -> Unit = { now ->
    ui.removeCallbacks(gone)
    val resting = now.phase == Talk.Phase.RESTING || now.phase == Talk.Phase.TROUBLE
    val wait = Talk.closeAfter(now) ?: if (resting) LINGER_MS else null
    if (wait != null) ui.postDelayed(gone, wait)
  }

  fun allowed(context: Context) = Settings.canDrawOverlays(context)

  fun show(context: Context) {
    if (view != null || !allowed(context)) return
    val app = context.applicationContext
    val wm = app.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    val floating = Floating(app, object : Floating.Host {
      override val canType = false
      override fun wantsTyping() {
        remove()
        DiabloAssistService.reopen(type = true)
      }

      override val besideCloses = true

      override fun outside() {
        ui.post {
          Talk.quiet()
          remove()
        }
      }
    })
    // A window only as tall as his pieces, at the bottom, so the rest of the screen stays the app's.
    val params = WindowManager.LayoutParams(
      WindowManager.LayoutParams.MATCH_PARENT,
      WindowManager.LayoutParams.WRAP_CONTENT,
      WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
      WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
        WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
      PixelFormat.TRANSLUCENT,
    ).apply {
      gravity = Gravity.BOTTOM
      layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
    }
    wm.addView(floating, params)
    view = floating
    Talk.listen(follow)
  }

  fun hide(@Suppress("UNUSED_PARAMETER") context: Context) {
    remove()
  }

  private fun remove() {
    ui.removeCallbacks(gone)
    Talk.unlisten(follow)
    val v = view ?: return
    view = null
    (v.context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(v)
  }
}
