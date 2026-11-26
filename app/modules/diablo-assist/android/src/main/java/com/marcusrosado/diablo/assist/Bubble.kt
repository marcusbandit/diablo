package com.marcusrosado.diablo.assist

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.ComponentCallbacks
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.PorterDuff
import android.graphics.PorterDuffColorFilter
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.TypedValue
import android.view.Choreographer
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowInsets
import android.view.WindowManager
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Diablo as a bubble over every app, the way Android's chat bubbles work. His face floats at the
 * side of the screen: a tap talks to him, the T beside him types, and his reply shows in a card
 * beside him that folds away once it has been read. He can be dragged anywhere and settles at the
 * nearer side. Dragged, a target with an X rises at the bottom centre; let go over it and he goes,
 * quiet. Pulled back out before letting go, he stays. The bubble outlives the assistant session,
 * which only reads the screen, holds the microphone and hosts the typing bar.
 */
object Bubble {
  private val ui = Handler(Looper.getMainLooper())

  private var app: Context? = null
  private var wm: WindowManager? = null
  private var head: Head? = null
  private var headParams: WindowManager.LayoutParams? = null
  private var typeButton: View? = null
  private var typeParams: WindowManager.LayoutParams? = null
  private var card: Card? = null
  private var cardParams: WindowManager.LayoutParams? = null
  private var target: Target? = null
  private var targetParams: WindowManager.LayoutParams? = null

  /** The centre of his orb on screen, in pixels. */
  private var cx = 0f
  private var cy = 0f
  /** -1 docked on the left, 1 on the right. */
  private var side = 1

  /** The line whose card already folded away after being read: a drag does not bring it back. */
  private var folded: Talk.Now? = null
  private val hideCard = Runnable {
    folded = lastShown
    foldCard()
  }
  private val follow: (Talk.Now) -> Unit = { show(it) }
  private var lastShown: Talk.Now? = null

  private val turns = object : ComponentCallbacks {
    override fun onConfigurationChanged(newConfig: Configuration) {
      // A fold or a turn changes the screen under him: he keeps his side and his height.
      ui.post { if (head != null) settle(side, savedFraction(), animate = false) }
    }

    @Deprecated("Deprecated in Java")
    override fun onLowMemory() {}
  }

  fun allowed(context: Context) = Settings.canDrawOverlays(context)

  val shown get() = head != null

  /** Whether a point on screen lands on one of his pieces. */
  fun hit(x: Float, y: Float): Boolean {
    for (view in listOfNotNull(head, typeButton?.takeIf { it.visibility == View.VISIBLE }, card)) {
      if (!view.isAttachedToWindow) continue
      val at = IntArray(2)
      view.getLocationOnScreen(at)
      if (x >= at[0] && x < at[0] + view.width && y >= at[1] && y < at[1] + view.height) return true
    }
    return false
  }

  /** Puts him on screen where he was last left, popping in, or does nothing when he is there. */
  fun show(context: Context) {
    if (head != null || !allowed(context)) return
    val ctx = context.applicationContext
    app = ctx
    val manager = ctx.getSystemService(WindowManager::class.java) ?: return
    wm = manager
    val h = Head(ctx)
    val hp = params(dp(HEAD), dp(HEAD))
    head = h
    headParams = hp

    val t = typeView(ctx)
    val tp = params(dp(TYPE), dp(TYPE))
    typeButton = t
    typeParams = tp

    val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    side = prefs.getInt("side", 1)
    place(side, prefs.getFloat("y", 0.62f))
    val x = Target(ctx)
    target = x
    targetParams = targetParams()
    x.centreY = targetY() - targetParams!!.y
    // Hidden between drags: a visible overlay, even one that takes no touches, can block taps on the app.
    x.visibility = View.GONE
    manager.addView(x, targetParams)
    manager.addView(t, tp)
    manager.addView(h, hp)
    t.alpha = 0f
    t.animate().alpha(1f).setStartDelay(160).setDuration(180).start()
    h.pop()
    ctx.registerComponentCallbacks(turns)
    Talk.listen(follow)
  }

  /** Takes him off the screen at once, quieting whatever he was doing. */
  fun dismiss() {
    Talk.quiet()
    DiabloSession.current?.hide()
    remove()
  }

