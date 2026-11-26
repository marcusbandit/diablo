package com.marcusrosado.diablo.assist

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
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

  /** His answer, and whether it waits on Marcus: a question card he asked, or a line ending in a question. */
  data class Reply(val text: String, val asks: Boolean)

  /** One turn in the main conversation. Returns his reply. Blocks: call it off the main thread. */
  fun chat(context: Context, text: String, screen: ScreenRead?): Reply {
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
    val out = JSONObject(post(context, "/v1/chat", "application/json", body.toString().toByteArray(), readMs = 10 * 60_000))
    val reply = out.optString("reply", "")
    return Reply(reply, asksQuestion(out.optJSONArray("objects")) || reply.trimEnd().endsWith("?"))
  }

  private fun asksQuestion(objects: JSONArray?): Boolean {
    if (objects == null) return false
    for (i in 0 until objects.length()) {
      val o = objects.optJSONObject(i) ?: continue
      if (o.optString("kind") == "question" && o.optString("state", "pending") == "pending") return true
    }
    return false
  }

  /** The reply in his own voice, the one the hub and the desk speak with, as a wav. */
  fun speech(context: Context, text: String): ByteArray {
    val body = JSONObject().put("text", text).toString().toByteArray()
    return postBytes(context, "/v1/speech", "application/json", body, readMs = 60_000, retrySlow = false)
  }

  /** A clip transcribed by the hub's speech to text, in the order the hub keeps for phones. */
  fun transcribe(context: Context, wav: ByteArray): String {
    val out = post(context, "/v1/stt", "audio/wav", wav, readMs = 60_000, extra = mapOf("X-Filename" to "phone.wav", "X-Stt-For" to "phone"))
    return JSONObject(out).optString("text", "").trim()
  }

  /** Words speech to text gets wrong: the right forms to listen for, and the fix for what was heard. */
  class Words(private val rules: List<Pair<String, List<Regex>>>) {
    val rights get() = rules.map { it.first }

    fun fix(text: String): String = rules.fold(text) { line, (right, wrongs) ->
      wrongs.fold(line) { out, wrong -> wrong.replace(out, Regex.escapeReplacement(right)) }
    }
  }

  @Volatile private var cached: Pair<String, Words>? = null

  fun words(context: Context): Words {
    val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("words", null) ?: "[]"
    cached?.let { if (it.first == raw) return it.second }
    val list = try {
      JSONArray(raw)
    } catch (_: Exception) {
      JSONArray()
    }
    val rules = (0 until list.length()).mapNotNull { i ->
      val o = list.optJSONObject(i) ?: return@mapNotNull null
      val right = o.optString("right").trim()
      val wrong = o.optJSONArray("wrong") ?: return@mapNotNull null
      val forms = (0 until wrong.length()).map { wrong.optString(it).trim() }.filter { it.isNotEmpty() }
        .sortedByDescending { it.length }
        .map { form -> Regex("(?<!\\w)" + form.split(Regex("\\s+")).joinToString("\\s+") { Regex.escape(it) } + "(?!\\w)", RegexOption.IGNORE_CASE) }
      if (right.isEmpty() || forms.isEmpty()) null else right to forms
    }
    return Words(rules).also { cached = raw to it }
  }

  /** Asks the hub for the words again and keeps them for the sessions that hear without it. Blocks. */
  fun fetchWords(context: Context) {
    val out = JSONObject(get(context, "/v1/hearing"))
    val list = out.optJSONArray("words") ?: return
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("words", list.toString()).apply()
  }

  private fun get(context: Context, path: String): String {
    val token = token(context) ?: throw Failure("Pair me in the app first, sir.")
    var last: Exception? = null
    for (base in hubs(context)) {
      val conn = (URL(base + path).openConnection() as HttpURLConnection)
      try {
        conn.connectTimeout = 4000
        conn.readTimeout = 8000
        conn.setRequestProperty("X-Diablo-Token", token)
        if (conn.responseCode !in 200..299) throw Failure("The hub answered ${conn.responseCode}.")
        return conn.inputStream.use { it.readBytes() }.toString(Charsets.UTF_8)
      } catch (e: IOException) {
        last = e
      } finally {
        conn.disconnect()
      }
    }
    throw Failure(if (last != null) "I cannot reach the hub right now." else "No hub answered.")
  }

  private fun post(
    context: Context,
    path: String,
    type: String,
    data: ByteArray,
    readMs: Int,
    extra: Map<String, String> = emptyMap(),
  ): String = postBytes(context, path, type, data, readMs, extra).toString(Charsets.UTF_8)

  private fun postBytes(
    context: Context,
    path: String,
    type: String,
    data: ByteArray,
    readMs: Int,
    extra: Map<String, String> = emptyMap(),
    retrySlow: Boolean = true,
  ): ByteArray {
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
        val bytes = stream?.use { s -> ByteArrayOutputStream().also { s.copyTo(it) }.toByteArray() } ?: ByteArray(0)
        if (code == 403) throw Failure("The hub no longer knows this phone. Pair me again in the app.")
        if (code !in 200..299) throw Failure("The hub answered $code.")
        return bytes
      } catch (e: SocketTimeoutException) {
        // A hub that took the request but answered too slowly may still be doing it: asking the next would do it twice.
        val connecting = e.message?.contains("connect", ignoreCase = true) == true
        if (!retrySlow && !connecting) throw Failure("The hub took too long.")
        last = e
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
