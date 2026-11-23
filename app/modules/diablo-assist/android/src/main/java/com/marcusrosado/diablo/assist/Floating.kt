package com.marcusrosado.diablo.assist

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.Rect
import android.graphics.Region
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat
import kotlin.random.Random

/**
 * Diablo floating over another app: his face in a glass orb at the bottom, what he heard or
 * answered in a glass card above it, and a T button beside him. Tapping his face or the T opens
 * a glass bar to type in; its mic goes back to talking. Everything else on the screen is left to
 * the app underneath. Used by the assistant session and by the overlay that lingers after it.
 */
@SuppressLint("ViewConstructor")
class Floating(context: Context, private val host: Host) : FrameLayout(context) {
  interface Host {
    /** Whether this window can take the keyboard. */
    val canType: Boolean get() = true
    /** The typing bar opened or closed: the window may need focus for the keyboard. */
    fun typing(open: Boolean) {}
    /** His face or the T was tapped where typing is not possible here. */
    fun wantsTyping() {}
    /** A touch landed beside him: outside the window, where the app underneath gets it too, or beside the typing bar. */
    fun outside() {}
    /** The window is wider than his pieces and a tap on the empty part of it closes him too. */
    val besideCloses: Boolean get() = false
  }

  private val ui = Handler(Looper.getMainLooper())
  private val density = resources.displayMetrics.density
  private fun dp(v: Float) = (v * density).toInt()

  private val regular = font("diablo_bricolage_regular")
  private val medium = font("diablo_bricolage_medium")

  private val column = LinearLayout(context).apply {
    orientation = LinearLayout.VERTICAL
    gravity = Gravity.BOTTOM
  }

