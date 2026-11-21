package com.marcusrosado.diablo.assist

import android.app.assist.AssistStructure
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.view.View
import android.view.WindowManager
import java.util.concurrent.Executors

/**
 * What holding power opens: he floats over the app in front and listens at once. Android hands
 * the session that app's text and a screenshot, which go to the hub with the first line. Only
 * his own pieces take touches; the app underneath stays usable.
 */
class DiabloSession(context: Context) : VoiceInteractionSession(context), Floating.Host {
  private var floating: Floating? = null
  private var typeFirst = false
  private val encode = Executors.newSingleThreadExecutor()

  override fun onCreate() {
    super.onCreate()
    window?.window?.let { w ->
      w.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
      w.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
      w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING)
      w.setDecorFitsSystemWindows(false)
    }
  }

  override fun onCreateContentView(): View {
    val view = Floating(context, this)
    floating = view
    return view
  }

  override fun onShow(args: Bundle?, showFlags: Int) {
    super.onShow(args, showFlags)
    Overlay.hide(context)
    Talk.begin()
    typeFirst = args?.getBoolean(TYPE) == true
    val view = floating ?: return
    if (typeFirst) {
      view.post { view.openTyping() }
    } else if (!Talk.busy) {
      Talk.hear(context)
    }
  }

  override fun onHandleAssist(state: AssistState) {
    val structure: AssistStructure = state.assistStructure ?: return
    Talk.screen(ScreenRead.of(structure, null))
  }

  override fun onHandleScreenshot(screenshot: Bitmap?) {
    val shot = screenshot ?: return
    encode.execute { Talk.shot(ScreenRead.jpeg(shot)) }
  }

  override fun onComputeInsets(outInsets: Insets) {
    super.onComputeInsets(outInsets)
    val view = floating ?: return
    outInsets.contentInsets.top = view.height
    outInsets.touchableInsets = Insets.TOUCHABLE_INSETS_REGION
    view.touchable(outInsets.touchableRegion)
  }

  override fun onBackPressed() {
    val view = floating
    if (view != null && view.typing) view.closeTyping() else hide()
  }

  override fun onHide() {
    floating?.closeTyping()
    Talk.stopHearing()
    // A turn still running lingers in the overlay, when he may draw over apps.
    if (Talk.busy) Overlay.show(context)
    super.onHide()
  }

  override fun typing(open: Boolean) {
    floating?.requestLayout()
  }

  companion object {
    const val TYPE = "diablo.type"
  }
}