  private fun remove() {
    ui.removeCallbacks(hideCard)
    Talk.unlisten(follow)
    app?.unregisterComponentCallbacks(turns)
    spring?.stop()
    val manager = wm
    for (view in listOfNotNull(head, typeButton, card, target)) {
      if (view.isAttachedToWindow) manager?.removeViewImmediate(view)
    }
    head = null
    typeButton = null
    card = null
    target = null
    lastShown = null
    folded = null
  }

  /** His face was tapped: listen, or send what he heard so far when already listening. */
  fun talk(context: Context) {
    if (Talk.now.phase == Talk.Phase.LISTENING) {
      Talk.finishHearing()
      return
    }
    if (DiabloSession.current != null) {
      Talk.hear(context)
    } else if (!DiabloAssistService.reopen(type = false, listen = true)) {
      Talk.hear(context)
    }
  }

  private fun type() {
    val session = DiabloSession.current
    if (session != null) session.openTyping() else DiabloAssistService.reopen(type = true, listen = false)
  }

  // ---------- where he sits ----------

  private val metrics get() = wm!!.currentWindowMetrics
  private val width get() = metrics.bounds.width()
  private val height get() = metrics.bounds.height()
  private val safe
    get() = metrics.windowInsets.getInsetsIgnoringVisibility(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())

  private fun edgeX(s: Int): Float {
    val r = dp(ORB) / 2f
    return if (s < 0) dp(GAP) + r else width - dp(GAP) - r
  }

  private fun topY() = safe.top + dp(ORB) / 2f + dp(12f)
  private fun bottomY() = height - safe.bottom - dp(ORB) / 2f - dp(12f)

  private fun place(s: Int, fraction: Float) {
    cx = edgeX(s)
    cy = topY() + (bottomY() - topY()) * fraction.coerceIn(0f, 1f)
    moveTo(cx, cy)
  }

