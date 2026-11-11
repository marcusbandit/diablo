package com.marcusrosado.diablo.notify

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
import com.marcusrosado.diablo.DiabloApp
import com.marcusrosado.diablo.core.Host
import com.marcusrosado.diablo.core.Store
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.Hub
import com.marcusrosado.diablo.data.Sync
import com.marcusrosado.diablo.data.Vault
import com.marcusrosado.diablo.data.str
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.Call
import okhttp3.OkHttpClient
import java.io.IOException

/**
 * Holds the hub's event stream while the app is in the background, so a reply that lands
 * later, a T3 handoff or a wake report, becomes a notification. The stream asks the hub for
 * a keepalive every four minutes instead of every fifteen seconds: between events the
 * socket is silent and the phone sleeps. Everything stays on the tailnet.
 */
class Listener : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private val lock = Mutex()
    private val hub = Hub(OkHttpClient())
    private lateinit var store: Store
    private lateinit var vault: Vault
    private lateinit var notifier: Notifier
    private lateinit var power: PowerManager
    private var loop: Job? = null
    private var foreground = false
    @Volatile private var call: Call? = null

    private val network = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n: Network) {
            wake.trySend(Unit)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        store = (application as DiabloApp).store
        vault = Vault(this)
        notifier = Notifier(this)
        power = getSystemService(PowerManager::class.java)
        notifier.channels(store.sections())
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0
        foreground = runCatching { ServiceCompat.startForeground(this, Notifier.LISTENING_ID, notifier.listening("Starting"), type) }.isSuccess
        getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(network)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!foreground || !Notifier.wanted(this, store)) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (loop?.isActive != true) loop = scope.launch { run() } else wake.trySend(Unit)
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(network) }
        call?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun run() {
        var backoff = FIRST_RETRY
        while (currentCoroutineContext().isActive) {
            val token = vault.get(DiabloModel.TOKEN).orEmpty()
            val host = if (token.isBlank()) null else pick()
            if (host == null) {
                notifier.showListening(if (token.isBlank()) "Waiting for the hub token" else "No host answers, trying again soon")
                withTimeoutOrNull(IDLE_RETRY) { wake.receive() }
                continue
            }
            try {
                held { catchUp(host.url, token) }
                notifier.showListening("Connected to ${host.name}")
                backoff = FIRST_RETRY
                hub.events(host.url, token, ping = PING, started = { call = it }) { event -> on(host.url, token, event.str("kind"), event.str("section")) }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                notifier.showListening("Lost ${host.name}, reconnecting")
            }
            withTimeoutOrNull(backoff) { wake.receive() }
            backoff = (backoff * 2).coerceAtMost(IDLE_RETRY)
        }
    }

    private fun on(url: String, token: String, kind: String?, section: String?) {
        when (kind) {
            "message" -> if (section != null) scope.launch { held { check(url, token, section) } }
            "sections" -> scope.launch { held { refreshSections(url, token) } }
        }
    }

    private fun pick(): Host? {
        for (host in store.hosts()) {
            try {
                store.recordProbe(host.name, true, hub.health(host.url).toUInt())
                return host
            } catch (error: IOException) {
                store.recordProbe(host.name, false, 0u)
            }
        }
        return null
    }

    /** After a gap in the stream: whatever arrived meanwhile in any section. */
    private suspend fun catchUp(url: String, token: String) {
        refreshSections(url, token)
        store.sections().forEach { section ->
            try {
                check(url, token, section.id)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
            }
        }
    }

    private suspend fun refreshSections(url: String, token: String) = lock.withLock {
        store.mergeSections(hub.sections(url, token))
        notifier.channels(store.sections())
    }

    /** Pulls a section and notifies its replies newer than the last one already handled. */
    private suspend fun check(url: String, token: String, section: String) = lock.withLock {
        Sync.pull(hub, store, url, token, section)
        val newest = store.lastRemoteId(section)
        val seen = store.get(seenKey(section))?.toLongOrNull()
        val since = store.get(Notifier.SINCE)?.toDoubleOrNull() ?: 0.0
        store.set(seenKey(section), maxOf(seen ?: 0L, newest).toString())
        if (Presence.looking(section)) return@withLock
        val fresh = store.messages(section, 50u)
            .filter { it.role == "assistant" && (it.remoteId ?: 0L) > (seen ?: 0L) && (seen != null || it.ts >= since) }
            .map { Note(it.remoteId ?: 0L, Notifier.plain(it.text), it.ts) }
        val info = store.sections().firstOrNull { it.id == section } ?: return@withLock
        notifier.post(info, fresh)
    }

    /** Keeps the CPU up while one event is handled, even in Doze. */
    private suspend fun held(block: suspend () -> Unit) {
        val lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "diablo:listener")
        lock.acquire(HOLD_MS)
        try {
            block()
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
        } finally {
            if (lock.isHeld) lock.release()
        }
    }

    companion object {
        const val PING = 240
        private const val FIRST_RETRY = 10_000L
        private const val IDLE_RETRY = 300_000L
        private const val HOLD_MS = 30_000L

        fun seenKey(section: String) = "seen:$section"

        /** Starts or stops the listener to match the setting and the permission. */
        fun update(context: Context) {
            val store = (context.applicationContext as DiabloApp).store
            val intent = Intent(context, Listener::class.java)
            if (Notifier.wanted(context, store)) {
                runCatching { ContextCompat.startForegroundService(context, intent) }
            } else {
                context.stopService(intent)
            }
        }
    }
}

/** Brings the listener back after a reboot or an app update. */
class Boot : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            Listener.update(context)
        }
    }
}