  private val cardText = TextView(context).apply {
    setTextColor(INK)
    setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
    setLineSpacing(0f, 1.12f)
    typeface = regular
  }
  private val card = object : ScrollView(context) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
      // At most 30% of the window, so on a half-folded phone he stays below the seam.
      val cap = resources.displayMetrics.heightPixels * 3 / 10
      super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(cap, MeasureSpec.AT_MOST))
    }
  }.apply {
    background = glass(dp(18f).toFloat())
    setPadding(dp(14f), dp(10f), dp(14f), dp(11f))
    isVerticalScrollBarEnabled = false
    addView(cardText)
    visibility = GONE
  }

  private val face = ImageView(context).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
  private val orb = FrameLayout(context).apply {
    addView(face, LayoutParams(dp(38f), dp(38f), Gravity.CENTER))
    isClickable = true
    contentDescription = "Diablo"
  }
  private val tx = FrameLayout(context).apply {
    background = glass(dp(20f).toFloat())
    addView(Glyph(context, Glyph.TYPE), LayoutParams(dp(18f), dp(18f), Gravity.CENTER))
    isClickable = true
    contentDescription = "Type"
  }
  private val row = FrameLayout(context).apply {
    addView(orb, LayoutParams(dp(58f), dp(58f), Gravity.CENTER))
    addView(tx, LayoutParams(dp(40f), dp(40f), Gravity.END or Gravity.CENTER_VERTICAL).apply { marginEnd = dp(10f) })
  }

  private val smallFace = ImageView(context).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
  private val smallOrb = FrameLayout(context).apply {
    addView(smallFace, LayoutParams(dp(24f), dp(24f), Gravity.CENTER))
  }
  private val field = EditText(context).apply {
    background = null
    setTextColor(INK)
    setHintTextColor(Color.argb(170, 242, 244, 246))
    hint = "Ask Diablo"
    setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
    typeface = regular
    inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or InputType.TYPE_TEXT_FLAG_MULTI_LINE
    maxLines = 4
    imeOptions = EditorInfo.IME_ACTION_SEND
    setRawInputType(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES)
    setPadding(dp(10f), dp(6f), dp(6f), dp(6f))
  }
  private val mic = FrameLayout(context).apply {
    background = GradientDrawable().apply {
      shape = GradientDrawable.OVAL
      setColor(Color.argb(36, 255, 255, 255))
    }
    addView(Glyph(context, Glyph.MIC), LayoutParams(dp(16f), dp(16f), Gravity.CENTER))
    isClickable = true
    contentDescription = "Talk"
  }
  private val bar = LinearLayout(context).apply {
    orientation = LinearLayout.HORIZONTAL
    gravity = Gravity.CENTER_VERTICAL
    background = glass(dp(28f).toFloat())
    setPadding(dp(7f), dp(7f), dp(7f), dp(7f))
    addView(smallOrb, LinearLayout.LayoutParams(dp(36f), dp(36f)))
    addView(field, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
    addView(mic, LinearLayout.LayoutParams(dp(36f), dp(36f)))
    visibility = GONE
  }

  private var typingOpen = false
  private var shown: Talk.Phase? = null
  private var folder = "resting"
  private val blink = object : Runnable {
    override fun run() {
      setFace()
      ui.postDelayed(this, 250)
    }
  }
  private val follow: (Talk.Now) -> Unit = { show(it) }

  init {
    clipChildren = false
    column.addView(card, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(12f) })
    column.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(64f)))
    column.addView(bar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    addView(column, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))

    orb.setOnClickListener { openTyping() }
    tx.setOnClickListener { openTyping() }
    mic.setOnClickListener {
      closeTyping()
      Talk.hear(context)
    }
    field.setOnEditorActionListener { _, action, event ->
      val enter = event?.keyCode == KeyEvent.KEYCODE_ENTER && event.action == KeyEvent.ACTION_DOWN
      if (action == EditorInfo.IME_ACTION_SEND || enter) {
        val text = field.text.toString()
        if (text.isNotBlank()) {
          field.setText("")
          closeTyping()
          Talk.send(context, text)
        }
        true
      } else {
        false
      }
    }

    setOnApplyWindowInsetsListener { _, insets ->
      val bottom = insets.getInsets(WindowInsets.Type.navigationBars() or WindowInsets.Type.ime()).bottom
      column.setPadding(dp(14f), 0, dp(14f), bottom + dp(14f))
      insets
    }
  }

  override fun onAttachedToWindow() {
    super.onAttachedToWindow()
    Talk.listen(follow)
    ui.post(blink)
    requestApplyInsets()
  }

  override fun onDetachedFromWindow() {
    Talk.unlisten(follow)
    ui.removeCallbacks(blink)
    super.onDetachedFromWindow()
  }

  override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
    val beside = (typingOpen || host.besideCloses) && ev.actionMasked == MotionEvent.ACTION_DOWN && !onPiece(ev.rawX.toInt(), ev.rawY.toInt())
    if (ev.actionMasked == MotionEvent.ACTION_OUTSIDE || beside) {
      host.outside()
      return true
    }
    return super.dispatchTouchEvent(ev)
  }

  override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
    super.onSizeChanged(w, h, oldw, oldh)
    // Wide (unfolded, or on its side): he keeps to the right half, beside the app, never across the hinge.
    val wide = w / density >= 600
    val lp = column.layoutParams as LayoutParams
    val width = if (wide) w / 2 else ViewGroup.LayoutParams.MATCH_PARENT
    if (lp.width != width) {
      lp.width = width
      lp.gravity = Gravity.BOTTOM or Gravity.END
      post { column.layoutParams = lp }
    }
  }

  fun openTyping() {
    if (!host.canType) {
      host.wantsTyping()
      return
    }
    if (typingOpen) return
    typingOpen = true
    Talk.stopHearing()
    row.visibility = GONE
    bar.visibility = VISIBLE
    host.typing(true)
    field.requestFocus()
    field.post {
      (context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).showSoftInput(field, InputMethodManager.SHOW_IMPLICIT)
    }
  }

  fun closeTyping() {
    if (!typingOpen) return
    typingOpen = false
    (context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).hideSoftInputFromWindow(field.windowToken, 0)
    bar.visibility = GONE
    row.visibility = VISIBLE
    host.typing(false)
  }

  val typing get() = typingOpen

  private fun show(now: Talk.Now) {
    val text = when {
      now.reply.isNotEmpty() -> now.reply
      now.phase == Talk.Phase.LISTENING || now.phase == Talk.Phase.THINKING -> now.heard
      else -> ""
    }
    cardText.text = text
    cardText.setTextColor(if (now.reply.isEmpty()) DIM else INK)
    cardText.typeface = if (now.reply.isEmpty()) medium else regular
    card.visibility = if (text.isEmpty()) GONE else VISIBLE
    if (now.reply.isNotEmpty()) card.post { card.scrollTo(0, 0) }
    if (shown != now.phase) {
      shown = now.phase
      folder = FOLDER.getValue(now.phase)
      val ring = STATE.getValue(now.phase)
      orb.background = ringed(ring, dp(29f).toFloat())
      smallOrb.background = ringed(ring, dp(18f).toFloat())
      setFace()
    }
  }

  private fun setFace() {
    val n = Random.nextInt(1, 5)
    val id = resources.getIdentifier("diablo_face_${folder}_$n", "drawable", context.packageName)
      .takeIf { it != 0 } ?: resources.getIdentifier("diablo_face_${folder}_1", "drawable", context.packageName)
    if (id == 0) return
    for (view in arrayOf(face, smallFace)) {
      view.setImageResource(id)
      view.setColorFilter(INK, PorterDuff.Mode.SRC_IN)
    }
  }

  private fun onPiece(x: Int, y: Int): Boolean {
    val at = IntArray(2)
    for (view in arrayOf(card, orb, tx, bar)) {
      if (!view.isShown || view.width == 0) continue
      view.getLocationOnScreen(at)
      if (x >= at[0] && x < at[0] + view.width && y >= at[1] && y < at[1] + view.height) return true
    }
    return false
  }

  /** Where he is on screen, in window pixels: only these parts take touches. */
  fun touchable(out: Region) {
    out.setEmpty()
    val at = IntArray(2)
    val box = Rect()
    for (view in arrayOf(card, orb, tx, bar)) {
      if (!view.isShown || view.width == 0) continue
      view.getLocationInWindow(at)
      box.set(at[0], at[1], at[0] + view.width, at[1] + view.height)
      out.op(box, Region.Op.UNION)
    }
  }

  private fun font(name: String): Typeface? {
    val id = resources.getIdentifier(name, "font", context.packageName)
    return if (id == 0) null else ResourcesCompat.getFont(context, id)
  }

  private fun glass(radius: Float) = GradientDrawable().apply {
    cornerRadius = radius
    setColor(GLASS)
    setStroke(dp(1f), EDGE)
  }

  /** The orb: glass with a ring and a soft halo in the colour of what he is doing. */
  private fun ringed(colour: Int, radius: Float): LayerDrawable {
    val halo = GradientDrawable().apply {
      shape = GradientDrawable.OVAL
      setColor(Color.TRANSPARENT)
      setStroke(dp(5f), Color.argb(70, Color.red(colour), Color.green(colour), Color.blue(colour)))
    }
    val body = GradientDrawable().apply {
      shape = GradientDrawable.OVAL
      setColor(GLASS)
      setStroke(dp(2f), colour)
    }
    return LayerDrawable(arrayOf(halo, body)).apply { setLayerInset(1, dp(3f), dp(3f), dp(3f), dp(3f)) }
  }

  companion object {
    private val GLASS = Color.argb(160, 18, 20, 24)
    private val EDGE = Color.argb(46, 255, 255, 255)
    private val INK = Color.rgb(242, 244, 246)
    private val DIM = Color.argb(200, 242, 244, 246)

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
}

