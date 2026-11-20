package com.marcusrosado.diablo.assist

import android.app.assist.AssistStructure
import android.graphics.Bitmap
import android.util.Base64
import java.io.ByteArrayOutputStream

/**
 * What was on the screen when he was called: the app, its visible text as lines the hub takes as
 * context, and a screenshot. The hub keeps at most 12 lines of 400 characters and treats them as
 * untrusted text, never as orders.
 */
class ScreenRead(val lines: List<String>, val jpeg: String?) {
  fun withShot(shot: String?) = ScreenRead(lines, shot ?: jpeg)

  companion object {
    private const val LINES = 11
    private const val CHARS = 400

    fun of(structure: AssistStructure, shot: String?): ScreenRead {
      val app = structure.activityComponent?.packageName ?: ""
      val pieces = ArrayList<String>()
      for (i in 0 until structure.windowNodeCount) {
        walk(structure.getWindowNodeAt(i).rootViewNode, pieces, 0)
      }
      // Join the pieces into lines of up to CHARS, so as much of the screen as fits goes along.
      val lines = ArrayList<String>()
      if (app.isNotEmpty()) lines.add("Phone app in front: $app")
      val line = StringBuilder()
      for (piece in pieces) {
        if (lines.size >= LINES + 1) break
        if (line.isNotEmpty() && line.length + piece.length + 3 > CHARS) {
          lines.add(line.toString())
          line.setLength(0)
        }
        if (line.isNotEmpty()) line.append(" | ")
        line.append(piece.take(CHARS))
      }
      if (line.isNotEmpty() && lines.size < LINES + 1) lines.add(line.toString())
      return ScreenRead(lines, shot)
    }

    private fun walk(node: AssistStructure.ViewNode?, out: MutableList<String>, depth: Int) {
      if (node == null || depth > 60 || out.size > 400) return
      if (node.visibility != android.view.View.VISIBLE) return
      val text = node.text?.toString()?.let { " ".join(it) }
        ?: node.contentDescription?.toString()?.let { " ".join(it) }
      if (!text.isNullOrBlank() && (out.isEmpty() || out.last() != text)) out.add(text)
      for (i in 0 until node.childCount) walk(node.getChildAt(i), out, depth + 1)
    }

    private fun String.join(raw: String) = raw.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(this)

    /** A screenshot as base64 JPEG, at most 1080 px wide, well under the hub's 4 MB. */
    fun jpeg(bitmap: Bitmap): String {
      // The screenshot may be a hardware bitmap, which cannot be scaled: copy it into memory first.
      val soft = bitmap.copy(Bitmap.Config.ARGB_8888, false) ?: bitmap
      val scale = minOf(1f, 1080f / soft.width)
      val small = if (scale < 1f) Bitmap.createScaledBitmap(soft, (soft.width * scale).toInt(), (soft.height * scale).toInt(), true) else soft
      val out = ByteArrayOutputStream()
      small.compress(Bitmap.CompressFormat.JPEG, 72, out)
      return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }
  }
}
