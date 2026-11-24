package com.marcusrosado.diablo.assist

import android.app.assist.AssistStructure
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.voice.VoiceInteractionSession
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.Window
import android.view.WindowManager
import java.util.concurrent.Executors

/**
 * What holding power opens. Android hands the session the app in front, its text and a
 * screenshot, which go to the hub with the first line, and he listens at once.
 *
 * When he may draw over apps he is a bubble (Bubble.kt): the session draws nothing but the typing
 * bar, and only holds the microphone and the screen while he is being talked to. It goes once a
 * reply is done or Marcus taps the app, and the bubble stays until he is dragged into the X.
 *
 * Without that, his face, the card and the T sit in this window at the bottom as before, and the
 * session goes by itself a beat after a command is answered.
 */
class DiabloSession(context: Context) : VoiceInteractionSession(context), Floating.Host {
  private var floating: Floating? = null
  private val encode = Executors.newSingleThreadExecutor()
  private val ui = Handler(Looper.getMainLooper())
  private var bubble = false
  private val leave = Runnable {
    pending = false
    if (floating?.typing != true) hide()
  }
  /** The close timer is running. */
  private var pending = false
  private val closer: (Talk.Now) -> Unit = { now -> plan(now) }

  /** A reply finished while the typing bar was open: closing the bar without sending lets him go. */
  private var missed = false

  private fun plan(now: Talk.Now) {
    ui.removeCallbacks(leave)
    val wait = Talk.closeAfter(now)
    if (floating?.typing == true) {
      missed = wait != null
      return
    }
    missed = false
    pending = wait != null
    wait?.let { ui.postDelayed(leave, it) }
  }

  override fun onCreate() {
    super.onCreate()
    window?.window?.let { w ->
      w.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
      w.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
      w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING)
      w.setDecorFitsSystemWindows(false)
      // Only as tall as his pieces, at the bottom: a touch above reaches the app.
      w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT)
      w.setGravity(Gravity.BOTTOM)
      w.addFlags(WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH)
      val inner = w.callback ?: return@let
      w.callback = object : Window.Callback by inner {
        override fun dispatchTouchEvent(event: MotionEvent): Boolean {
          if (event.actionMasked == MotionEvent.ACTION_OUTSIDE) {
            // A touch on the bubble is his own, not a tap on the app.
            if (!(bubble && Bubble.hit(event.rawX, event.rawY))) outside()
            return true
          }
          return inner.dispatchTouchEvent(event)
        }
      }
    }
  }

  override fun onCreateContentView(): View {
    val view = Floating(context, this)
    floating = view
    return view
  }

  override fun onShow(args: Bundle?, showFlags: Int) {
    super.onShow(args, showFlags)
    current = this
    Talk.begin()
    bubble = Bubble.allowed(context)
    val view = floating ?: return
    view.docked = bubble
    if (bubble) Bubble.show(context)
    Talk.again = { again() }
    Talk.listen(closer)
    when {
      args?.getBoolean(TYPE) == true -> view.post { view.openTyping() }
      args?.getBoolean(LISTEN) == true || !Talk.busy -> Talk.hear(context)
    }
  }

  /** He asked something and finished saying it: listen for the answer. */
  private fun again() {
    if (current === this) {
      Talk.hear(context)
    } else if (bubble && Bubble.shown) {
      Bubble.talk(context)
    }
  }

  fun openTyping() {
    floating?.openTyping()
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
    if (view.typing) {
      outInsets.touchableInsets = Insets.TOUCHABLE_INSETS_FRAME
    } else {
      outInsets.touchableInsets = Insets.TOUCHABLE_INSETS_REGION
      // As a bubble nothing here takes a touch: his pieces are their own windows.
      if (bubble) outInsets.touchableRegion.setEmpty() else view.touchable(outInsets.touchableRegion)
    }
  }

  override fun onBackPressed() {
    val view = floating
    if (view != null && view.typing) view.closeTyping() else hide()
  }

  override fun onHide() {
    if (current === this) current = null
    Talk.unlisten(closer)
    floating?.closeTyping()
    ui.removeCallbacks(leave)
    pending = false
    missed = false
    Talk.stopHearing()
    super.onHide()
  }

  override fun outside() {
    if (bubble) {
      // A tap on the app puts him back to floating; whatever he was saying he finishes.
      hide()
      return
    }
    // Tapping him away means away: no reply read out later.
    Talk.quiet()
    hide()
  }

  override fun typing(open: Boolean) {
    // Typing needs the whole screen so the keyboard can lift the bar; a tap beside him then closes it.
    // Taps on the keyboard land in another window, so watching for outside touches stops meanwhile.
    window?.window?.let { w ->
      w.setLayout(
        WindowManager.LayoutParams.MATCH_PARENT,
        if (open) WindowManager.LayoutParams.MATCH_PARENT else WindowManager.LayoutParams.WRAP_CONTENT,
      )
      if (open) {
        w.clearFlags(WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH)
      } else {
        w.addFlags(WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH)
      }
    }
    floating?.requestLayout()
    if (open) {
      if (pending) missed = true
      pending = false
      ui.removeCallbacks(leave)
    } else if (missed) {
      plan(Talk.now)
    }
  }

  companion object {
    const val TYPE = "diablo.type"
    const val LISTEN = "diablo.listen"

    /** The session on screen now, if any: while it is, he can hear and type. */
    @Volatile var current: DiabloSession? = null
      private set
  }
}
