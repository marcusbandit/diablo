package com.marcusrosado.diablo.listener

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Holds the hub's event stream while the app is in the background, so a reply that lands later
 * becomes a notification. It asks the hub for a keepalive every four minutes instead of every
 * fifteen seconds, so the socket is silent between events and the radio can sleep. A foreground
 * service keeps network access in Doze; each event is handled under a short wake lock.
 */
class ListenerService : Service() {
  private val lock = Object()
  @Volatile private var running = false
  @Volatile private var conn: HttpURLConnection? = null
  @Volatile private var poked = false
  private var worker: Thread? = null
  private lateinit var notifier: Notifier
  private lateinit var power: PowerManager
  private var foreground = false

  private val network = object : ConnectivityManager.NetworkCallback() {
    override fun onAvailable(n: Network) = poke()
  }

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onCreate() {
    super.onCreate()
    notifier = Notifier(this)
    power = getSystemService(PowerManager::class.java)
    notifier.channels(Prefs.sections(this))
    val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0
    foreground = runCatching {
      ServiceCompat.startForeground(this, Notifier.LISTENING_ID, notifier.listening("Starting"), type)
    }.isSuccess
    runCatching { getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(network) }
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (!foreground || !Prefs.wanted(this)) {
      stopSelf()
      return START_NOT_STICKY
    }
    if (intent?.getBooleanExtra(EXTRA_RECONNECT, false) == true) conn?.disconnect()
    if (worker?.isAlive != true) {
      running = true
      worker = Thread({ loop() }, "diablo-listener").also { it.start() }
    } else {
      poke()
    }
    return START_STICKY
  }

  override fun onDestroy() {
    running = false
    runCatching { getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(network) }
    conn?.disconnect()
    poke()
    super.onDestroy()
  }

  private fun poke() {
    synchronized(lock) {
      poked = true
      lock.notifyAll()
    }
  }

  private fun pause(ms: Long) {
    synchronized(lock) {
      if (!poked) runCatching { lock.wait(ms) }
      poked = false
    }
  }

  private fun loop() {
    var backoff = FIRST_RETRY
    while (running) {
      if (!Prefs.wanted(this)) {
        stopSelf()
        return
      }
      val token = Prefs.token(this)
      val hub = token?.let { pick() }
      if (token == null || hub == null) {
        notifier.showListening(if (token == null) "Waiting for this phone to be paired" else "No hub answers, trying again soon")
        pause(IDLE_RETRY)
        continue
      }
      val name = runCatching { URL(hub).host }.getOrDefault(hub)
      try {
        held { catchUp(hub, token) }
        notifier.showListening("Connected to $name")
        backoff = FIRST_RETRY
        stream(hub, token)
      } catch (error: Exception) {
        if (!running) break
        notifier.showListening("Lost $name, reconnecting")
      }
      pause(backoff)
      backoff = (backoff * 2).coerceAtMost(IDLE_RETRY)
    }
  }

  private fun pick(): String? = Prefs.hubs(this).firstOrNull { base ->
    runCatching {
      val c = open("$base/v1/health", null, 3_000)
      try { c.responseCode in 200..299 } finally { c.disconnect() }
    }.getOrDefault(false)
  }

  private fun open(url: String, token: String?, readMs: Int): HttpURLConnection {
    val c = URL(url).openConnection() as HttpURLConnection
    c.connectTimeout = 5_000
    c.readTimeout = readMs
    c.setRequestProperty("Accept", "application/json")
    if (token != null) c.setRequestProperty(TOKEN_HEADER, token)
    return c
  }

  private fun getJson(url: String, token: String): JSONObject {
    val c = open(url, token, 15_000)
    try {
      val code = c.responseCode
      if (code !in 200..299) throw IllegalStateException("hub answered $code")
      return JSONObject(c.inputStream.bufferedReader().use { it.readText() })
    } finally {
      c.disconnect()
    }
  }