/** The two small icons, drawn so they need no image: a T with a caret, and a microphone. */
@SuppressLint("ViewConstructor")
private class Glyph(context: Context, private val kind: Int) : View(context) {
  private val pen = Paint(Paint.ANTI_ALIAS_FLAG).apply {
    color = Color.rgb(242, 244, 246)
    style = Paint.Style.STROKE
    strokeCap = Paint.Cap.ROUND
    strokeJoin = Paint.Join.ROUND
  }

  override fun onDraw(canvas: Canvas) {
    val s = width / 18f
    pen.strokeWidth = 2f * s
    if (kind == TYPE) {
      canvas.drawLine(2 * s, 3 * s, 12 * s, 3 * s, pen)
      canvas.drawLine(7 * s, 3 * s, 7 * s, 15 * s, pen)
      canvas.drawLine(14 * s, 9 * s, 14 * s, 16 * s, pen)
    } else {
      pen.strokeWidth = 1.8f * s
      canvas.drawRoundRect(6 * s, 1.5f * s, 12 * s, 11 * s, 3 * s, 3 * s, pen)
      canvas.drawArc(3.5f * s, 4 * s, 14.5f * s, 14 * s, 0f, 180f, false, pen)
      canvas.drawLine(9 * s, 14 * s, 9 * s, 16.5f * s, pen)
    }
  }

  companion object {
    const val TYPE = 0
    const val MIC = 1
  }
}
