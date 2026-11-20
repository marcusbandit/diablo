package com.marcusrosado.diablo.assist

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/**
 * The assistant's own line to the hub. The app hands it the hubs and this device's token
 * (configure), kept in the app's private preferences, so a session can talk while the app is
 * closed. Every call tries the hubs in order: the Pi, then the standby.
 */
object HubLink {
  private const val PREFS = "diablo-assist"
  const val SECTION = "diablo"

  class Failure(message: String) : Exception(message)

  fun save(context: Context, hubs: List<String>, token: String?) {
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
      .putString("hubs", JSONArray(hubs).toString())
      .putString("token", token)
      .apply()
  }

  private fun hubs(context: Context): List<String> {
    val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("hubs", null) ?: return emptyList()
    val list = JSONArray(raw)
    return (0 until list.length()).map { list.getString(it).trimEnd('/') }.filter { it.isNotEmpty() }
  }

  private fun token(context: Context): String? =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("token", null)

  fun paired(context: Context) = token(context) != null && hubs(context).isNotEmpty()

  /** One turn in the main conversation. Returns his reply. Blocks: call it off the main thread. */
  fun chat(context: Context, text: String, screen: ScreenRead?): String {
    val body = JSONObject()
      .put("text", text)
      .put("source", "phone")
      .put("section", SECTION)
      .put("client_id", "assist-" + UUID.randomUUID())
    screen?.let {
      if (it.lines.isNotEmpty()) body.put("context", JSONArray(it.lines))
      it.jpeg?.let { shot ->
        body.put("images", JSONArray().put(JSONObject().put("data", shot).put("media_type", "image/jpeg")))
      }
    }
    val out = post(context, "/v1/chat", "application/json", body.toString().toByteArray(), readMs = 10 * 60_000)
    return JSONObject(out).optString("reply", "")
  }

  /** A clip transcribed by the hub's speech to text, the way the desk's ears do it. */
  fun transcribe(context: Context, wav: ByteArray): String {
    val out = post(context, "/v1/stt", "audio/wav", wav, readMs = 60_000, extra = mapOf("X-Filename" to "phone.wav"))
    return JSONObject(out).optString("text", "").trim()
  }

  private fun post(
    context: Context,
    path: String,
    type: String,
    data: ByteArray,
    readMs: Int,
    extra: Map<String, String> = emptyMap(),
  ): String {
    val token = token(context) ?: throw Failure("Pair me in the app first, sir.")
    val bases = hubs(context)
    if (bases.isEmpty()) throw Failure("Pair me in the app first, sir.")
    var last: Exception? = null
    for (base in bases) {
      val conn = (URL(base + path).openConnection() as HttpURLConnection)
      try {
        conn.requestMethod = "POST"
        conn.connectTimeout = 4000
        conn.readTimeout = readMs
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", type)
        conn.setRequestProperty("X-Diablo-Token", token)
        extra.forEach { (k, v) -> conn.setRequestProperty(k, v) }
        conn.outputStream.use { it.write(data) }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.use { s -> ByteArrayOutputStream().also { s.copyTo(it) }.toString("UTF-8") } ?: ""
        if (code == 403) throw Failure("The hub no longer knows this phone. Pair me again in the app.")
        if (code !in 200..299) throw Failure("The hub answered $code.")
        return text
      } catch (e: IOException) {
        // This hub did not answer: try the next one.
        last = e
      } finally {
        conn.disconnect()
      }
    }
    throw Failure(if (last != null) "I cannot reach the hub right now." else "No hub answered.")
  }
}