  /** Holds /v1/events until it closes. A silent socket longer than two keepalives counts as lost. */
  private fun stream(hub: String, token: String) {
    val c = open("$hub/v1/events?ping=$PING", token, (PING * 2 + 30) * 1000)
    c.setRequestProperty("Accept", "text/event-stream")
    conn = c
    try {
      if (c.responseCode !in 200..299) throw IllegalStateException("events ${c.responseCode}")
      BufferedReader(InputStreamReader(c.inputStream, Charsets.UTF_8)).use { reader ->
        while (running) {
          val line = reader.readLine() ?: break
          if (!line.startsWith("data:")) continue
          val event = runCatching { JSONObject(line.substring(5).trim()) }.getOrNull() ?: continue
          when (event.optString("kind")) {
            "message" -> event.optString("section").ifBlank { null }?.let { s -> held { check(hub, token, s) } }
            "sections" -> held { refreshSections(hub, token) }
          }
        }
      }
    } finally {
      conn = null
      c.disconnect()
    }
  }

  /** After a gap in the stream: whatever arrived meanwhile in any section. */
  private fun catchUp(hub: String, token: String) {
    refreshSections(hub, token)
    Prefs.sections(this).forEach { s -> runCatching { check(hub, token, s.id) } }
  }

  private fun refreshSections(hub: String, token: String) {
    val list = getJson("$hub/v1/sections", token).optJSONArray("sections") ?: JSONArray()
    Prefs.saveSections(this, list)
    notifier.channels(Prefs.sections(this))
  }

  /** Pulls a section and notifies its replies newer than the last one handled. */
  private fun check(hub: String, token: String, section: String) {
    val seen = Prefs.seen(this, section)
    val since = Prefs.since(this)
    val query = if (seen != null) "after=$seen&limit=100" else "limit=30"
    val id = URLEncoder.encode(section, "UTF-8")
    val messages = getJson("$hub/v1/sections/$id/messages?$query", token).optJSONArray("messages") ?: JSONArray()
    var newest = seen ?: 0L
    val fresh = mutableListOf<Note>()
    for (i in 0 until messages.length()) {
      val m = messages.optJSONObject(i) ?: continue
      val mid = m.optLong("id")
      newest = maxOf(newest, mid)
      if (m.optString("role") != "assistant") continue
      val ts = m.optDouble("ts", 0.0)
      if (seen == null && ts < since) continue
      if (seen != null && mid <= seen) continue
      if (m.optJSONObject("meta")?.has("superseded_by") == true) continue
      fresh += Note(mid, Plain.of(m.optString("text")), ts)
    }
    Prefs.setSeen(this, section, newest)
    if (Presence.looking(section)) return
    val info = Prefs.sections(this).firstOrNull { it.id == section } ?: return
    notifier.post(info, fresh)
  }

  /** Keeps the CPU up while one event is handled, even in Doze. */
  private fun held(block: () -> Unit) {
    val wake = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "diablo:listener")
    wake.acquire(HOLD_MS)
    try {
      block()
    } catch (error: Exception) {
      // The next event or reconnect tries again.
    } finally {
      if (wake.isHeld) wake.release()
    }
  }

  companion object {
    const val TOKEN_HEADER = "X-Diablo-Token"
    const val PING = 240
    const val EXTRA_RECONNECT = "reconnect"
    private const val FIRST_RETRY = 10_000L
    private const val IDLE_RETRY = 300_000L
    private const val HOLD_MS = 30_000L

    /** Starts or stops the listener to match the switch, the pairing and the permission. */
    fun update(context: Context, reconnect: Boolean = false) {
      val intent = Intent(context, ListenerService::class.java).putExtra(EXTRA_RECONNECT, reconnect)
      if (Prefs.wanted(context)) {
        runCatching { ContextCompat.startForegroundService(context, intent) }
      } else {
        context.stopService(intent)
      }
    }
  }
}

/** Brings the listener back after a reboot or an app update. */
class BootReceiver : BroadcastReceiver() {
  override fun onReceive(context: Context, intent: Intent) {
    if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
      ListenerService.update(context)
    }
  }
}
