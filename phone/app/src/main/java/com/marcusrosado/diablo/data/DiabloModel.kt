package com.marcusrosado.diablo.data

import android.app.Application
import android.net.ConnectivityManager
import android.os.Build
import android.net.Network
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.marcusrosado.diablo.DiabloApp
import com.marcusrosado.diablo.core.Host
import com.marcusrosado.diablo.core.Message
import com.marcusrosado.diablo.core.MessageState
import com.marcusrosado.diablo.core.Section
import com.marcusrosado.diablo.core.Store
import com.marcusrosado.diablo.notify.Listener
import com.marcusrosado.diablo.notify.Note
import com.marcusrosado.diablo.notify.Notifier
import com.marcusrosado.diablo.notify.Presence
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.OkHttpClient
import java.io.IOException

private const val REFUSED = "not paired, pair again"

/** Which host the phone is talking to, or why it is not. */
data class Link(
    val host: String? = null,
    val ms: Int? = null,
    val queued: Int = 0,
    val problem: String? = null,
    val checking: Boolean = true,
) {
    /** The host answered but no longer knows this phone's token. */
    val refused: Boolean get() = problem == REFUSED
}

data class Probe(val ok: Boolean, val text: String)

/** Text a panel button wants in the composer of a section, without sending it. nonce makes a repeat land again. */
data class Draft(val section: String, val text: String, val nonce: Long = System.nanoTime())

/** A short line under a panel: what a tap did, or why it did nothing. */
data class Notice(val section: String, val text: String, val failed: Boolean, val nonce: Long = System.nanoTime())

/** Where pairing this phone with a hub stands. */
data class Pairing(
    val working: Boolean = false,
    val ok: Boolean? = null,
    val text: String? = null,
    /** A code from a QR code or link that names a hub this phone does not know yet, waiting for a yes. */
    val unknownHost: PairCode? = null,
)

/**
 * The phone's view of Diablo. The Rust store is the source of what is shown; the network
 * only adds to it. Everything reads from the store first, so a section opens instantly and
 * offline.
 */
class DiabloModel(app: Application) : AndroidViewModel(app) {
    private val store = (app as DiabloApp).store
    private val notifier = Notifier(app)
    private val vault = Vault(app)
    private val hub = Hub(OkHttpClient())
    private val sendLock = Mutex()
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private var streamJob: Job? = null
    private var streamHost: String? = null
    @Volatile private var streamCall: Call? = null
    @Volatile private var visible = true

    private val _sections = MutableStateFlow(store.sections())
    val sections: StateFlow<List<Section>> = _sections.asStateFlow()

    private val _current = MutableStateFlow(store.get("current") ?: "diablo")
    val current: StateFlow<String> = _current.asStateFlow()

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    private val _thinking = MutableStateFlow<Set<String>>(emptySet())
    val thinking: StateFlow<Set<String>> = _thinking.asStateFlow()

    private val _link = MutableStateFlow(Link())
    val link: StateFlow<Link> = _link.asStateFlow()

    private val _hosts = MutableStateFlow(store.hosts())
    val hosts: StateFlow<List<Host>> = _hosts.asStateFlow()

    private val _onboarded = MutableStateFlow(store.get("onboarded") == "1")
    val onboarded: StateFlow<Boolean> = _onboarded.asStateFlow()

    val hasToken: Boolean get() = !vault.get(TOKEN).isNullOrEmpty()

    private val _notify = MutableStateFlow(store.get(Notifier.SETTING))
    /** "1" on, "0" turned off, null never asked. */
    val notify: StateFlow<String?> = _notify.asStateFlow()

    private val _jumps = MutableStateFlow(0)
    /** Counts the times a notification opened a section, so the screens can go to it. */
    val jumps: StateFlow<Int> = _jumps.asStateFlow()

    private val _pairing = MutableStateFlow(Pairing())
    val pairing: StateFlow<Pairing> = _pairing.asStateFlow()

    private val _linked = MutableStateFlow(false)
    /** True while a pairing that came in through a link or QR code wants to be shown. */
    val linked: StateFlow<Boolean> = _linked.asStateFlow()

    private val _device = MutableStateFlow(if (hasToken) store.get(DEVICE) else null)
    /** The name this phone was paired under, or null when it has no token of its own. */
    val device: StateFlow<String?> = _device.asStateFlow()