  private fun savedFraction(): Float =
    app?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)?.getFloat("y", 0.62f) ?: 0.62f

  private fun save() {
    val span = bottomY() - topY()
    val fraction = if (span <= 0f) 0.5f else ((cy - topY()) / span).coerceIn(0f, 1f)
    app?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)?.edit()?.putInt("side", side)?.putFloat("y", fraction)?.apply()
  }

  private fun moveTo(x: Float, y: Float) {
    cx = x
    cy = y
    val hp = headParams ?: return
    hp.x = (x - dp(HEAD) / 2f).roundToInt()
    hp.y = (y - dp(HEAD) / 2f).roundToInt()
    head?.takeIf { it.isAttachedToWindow }?.let { wm?.updateViewLayout(it, hp) }
    placeType()
  }

  private fun placeType() {
    val tp = typeParams ?: return
    val gap = dp(ORB) / 2f + dp(8f) + dp(TYPE) / 2f
    val x = cx - side * gap
    tp.x = (x - dp(TYPE) / 2f).roundToInt()
    tp.y = (cy - dp(TYPE) / 2f).roundToInt()
    typeButton?.takeIf { it.isAttachedToWindow }?.let { wm?.updateViewLayout(it, tp) }
  }

  /** Glides him to a side at a height, the same spring a fling uses. */
  private fun settle(s: Int, fraction: Float, animate: Boolean) {
    side = s
    val y = topY() + (bottomY() - topY()) * fraction.coerceIn(0f, 1f)
    if (!animate) {
      moveTo(edgeX(s), y)
      placeCard()
      return
    }
    springTo(edgeX(s), y, 0f, 0f, STIFF_SETTLE) { landed() }
  }

  private fun landed() {
    save()
    showType(true)
    lastShown?.let {
      lastShown = null
      show(it)
    }
  }

  // ---------- the spring ----------

  private var spring: Spring? = null

  private fun springTo(x: Float, y: Float, vx: Float, vy: Float, stiffness: Float, done: () -> Unit = {}) {
    spring?.stop()
    spring = Spring(cx, cy, vx, vy, x, y, stiffness, ::moveTo, done).also { it.start() }
  }

  /**
   * A damped spring stepped every frame, so a fling carries its speed into the settle and a
   * target that moves mid-flight is followed without a jump.
   */
  private class Spring(
    var x: Float, var y: Float, var vx: Float, var vy: Float,
    var tx: Float, var ty: Float,
    val stiffness: Float,
    val step: (Float, Float) -> Unit,
    val done: () -> Unit,
  ) : Choreographer.FrameCallback {
    private var last = 0L
    var running = false
      private set
    private val damping = 2f * 0.78f * sqrt(stiffness)

    fun start() {
      running = true
      last = 0L
      Choreographer.getInstance().postFrameCallback(this)
    }

    fun stop() {
      running = false
      Choreographer.getInstance().removeFrameCallback(this)
    }

    override fun doFrame(frameTimeNanos: Long) {
      if (!running) return
      val dt = if (last == 0L) 1f / 60f else ((frameTimeNanos - last) / 1e9f).coerceIn(0.001f, 0.05f)
      last = frameTimeNanos
      var left = dt
      while (left > 0f) {
        val h = min(left, 1f / 240f)
        vx += (-stiffness * (x - tx) - damping * vx) * h
        vy += (-stiffness * (y - ty) - damping * vy) * h
        x += vx * h
        y += vy * h
        left -= h
      }
      val still = abs(x - tx) < 0.6f && abs(y - ty) < 0.6f && abs(vx) < 30f && abs(vy) < 30f
      if (still) {
        x = tx
        y = ty
      }
      step(x, y)
      if (still) {
        running = false
        done()
      } else {
        Choreographer.getInstance().postFrameCallback(this)
      }
    }
  }

  // ---------- dragging, and the X ----------

  private var downX = 0f
  private var downY = 0f
  private var grabX = 0f
  private var grabY = 0f
  private var dragging = false
  private var interrupted = false
  private var captured = false
  private var chasing = false
  private var velocity: VelocityTracker? = null

  private fun targetX() = width / 2f
  private fun targetY() = (height - safe.bottom - dp(72f)).toFloat()

  private fun touch(view: View, e: MotionEvent): Boolean {
    when (e.actionMasked) {
      MotionEvent.ACTION_DOWN -> {
        interrupted = spring?.running == true
        spring?.stop()
        downX = e.rawX
        downY = e.rawY
        grabX = cx
        grabY = cy
        dragging = false
        captured = false
        chasing = false
        velocity?.recycle()
        velocity = VelocityTracker.obtain().also { track(it, e) }
        head?.press(true)
      }
      MotionEvent.ACTION_MOVE -> {
        velocity?.let { track(it, e) }
        val dx = e.rawX - downX
        val dy = e.rawY - downY
        if (!dragging && hypot(dx, dy) > ViewConfiguration.get(view.context).scaledTouchSlop) startDrag()
        if (dragging) drag(grabX + dx, grabY + dy, view)
      }
      MotionEvent.ACTION_UP -> {
        velocity?.let { track(it, e) }
        head?.press(false)
        if (!dragging) {
          view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
          // Caught mid glide: he still settles at a side.
          if (interrupted) fling(0f, 0f)
          app?.let { talk(it) }
        } else if (captured) {
          view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
          drop()
        } else {
          val v = velocity
          v?.computeCurrentVelocity(1000)
          fling(v?.xVelocity ?: 0f, v?.yVelocity ?: 0f)
        }
        velocity?.recycle()
        velocity = null
        dragging = false
      }
      MotionEvent.ACTION_CANCEL -> {
        head?.press(false)
        if (dragging || interrupted) fling(0f, 0f)
        velocity?.recycle()
        velocity = null
        dragging = false
      }
    }
    return true
  }

  /** The window moves with the finger, so speed is measured on the screen, not in the window. */
  private fun track(tracker: VelocityTracker, e: MotionEvent) {
    val onScreen = MotionEvent.obtain(e)
    onScreen.setLocation(e.rawX, e.rawY)
    tracker.addMovement(onScreen)
    onScreen.recycle()
  }

  private fun startDrag() {
    dragging = true
    head?.press(false)
    head?.dragging = true
    showType(false)
    foldCard(quick = true)
    showTarget(true)
  }

  private fun drag(fx: Float, fy: Float, view: View) {
    val far = hypot(fx - targetX(), fy - targetY())
    if (!captured && far < dp(MAGNET_IN)) {
      captured = true
      chasing = false
      view.performHapticFeedback(HapticFeedbackConstants.GESTURE_THRESHOLD_ACTIVATE)
      target?.hold(true)
      springTo(targetX(), targetY(), 0f, 0f, STIFF_MAGNET)
      return
    }
    if (captured && far > dp(MAGNET_OUT)) {
      captured = false
      chasing = true
      view.performHapticFeedback(HapticFeedbackConstants.GESTURE_THRESHOLD_DEACTIVATE)
      target?.hold(false)
      springTo(fx, fy, 0f, 0f, STIFF_CHASE) { chasing = false }
      return
    }
    if (captured) return
    val s = spring
    if (chasing && s != null) {
      s.tx = fx
      s.ty = fy
    } else {
      moveTo(fx, fy)
    }
  }

  private fun fling(vx: Float, vy: Float) {
    head?.dragging = false
    captured = false
    chasing = false
    showTarget(false)
    val fast = dp(900f)
    val s = when {
      vx > fast -> 1
      vx < -fast -> -1
      else -> if (cx < width / 2f) -1 else 1
    }
    side = s
    val y = (cy + vy * 0.12f).coerceIn(topY(), bottomY())
    springTo(edgeX(s), y, vx, vy, STIFF_SETTLE) { landed() }
  }

  /** Let go over the X: he shrinks into it and goes, and so does the target. */
  private fun drop() {
    spring?.stop()
    moveTo(targetX(), targetY())
    target?.swallow()
    head?.vanish { dismiss() }
  }

  private fun targetParams(): WindowManager.LayoutParams {
    val tall = dp(TARGET_TALL)
    val top = height - tall
    return WindowManager.LayoutParams(
      WindowManager.LayoutParams.MATCH_PARENT, tall,
      WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
      WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
        WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
      PixelFormat.TRANSLUCENT,
    ).apply {
      gravity = Gravity.TOP or Gravity.START
      y = top
      layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
    }
  }

  /** The target lives under the head from the start, so he is always drawn above it; it only rises and sinks. */
  private fun showTarget(on: Boolean) {
    val t = target ?: return
    if (on) {
      val tp = targetParams()
      targetParams = tp
      t.centreY = targetY() - tp.y
      if (t.isAttachedToWindow) wm?.updateViewLayout(t, tp)
      t.visibility = View.VISIBLE
      t.rise(true)
    } else {
      t.hold(false)
      t.rise(false) { if (!dragging) it.visibility = View.GONE }
    }
  }

  // ---------- the T ----------

  private fun typeView(context: Context): View = FrameLayout(context).apply {
    background = GradientDrawable().apply {
      shape = GradientDrawable.OVAL
      setColor(GLASS)
      setStroke(dp(1f), EDGE)
    }
    addView(Glyph(context, Glyph.TYPE), FrameLayout.LayoutParams(dp(16f), dp(16f), Gravity.CENTER))
    contentDescription = "Type"
    isClickable = true
    setOnClickListener {
      it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
      type()
    }
  }

  private fun showType(on: Boolean) {
    val t = typeButton ?: return
    val tp = typeParams ?: return
    if (on) {
      placeType()
      tp.flags = tp.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE.inv()
      t.visibility = View.VISIBLE
      t.scaleX = 0.6f
      t.scaleY = 0.6f
      t.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(160).setInterpolator(OvershootInterpolator(1.6f)).start()
    } else {
      tp.flags = tp.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
      t.animate().alpha(0f).scaleX(0.6f).scaleY(0.6f).setDuration(100).start()
    }
    if (t.isAttachedToWindow) wm?.updateViewLayout(t, tp)
  }

  // ---------- the card ----------

  /** What the card holds: his line, and under it what Marcus is saying while he listens or thinks. */
  private fun cardText(now: Talk.Now): Pair<String, String> {
    val said = when (now.phase) {
      Talk.Phase.LISTENING, Talk.Phase.THINKING -> now.heard
      else -> ""
    }
    return Pair(now.reply, said)
  }

  private fun show(now: Talk.Now) {
    val before = lastShown
    lastShown = now
    head?.state(now.phase)
    ui.removeCallbacks(hideCard)
    if (dragging || now == folded) return
    val (his, said) = cardText(now)
    if (his.isEmpty() && said.isEmpty()) {
      foldCard()
      return
    }
    val changed = before == null || before.reply != now.reply || before.heard != now.heard || card == null
    if (changed) openCard(now)
    Talk.closeAfter(now)?.let { ui.postDelayed(hideCard, it) }
  }

  private fun openCard(now: Talk.Now) {
    val manager = wm ?: return
    val ctx = app ?: return
    val (his, said) = cardText(now)
    val c = card ?: Card(ctx).also { card = it }
    c.set(his, said)
    val fresh = !c.isAttachedToWindow
    if (fresh) {
      cardParams = params(cardWidth(), WindowManager.LayoutParams.WRAP_CONTENT)
    }
    val cp = cardParams ?: return
    cp.width = cardWidth()
    placeCard(c, cp)
    if (fresh) {
      manager.addView(c, cp)
      c.alpha = 0f
      c.scaleX = 0.92f
      c.scaleY = 0.92f
      c.post {
        c.pivotX = if (side > 0) c.width.toFloat() else 0f
        c.pivotY = if (cp.y < cy) c.height.toFloat() else 0f
        c.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(170).setInterpolator(DecelerateInterpolator()).start()
      }
    } else {
      c.animate().cancel()
      c.alpha = 1f
      c.scaleX = 1f
      c.scaleY = 1f
      manager.updateViewLayout(c, cp)
    }
  }

  private fun placeCard() {
    val c = card ?: return
    val cp = cardParams ?: return
    if (!c.isAttachedToWindow) return
    cp.width = cardWidth()
    placeCard(c, cp)
    wm?.updateViewLayout(c, cp)
  }

  private fun cardWidth() = min(width - dp(24f), dp(340f))

  private fun placeCard(c: Card, cp: WindowManager.LayoutParams) {
    val w = cp.width
    c.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(height * 3 / 10, View.MeasureSpec.AT_MOST))
    val h = c.measuredHeight
    // The window is as tall as the capped card, so a long reply scrolls inside it instead of spilling over him.
    cp.height = h
    cp.x = if (side > 0) width - dp(12f) - w else dp(12f)
    val above = cy - dp(ORB) / 2f - dp(10f) - h
    cp.y = if (above >= safe.top + dp(8f)) above.roundToInt() else (cy + dp(ORB) / 2f + dp(10f)).roundToInt()
  }

  private fun foldCard(quick: Boolean = false) {
    val c = card ?: return
    card = null
    c.animate().cancel()
    c.animate().alpha(0f).scaleX(0.94f).scaleY(0.94f).setDuration(if (quick) 90L else 220L).setListener(object : AnimatorListenerAdapter() {
      override fun onAnimationEnd(animation: Animator) {
        if (c.isAttachedToWindow) wm?.removeViewImmediate(c)
      }
    }).start()
  }

  // ---------- pieces ----------

  private fun params(w: Int, h: Int) = WindowManager.LayoutParams(
    w, h,
    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
      WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
    PixelFormat.TRANSLUCENT,
  ).apply {
    gravity = Gravity.TOP or Gravity.START
    layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
  }

  private fun dp(v: Float): Int {
    val ctx = app ?: return v.toInt()
    return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics).roundToInt()
  }

  /** His face in a glass orb, ringed and haloed in the colour of what he is doing. */
  @SuppressLint("ViewConstructor", "ClickableViewAccessibility")
  private class Head(context: Context) : View(context) {
    private val density = resources.displayMetrics.density
    private val body = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = GLASS }
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val halo = Paint(Paint.ANTI_ALIAS_FLAG)
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
      style = Paint.Style.STROKE
      strokeCap = Paint.Cap.ROUND
    }
    private val inkFilter = PorterDuffColorFilter(INK, PorterDuff.Mode.SRC_IN)
    private var face: Drawable? = null
    private var folder = "resting"
    private var phase = Talk.Phase.RESTING
    private var colour = STATE.getValue(Talk.Phase.RESTING)
    private var lastFace = 0L
    private var level = 0f
    private var pressed = 0f
    private var pressTarget = 0f
    private var scale = 0f
    private var scaleAnim: ValueAnimator? = null
    private val born = SystemClock.uptimeMillis()
    var dragging = false
      set(value) {
        field = value
        stir()
      }
    private val box = RectF()

    init {
      contentDescription = "Diablo"
      setOnTouchListener { v, e -> Bubble.touch(v, e) }
      setFace()
    }

    /** When something last happened to him; long at rest, he holds still and stops drawing. */
    private var stirred = SystemClock.uptimeMillis()

    fun stir() {
      stirred = SystemClock.uptimeMillis()
      invalidate()
    }

    fun state(p: Talk.Phase) {
      stir()
      if (p == phase) return
      phase = p
      colour = STATE.getValue(p)
      folder = FOLDER.getValue(p)
      setFace()
    }

    fun press(on: Boolean) {
      pressTarget = if (on) 1f else 0f
      stir()
    }

    fun pop() {
      scaleAnim?.cancel()
      scaleAnim = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 320
        interpolator = OvershootInterpolator(2.2f)
        addUpdateListener {
          scale = it.animatedValue as Float
          invalidate()
        }
        start()
      }
      performHapticFeedback(HapticFeedbackConstants.CONFIRM)
    }

    fun vanish(done: () -> Unit) {
      scaleAnim?.cancel()
      scaleAnim = ValueAnimator.ofFloat(scale, 0f).apply {
        duration = 200
        interpolator = DecelerateInterpolator()
        addUpdateListener {
          scale = it.animatedValue as Float
          invalidate()
        }
        addListener(object : AnimatorListenerAdapter() {
          override fun onAnimationEnd(animation: Animator) = done()
        })
        start()
      }
    }

    private fun setFace() {
      val n = Random.nextInt(1, 5)
      val id = resources.getIdentifier("diablo_face_${folder}_$n", "drawable", context.packageName)
        .takeIf { it != 0 } ?: resources.getIdentifier("diablo_face_${folder}_1", "drawable", context.packageName)
      if (id == 0) return
      face = ResourcesCompat.getDrawable(resources, id, null)?.mutate()?.apply { colorFilter = inkFilter }
    }

    override fun onDraw(canvas: Canvas) {
      val now = SystemClock.uptimeMillis()
      val t = (now - born) / 1000f
      if (now - lastFace >= FACE_MS && (phase != Talk.Phase.SPEAKING || level > 0.12f)) {
        lastFace = now
        setFace()
      }
      val heard = when (phase) {
        Talk.Phase.LISTENING -> Talk.micLevel
        Talk.Phase.SPEAKING -> Talk.voiceLevel()
        else -> 0f
      }
      level += (heard - level) * if (heard > level) 0.45f else 0.12f
      pressed += (pressTarget - pressed) * 0.35f

      val r = 30f * density
      val bob = if (dragging || pressTarget > 0f) 0f else sin(t * 2f * PI.toFloat() / 3.4f) * 2.5f * density
      val breath = when (phase) {
        Talk.Phase.RESTING -> 0.5f + 0.5f * sin(t * 2f * PI.toFloat() / 4f)
        Talk.Phase.THINKING -> 0.6f + 0.4f * sin(t * 2f * PI.toFloat() / 1.2f)
        else -> 0f
      }
      val s = scale * (1f - 0.1f * pressed) * (if (dragging) 1.06f else 1f)
      val x = width / 2f
      val y = height / 2f + bob
      canvas.save()
      canvas.scale(s, s, x, y)

      val spread = 3f * density + (5f * breath + 9f * level) * density
      halo.color = Color.argb((46 + 90 * level).toInt().coerceAtMost(150), Color.red(colour), Color.green(colour), Color.blue(colour))
      canvas.drawCircle(x, y, r + spread, halo)
      canvas.drawCircle(x, y, r, body)
      ring.color = colour
      ring.strokeWidth = 2f * density
      canvas.drawCircle(x, y, r - density, ring)
      if (phase == Talk.Phase.THINKING) {
        arc.color = Color.argb(255, 255, 214, 184)
        arc.strokeWidth = 2.6f * density
        box.set(x - r + density, y - r + density, x + r - density, y + r - density)
        canvas.drawArc(box, (t * 330f) % 360f, 80f, false, arc)
      }
      face?.let {
        val f = (FACE / 2f * density).toInt()
        it.setBounds((x - f).toInt(), (y - f).toInt(), (x + f).toInt(), (y + f).toInt())
        it.draw(canvas)
      }
      canvas.restore()
      val settled = (phase == Talk.Phase.RESTING || phase == Talk.Phase.TROUBLE) && !dragging && abs(pressTarget - pressed) < 0.01f && scaleAnim?.isRunning != true
      if (!settled || now - stirred < IDLE_MS) postInvalidateOnAnimation()
    }
  }

  /** The card with his reply, and what Marcus is saying under it while he listens. */
  @SuppressLint("ViewConstructor")
  private class Card(context: Context) : FrameLayout(context) {
    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = (v * density).toInt()
    private val regular: Typeface? = font(context, "diablo_bricolage_regular")
    private val medium: Typeface? = font(context, "diablo_bricolage_medium")
    private val his = TextView(context).apply {
      setTextColor(INK)
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
      setLineSpacing(0f, 1.14f)
      typeface = regular
    }
    private val said = TextView(context).apply {
      setTextColor(DIM)
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
      setLineSpacing(0f, 1.1f)
      typeface = medium
    }
    private val column = LinearLayout(context).apply {
      orientation = LinearLayout.VERTICAL
      addView(his)
      addView(said, LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }
    private val scroll = ScrollView(context).apply {
      isVerticalScrollBarEnabled = false
      addView(column)
    }

    init {
      background = GradientDrawable().apply {
        cornerRadius = dp(18f).toFloat()
        setColor(GLASS)
        setStroke(dp(1f), EDGE)
      }
      setPadding(dp(14f), dp(10f), dp(14f), dp(11f))
      addView(scroll, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    private var shown = ""

    fun set(line: String, heard: String) {
      val fresh = shown != line
      shown = line
      his.text = Talk.readable(line)
      his.visibility = if (line.isEmpty()) GONE else VISIBLE
      said.text = heard
      said.visibility = if (heard.isEmpty()) GONE else VISIBLE
      (said.layoutParams as LinearLayout.LayoutParams).topMargin = if (line.isEmpty()) 0 else dp(6f)
      if (fresh) scroll.post { scroll.scrollTo(0, 0) }
    }

    private fun font(context: Context, name: String): Typeface? {
      val id = context.resources.getIdentifier(name, "font", context.packageName)
      return if (id == 0) null else ResourcesCompat.getFont(context, id)
    }
  }

  /** The X at the bottom centre that rises while he is dragged, and swells when he is over it. */
  @SuppressLint("ViewConstructor")
  private class Target(context: Context) : View(context) {
    private val density = resources.displayMetrics.density
    var centreY = 0f
    private var up = 0f
    private var held = 0f
    private var gone = 0f
    private var upAnim: ValueAnimator? = null
    private var holdAnim: ValueAnimator? = null
    private val shade = Paint()
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val cross = Paint(Paint.ANTI_ALIAS_FLAG).apply {
      style = Paint.Style.STROKE
      strokeCap = Paint.Cap.ROUND
      color = INK
    }

    fun rise(on: Boolean, after: (Target) -> Unit = {}) {
      upAnim?.cancel()
      upAnim = ValueAnimator.ofFloat(up, if (on) 1f else 0f).apply {
        duration = if (on) 220 else 180
        interpolator = DecelerateInterpolator()
        addUpdateListener {
          up = it.animatedValue as Float
          invalidate()
        }
        if (!on) {
          addListener(object : AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: Animator) = after(this@Target)
          })
        }
        start()
      }
    }

    fun hold(on: Boolean) {
      holdAnim?.cancel()
      holdAnim = ValueAnimator.ofFloat(held, if (on) 1f else 0f).apply {
        duration = 160
        interpolator = if (on) OvershootInterpolator(2f) else DecelerateInterpolator()
        addUpdateListener {
          held = it.animatedValue as Float
          invalidate()
        }
        start()
      }
    }

    /** He was dropped in: the target takes him with a short pulse, then sinks. */
    fun swallow() {
      ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 260
        addUpdateListener {
          gone = it.animatedValue as Float
          invalidate()
        }
        addListener(object : AnimatorListenerAdapter() {
          override fun onAnimationEnd(animation: Animator) = rise(false)
        })
        start()
      }
    }

    override fun onDraw(canvas: Canvas) {
      val w = width.toFloat()
      val h = height.toFloat()
      shade.shader = LinearGradient(0f, 0f, 0f, h, Color.TRANSPARENT, Color.argb((150 * up).toInt(), 0, 0, 0), Shader.TileMode.CLAMP)
      canvas.drawRect(0f, 0f, w, h, shade)
      val lift = (1f - up) * 48f * density
      val x = w / 2f
      val y = centreY + lift
      val pulse = if (gone > 0f) sin(gone * PI.toFloat()) * 0.18f else 0f
      val r = 28f * density * (1f + 0.32f * held + pulse)
      val hot = held
      disc.color = blend(GLASS, Color.argb(230, 0x7a, 0x2a, 0x34), hot)
      disc.alpha = (disc.alpha * up).toInt()
      canvas.drawCircle(x, y, r, disc)
      edge.color = blend(EDGE, STATE.getValue(Talk.Phase.TROUBLE), hot)
      edge.alpha = (Color.alpha(edge.color) * up).toInt()
      edge.strokeWidth = (1f + 1.2f * hot) * density
      canvas.drawCircle(x, y, r, edge)
      cross.strokeWidth = 2.4f * density
      cross.alpha = (255 * up * (1f - 0.4f * hot)).toInt()
      val a = 8f * density * (1f + 0.2f * held)
      canvas.drawLine(x - a, y - a, x + a, y + a, cross)
      canvas.drawLine(x - a, y + a, x + a, y - a, cross)
    }

    private fun blend(a: Int, b: Int, t: Float): Int {
      val u = t.coerceIn(0f, 1f)
      fun mix(p: Int, q: Int) = (p + (q - p) * u).toInt()
      return Color.argb(mix(Color.alpha(a), Color.alpha(b)), mix(Color.red(a), Color.red(b)), mix(Color.green(a), Color.green(b)), mix(Color.blue(a), Color.blue(b)))
    }
  }

  private const val PREFS = "diablo-bubble"
  private const val HEAD = 104f
  private const val ORB = 60f
  private const val TYPE = 38f
  private const val GAP = 8f
  private const val MAGNET_IN = 84f
  private const val MAGNET_OUT = 120f
  private const val TARGET_TALL = 220f
  private const val STIFF_SETTLE = 420f
  private const val STIFF_MAGNET = 900f
  private const val STIFF_CHASE = 1600f
  /** The face drawings fill half their square, so they are drawn past the orb to fill it. */
  private const val FACE = 64f
  private const val FACE_MS = 250L
  private const val IDLE_MS = 30_000L

  private val GLASS = Color.argb(200, 18, 20, 24)
  private val EDGE = Color.argb(46, 255, 255, 255)
  private val INK = Color.rgb(242, 244, 246)
  private val DIM = Color.argb(190, 242, 244, 246)

  private val STATE = mapOf(
    Talk.Phase.RESTING to Color.rgb(0x8d, 0x96, 0xa0),
    Talk.Phase.LISTENING to Color.rgb(0x7f, 0xb3, 0xd4),
    Talk.Phase.THINKING to Color.rgb(0xe0, 0x91, 0x5a),
    Talk.Phase.SPEAKING to Color.rgb(0x6f, 0xc4, 0x9a),
    Talk.Phase.TROUBLE to Color.rgb(0xf2, 0x9b, 0xa8),
  )
  private val FOLDER = mapOf(
    Talk.Phase.RESTING to "resting",
    Talk.Phase.LISTENING to "listening",
    Talk.Phase.THINKING to "thinking",
    Talk.Phase.SPEAKING to "talking",
    Talk.Phase.TROUBLE to "sorry",
  )
}