    private val _panels = MutableStateFlow(loadPanels())
    /** Each section's panel blocks, as last fetched; kept in the store so they show offline. */
    val panels: StateFlow<Map<String, List<JsonObject>>> = _panels.asStateFlow()

    private val _draft = MutableStateFlow<Draft?>(null)
    val draft: StateFlow<Draft?> = _draft.asStateFlow()

    private val _notice = MutableStateFlow<Notice?>(null)
    val notice: StateFlow<Notice?> = _notice.asStateFlow()

    private val actedAt = java.util.concurrent.ConcurrentHashMap<String, Long>()

    private val _fleet = MutableStateFlow(Fleet.installed(app) && store.get(Fleet.DISMISSED) != "1")
    /** True while the retired Diablo Fleet app is still on the phone and the offer to remove it stands. */
    val fleet: StateFlow<Boolean> = _fleet.asStateFlow()

    private val network = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n: Network) = poke()
        override fun onLost(n: Network) = poke()
    }

    init {
        Presence.section = _current.value
        reloadMessages()
        viewModelScope.launch(Dispatchers.IO) { syncLoop() }
        app.getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(network)
    }

    override fun onCleared() {
        runCatching { getApplication<Application>().getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(network) }
    }

    fun select(id: String) {
        _current.value = id
        Presence.section = id
        notifier.cancel(id)
        store.set("current", id)
        reloadMessages()
        poke()
    }

    /** A panel button: send the line to the current section, or only put it in the composer. */
    fun say(text: String, send: Boolean) {
        if (send) send(text) else _draft.value = Draft(_current.value, text)
    }

    fun draftTaken() {
        _draft.value = null
    }

    /** Opens the section that shows a module's panel, like Workout for a tap on the push-up tile. */
    fun openModule(module: String) {
        val shown = panelsMap().filterValues { module in it }.keys
        val target = _sections.value.firstOrNull { it.id in shown && it.id != "diablo" && !it.deleted }
            ?: _sections.value.firstOrNull { it.id in shown && !it.deleted }
        if (target != null) select(target.id)
    }

    /** A tap in a panel, like a lamp switch. The answer replaces the panel; a failure says why. */
    fun act(act: JsonObject) {
        val section = _current.value
        viewModelScope.launch(Dispatchers.IO) {
            val token = token()
            val host = _link.value.host?.let { name -> store.hosts().firstOrNull { it.name == name } }
            if (host == null || token.isBlank()) {
                showNotice(Notice(section, "Offline: the hub has to answer for that.", true))
                return@launch
            }
            try {
                val (message, fresh) = hub.act(host.url, token, act, section)
                if (fresh != null) {
                    keepPanel(section, fresh)
                    actedAt[section] = System.currentTimeMillis()
                }
                showNotice(Notice(section, message, false))
            } catch (error: HubException) {
                showNotice(Notice(section, error.message ?: "That did nothing.", true))
                refreshPanel(host.url, token, section)
            } catch (error: IOException) {
                showNotice(Notice(section, "The hub stopped answering.", true))
            } catch (error: Exception) {
                showNotice(Notice(section, "The hub sent an answer this app cannot read.", true))
            }
        }
    }

    private fun showNotice(notice: Notice) {
        _notice.value = notice
        viewModelScope.launch {
            delay(NOTICE_MS)
            _notice.compareAndSet(notice, null)
        }
    }

    /** The screen showed this notice; it never shows again. */
    fun noticeShown(notice: Notice) {
        _notice.compareAndSet(notice, null)
    }

    /** The screens went to the section a notification opened; a recreated activity must not go there again. */
    fun jumpHandled() {
        _jumps.value = 0
    }

    fun dismissFleet() {
        store.set(Fleet.DISMISSED, "1")
        _fleet.value = false
    }

    fun fleetCheck() {
        _fleet.value = Fleet.installed(getApplication()) && store.get(Fleet.DISMISSED) != "1"
    }

    private fun panelsMap(): Map<String, List<String>> = hub.panels.ifEmpty {
        runCatching {
            Json.parseToJsonElement(store.get(PANELS_MAP) ?: "{}").jsonObject.mapValues { (_, v) ->
                v.jsonArray.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
            }
        }.getOrDefault(emptyMap())
    }

    private fun loadPanels(): Map<String, List<JsonObject>> = store.sections().associate { section ->
        section.id to parsePanel(store.get(PANEL + section.id))
    }.filterValues { it.isNotEmpty() }

    private fun parsePanel(text: String?): List<JsonObject> = runCatching {
        Json.parseToJsonElement(text ?: return emptyList()).jsonArray.mapNotNull { it as? JsonObject }
    }.getOrDefault(emptyList())

    private fun keepPanel(section: String, blocks: JsonArray) {
        store.set(PANEL + section, blocks.toString())
        _panels.update { it + (section to blocks.mapNotNull { block -> block as? JsonObject }) }
    }

    private fun refreshPanel(url: String, token: String, section: String) {
        runCatching { keepPanel(section, hub.panel(url, token, section)) }
    }

    fun send(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return
        viewModelScope.launch(Dispatchers.IO) {
            store.addOutgoing(_current.value, trimmed)
            reloadMessages()
            flush()
        }
    }

    fun retry(localId: String) {
        viewModelScope.launch(Dispatchers.IO) {
            store.retry(localId)
            reloadMessages()
            flush()
        }
    }

    fun saveSection(section: Section): String? = try {
        store.saveSection(section)
        sectionsChanged()
        null
    } catch (error: Exception) {
        error.message
    }

    fun removeSection(id: String) {
        runCatching { store.removeSection(id) }
        if (_current.value == id) select("diablo")
        sectionsChanged()
    }

    fun move(id: String, delta: Int) {
        val ids = _sections.value.map { it.id }.toMutableList()
        val from = ids.indexOf(id)
        val to = (from + delta).coerceIn(0, ids.lastIndex)
        if (from < 0 || from == to) return
        ids.add(to, ids.removeAt(from))
        runCatching { store.reorder(ids) }
        sectionsChanged()
    }

    fun saveHost(host: Host): String? = try {
        store.saveHost(host)
        _hosts.value = store.hosts()
        poke()
        Listener.update(getApplication())
        null
    } catch (error: Exception) {
        error.message
    }

    fun removeHost(name: String) {
        store.removeHost(name)
        _hosts.value = store.hosts()
        poke()
        Listener.update(getApplication())
    }

    fun token(): String = vault.get(TOKEN).orEmpty()

    /** A scanned QR code, a tapped pairing link, or typed letters. */
    fun pairFrom(raw: String) {
        val code = PairCode.parse(raw)
        if (code == null) {
            _pairing.value = Pairing(ok = false, text = "That is not a pairing code. It looks like ABCD-EFGH.")
            return
        }
        if (code.host != null && store.hosts().none { it.url.trimEnd('/') == code.host }) {
            _pairing.value = Pairing(unknownHost = code)
            return
        }
        pair(code.code, code.host)
    }

    fun confirmHost(yes: Boolean) {
        val code = _pairing.value.unknownHost ?: return
        if (yes) {
            _pairing.value = Pairing()
            pair(code.code, code.host)
        } else {
            _pairing.value = Pairing(ok = false, text = "Not paired. Only pair with a hub you run yourself.")
        }
    }

    fun pairFromLink(raw: String) {
        _linked.value = true
        pairFrom(raw)
    }

    fun dismissLink() {
        _linked.value = false
    }

    fun pair(code: String, hostUrl: String? = null) {
        if (_pairing.value.working) return
        _pairing.value = Pairing(working = true, text = "Pairing...")
        viewModelScope.launch(Dispatchers.IO) { _pairing.value = runPairing(code, hostUrl) }
    }

    private fun runPairing(code: String, hostUrl: String?): Pairing {
        val candidates = hostUrl?.let { url -> listOf(url.trimEnd('/')) } ?: store.hosts().map { it.url }
        if (candidates.isEmpty()) return Pairing(ok = false, text = "No hosts yet. Add one in settings first.")
        var last = "No host answered. Is Tailscale on?"
        for (url in candidates) {
            val paired = try {
                hub.pair(url, code, deviceName())
            } catch (error: HubException) {
                last = when (error.code) {
                    403 -> "The hub does not know that code. Codes last ten minutes and work once; ask for a new one."
                    429 -> "Too many wrong codes. Wait ten minutes, then ask for a new one."
                    else -> "The hub answered ${error.code}: ${error.message}"
                }
                if (error.code == 403 || error.code == 429) break
                continue
            } catch (error: IOException) {
                continue
            }
            val known = store.hosts()
            if (known.none { it.url.trimEnd('/') == url }) {
                val base = paired.hub.ifBlank { "hub" }
                val name = generateSequence(1) { it + 1 }.map { if (it == 1) base else "$base $it" }.first { n -> known.none { it.name == n } }
                store.saveHost(Host(name, url, (known.maxOfOrNull { it.priority } ?: 0) + 1, null, null, 0u))
                _hosts.value = store.hosts()
            }
            val previous = store.get(DEVICE_ID)?.toLongOrNull()
            val previousToken = token()
            vault.put(TOKEN, paired.token)
            store.set(DEVICE, paired.device)
            store.set(DEVICE_ID, paired.deviceId.toString())
            store.set(DEVICE_HOST, url)
            _device.value = paired.device
            if (previous != null && previous != paired.deviceId && previousToken.isNotBlank()) {
                runCatching { hub.revokeDevice(url, paired.token, previous) }
            }
            val check = checkHost(url, paired.hub.ifBlank { url }, paired.token)
            poke()
            Listener.update(getApplication())
            return Pairing(ok = check.ok, text = "Paired as ${paired.device}. ${check.text}")
        }
        return Pairing(ok = false, text = last)
    }

    private fun checkHost(url: String, name: String, token: String): Probe = try {
        val started = System.nanoTime()
        val count = hub.sections(url, token).count { !it.deleted }
        Probe(true, "Connected to $name in ${(System.nanoTime() - started) / 1_000_000} ms, $count sections there.")
    } catch (error: HubException) {
        Probe(false, "$name answered ${error.code}: ${error.message}")
    } catch (error: IOException) {
        Probe(false, "$name stopped answering: ${error.message}")
    }

    /** Locks this phone out on the hub and forgets its token. */
    fun unpair() {
        viewModelScope.launch(Dispatchers.IO) {
            val token = token()
            val id = store.get(DEVICE_ID)?.toLongOrNull()
            val url = store.get(DEVICE_HOST)?.takeIf { it.isNotBlank() } ?: store.hosts().firstOrNull()?.url
            val name = _device.value ?: "this phone"
            val heard = id != null && url != null && token.isNotBlank() &&
                runCatching { hub.revokeDevice(url, token, id) }.isSuccess
            vault.put(TOKEN, null)
            store.set(DEVICE_ID, "")
            _device.value = null
            _pairing.value = if (heard) Pairing() else Pairing(
                ok = false,
                text = "This phone forgot its key, but the hub did not hear it. Send Diablo /devices and revoke $name there.",
            )
            poke()
            Listener.update(getApplication())
        }
    }

    private fun deviceName(): String {
        val model = Build.MODEL.orEmpty()
        val maker = Build.MANUFACTURER.orEmpty()
        return if (model.startsWith(maker, ignoreCase = true) || maker.isBlank()) model else "$maker $model"
    }

    fun finishOnboarding() {
        store.set("onboarded", "1")
        _onboarded.value = true
        Listener.update(getApplication())
        poke()
    }

    /** A notification opened this section. */
    fun open(id: String) {
        _sections.value = store.sections()
        if (_sections.value.none { it.id == id }) return
        select(id)
        _jumps.value++
    }

    /** The app came to the front: what he is looking at now needs no notification. */
    fun shown() {
        visible = true
        notifier.cancel(_current.value)
        Listener.update(getApplication())
        poke()
    }

    /** The app left the screen: the background listener takes over, so this stream and the
     * periodic sync stop until it comes back. */
    fun hidden() {
        visible = false
        stopListening()
    }

    private fun stopListening() {
        streamJob?.cancel()
        streamCall?.cancel()
        streamJob = null
        streamHost = null
    }

    fun setNotifications(on: Boolean) {
        if (on) {
            store.set(Notifier.SINCE, (System.currentTimeMillis() / 1000.0).toString())
            store.sections().forEach { store.set(Listener.seenKey(it.id), "") }
        }
        store.set(Notifier.SETTING, if (on) "1" else "0")
        _notify.value = store.get(Notifier.SETTING)
        if (!on) notifier.cancelAll()
        notifier.channels(store.sections())
        Listener.update(getApplication())
    }

    fun notificationsAllowed(): Boolean = notifier.allowed()

    /** His latest reply in a section, as a notification would read it. */
    fun lastReply(section: String): String? = store.messages(section, 30u)
        .lastOrNull { it.role == "assistant" && it.text.isNotBlank() }
        ?.let { Notifier.plain(it.text) }

    /** A real notification from the main section, to check that they arrive. */
    fun testNotification() {
        val section = store.sections().firstOrNull { it.id == "diablo" } ?: return
        notifier.channels(store.sections())
        val text = "Notifications reach this phone. When a handoff or a report lands while the app is closed, it shows up like this."
        notifier.cancelTest()
        notifier.post(section, listOf(Note(0, text, System.currentTimeMillis() / 1000.0)), Notifier.TEST_TAG)
    }

    fun replayOnboarding() {
        store.set("onboarded", "0")
        _onboarded.value = false
    }

    /** Tries every host with the given token, for onboarding and settings. */
    suspend fun probe(token: String = token()): Probe = withContext(Dispatchers.IO) {
        val hosts = store.hosts()
        if (hosts.isEmpty()) return@withContext Probe(false, "No hosts yet. Add one in settings.")
        var reached: String? = null
        for (host in hosts) {
            val ms = try {
                hub.health(host.url)
            } catch (error: IOException) {
                store.recordProbe(host.name, false, 0u)
                continue
            }
            store.recordProbe(host.name, true, ms.toUInt())
            reached = "${host.name} answered in $ms ms"
            if (token.isBlank()) return@withContext Probe(true, "$reached. Pair this phone to talk to him.")
            return@withContext try {
                val count = hub.sections(host.url, token).count { !it.deleted }
                Probe(true, "Connected to ${host.name} in $ms ms, $count sections there.")
            } catch (error: HubException) {
                Probe(false, if (error.code == 403) "${host.name} answered, but it does not know this phone. Pair it again." else "${host.name}: ${error.message}")
            } catch (error: IOException) {
                Probe(false, "${host.name} stopped answering: ${error.message}")
            }
        }
        _hosts.value = store.hosts()
        Probe(false, reached ?: "No host answered. Is Tailscale on?")
    }

    fun poke() {
        wake.trySend(Unit)
    }

    private fun sectionsChanged() {
        _sections.value = store.sections()
        notifier.channels(_sections.value)
        poke()
    }

    private fun reloadMessages() {
        _messages.value = store.messages(_current.value, 300u)
        _link.value = _link.value.copy(queued = store.outbox().size)
    }

    private suspend fun syncLoop() {
        while (viewModelScope.isActive) {
            if (!visible) {
                wake.receive()
                runCatching { flush() }
                continue
            }
            runCatching { syncOnce() }
            withTimeoutOrNull(if (_link.value.host == null) 15_000L else 60_000L) { wake.receive() }
        }
    }

    private suspend fun syncOnce() {
        val token = token()
        val host = pickHost()
        _hosts.value = store.hosts()
        if (host == null) {
            _link.value = Link(queued = store.outbox().size, problem = "offline", checking = false)
            return
        }
        if (token.isBlank()) {
            _link.value = Link(host.name, host.lastMs?.toInt(), store.outbox().size, "not paired", false)
            return
        }
        try {
            syncSections(host.url, token)
            flush()
            pull(host.url, token, _current.value)
            refreshPanel(host.url, token, _current.value)
            _link.value = Link(host.name, host.lastMs?.toInt(), store.outbox().size, null, false)
            listen(host, token)
        } catch (error: HubException) {
            _link.value = Link(host.name, host.lastMs?.toInt(), store.outbox().size,
                if (error.code == 403) REFUSED else "hub error ${error.code}", false)
        } catch (error: IOException) {
            store.recordProbe(host.name, false, 0u)
            _link.value = Link(null, null, store.outbox().size, "offline", false)
        }
    }

    private fun pickHost(): Host? {
        for (host in store.hosts()) {
            try {
                val ms = hub.health(host.url)
                store.recordProbe(host.name, true, ms.toUInt())
                return store.hosts().firstOrNull { it.name == host.name }
            } catch (error: IOException) {
                store.recordProbe(host.name, false, 0u)
            }
        }
        return null
    }

    private fun syncSections(url: String, token: String) {
        val dirty = store.dirtySections()
        val remote = if (dirty.isNotEmpty()) {
            try {
                hub.putSections(url, token, dirty, keepPanels = true).also { store.markSectionsSynced(dirty) }
            } catch (error: HubException) {
                if (error.code != 400) throw error
                hub.sections(url, token, keepPanels = true)
            }
        } else {
            hub.sections(url, token, keepPanels = true)
        }
        store.mergeSections(remote)
        if (hub.panels.isNotEmpty()) store.set(PANELS_MAP, buildJsonObject {
            hub.panels.forEach { (id, modules) -> put(id, JsonArray(modules.map { JsonPrimitive(it) })) }
        }.toString())
        _sections.value = store.sections()
        notifier.channels(_sections.value)
        if (_sections.value.none { it.id == _current.value }) select("diablo")
    }

    private fun pull(url: String, token: String, section: String) {
        Sync.pull(hub, store, url, token, section)
        if (section == _current.value) reloadMessages()
    }

    /** Sends the outbox in order. A message that the hub refuses is marked failed; a network
     * failure leaves it queued for the next host. */
    private suspend fun flush() = sendLock.withLock {
        val token = token()
        if (token.isBlank()) return@withLock
        while (true) {
            val next = store.outbox().firstOrNull { it.state == MessageState.PENDING } ?: break
            val host = _link.value.host?.let { name -> store.hosts().firstOrNull { it.name == name } }
                ?: pickHost() ?: break
            if (store.dirtySections().isNotEmpty()) {
                try {
                    syncSections(host.url, token)
                } catch (error: IOException) {
                    break
                }
            }
            _thinking.value = _thinking.value + next.section
            try {
                val reply = hub.chat(host.url, token, next.section, next.text, next.localId)
                store.markSent(next.localId, reply.userId)
                store.mergeRemote(next.section, listOf(replyMessage(next.section, reply)))
            } catch (error: HubException) {
                if (error.code == 409 || error.code == 503) break
                store.markFailed(next.localId)
            } catch (error: IOException) {
                _link.value = _link.value.copy(host = null, problem = "offline")
                break
            } finally {
                _thinking.value = _thinking.value - next.section
                reloadMessages()
            }
        }
    }

    private fun replyMessage(section: String, reply: ChatReply) = Message(
        localId = "r${reply.id}",
        remoteId = reply.id,
        section = section,
        role = "assistant",
        text = reply.reply,
        ts = System.currentTimeMillis() / 1000.0,
        state = MessageState.SENT,
        meta = buildJsonObject {
            put("section", section)
            reply.objects?.let { put("objects", it) }
        }.toString(),
    )

    private fun listen(host: Host, token: String) {
        if (!visible) return
        if (streamJob?.isActive == true && streamHost == host.name) return
        stopListening()
        streamHost = host.name
        streamJob = viewModelScope.launch(Dispatchers.IO) {
            while (isActive) {
                try {
                    hub.events(host.url, token, started = { streamCall = it }) { event ->
                        when (event.str("kind")) {
                            "message" -> event.str("section")?.let { if (it == _current.value) launch { runCatching { pull(host.url, token, it) } } }
                            "sections" -> launch { runCatching { syncSections(host.url, token) } }
                            "panel" -> {
                                val changed = (event["modules"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.orEmpty()
                                val section = _current.value
                                val shown = panelsMap()[section]
                                val fresh = System.currentTimeMillis() - (actedAt[section] ?: 0L) < ACT_FRESH_MS
                                if (!fresh && (shown == null || changed.isEmpty() || changed.any { it in shown })) {
                                    launch { refreshPanel(host.url, token, section) }
                                }
                            }
                        }
                    }
                } catch (error: IOException) {
                    poke()
                    delay(10_000)
                }
            }
        }
    }

    companion object {
        const val TOKEN = "hub-token"
        const val DEVICE = "device-name"
        const val DEVICE_ID = "device-id"
        const val DEVICE_HOST = "device-host"
        const val PANEL = "panel."
        const val PANELS_MAP = "panels-map"
        const val NOTICE_MS = 4_000L
        const val ACT_FRESH_MS = 2_000L
    }
}
