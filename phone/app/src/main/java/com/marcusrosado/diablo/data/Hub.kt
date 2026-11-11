package com.marcusrosado.diablo.data

import com.marcusrosado.diablo.core.Message
import com.marcusrosado.diablo.core.MessageState
import com.marcusrosado.diablo.core.Section
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

class HubException(val code: Int, message: String) : IOException(message)

data class Paired(val token: String, val deviceId: Long, val device: String, val hub: String)

data class ChatReply(val reply: String, val id: Long, val userId: Long, val objects: JsonArray?)

/** The hub's HTTP API. Blocking calls: run them on Dispatchers.IO. */
class Hub(private val base: OkHttpClient) {
    /** Which modules each section shows, as the hub last said. The store does not keep these. */
    @Volatile var panels: Map<String, List<String>> = emptyMap()
        private set
    private val quick = base.newBuilder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()
    private val slow = base.newBuilder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.MINUTES)
        .build()
    private val stream = base.newBuilder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .build()
    private val jsonType = "application/json".toMediaType()

    /** Milliseconds the hub took to answer /v1/health. */
    fun health(url: String): Int {
        val started = System.nanoTime()
        quick.newCall(Request.Builder().url("$url/v1/health").build()).execute().use {
            if (!it.isSuccessful) throw HubException(it.code, "health ${it.code}")
        }
        return ((System.nanoTime() - started) / 1_000_000).toInt()
    }

    /** Trades a one-time pairing code for this device's own token. */
    fun pair(url: String, code: String, name: String): Paired {
        val body = buildJsonObject {
            put("code", code)
            put("name", name)
        }
        val out = call(quick, Request.Builder().url("$url/v1/pair").post(body.toString().toRequestBody(jsonType)).build())
        val device = out["device"]?.jsonObject
        return Paired(
            token = out.str("token") ?: throw HubException(500, "the hub sent no token"),
            deviceId = device?.long("id") ?: 0,
            device = device?.str("name") ?: name,
            hub = out.str("hub") ?: "",
        )
    }

    fun revokeDevice(url: String, token: String, id: Long) {
        call(quick, Request.Builder().url("$url/v1/devices/$id").header(TOKEN, token).delete().build())
    }

    /** keepPanels: this is the sync with the hub in use, so its module map replaces the one in panels. */
    fun sections(url: String, token: String, keepPanels: Boolean = false): List<Section> =
        sectionsFrom(get(url, token, "/v1/sections")["sections"]!!.jsonArray, keepPanels)

    private fun sectionsFrom(array: JsonArray, keepPanels: Boolean): List<Section> {
        if (keepPanels) panels = array.mapNotNull { it as? JsonObject }.associate { o ->
            (o.str("id") ?: "") to ((o["panels"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull } ?: emptyList())
        }
        return array.mapNotNull { section(it) }
    }

    /** What a section's panel shows now: a list of blocks, see docs/api.md. */
    fun panel(url: String, token: String, section: String): JsonArray =
        get(url, token, "/v1/sections/$section/panel")["objects"] as? JsonArray ?: JsonArray(emptyList())

    /** Runs a tap from a panel. Returns the hub's message and the section's fresh panel, if it sent one. */
    fun act(url: String, token: String, act: JsonObject, section: String): Pair<String, JsonArray?> {
        val body = JsonObject(act + ("section" to JsonPrimitive(section)))
        val request = Request.Builder().url("$url/v1/act").header(TOKEN, token)
            .post(body.toString().toRequestBody(jsonType)).build()
        val out = call(quick, request)
        return (out.str("message") ?: "done") to ((out["panel"] as? JsonObject)?.get("objects") as? JsonArray)
    }

    fun putSections(url: String, token: String, sections: List<Section>, keepPanels: Boolean = false): List<Section> {
        val body = buildJsonObject {
            put("sections", buildJsonArray { sections.forEach { add(sectionJson(it)) } })
        }
        val request = Request.Builder().url("$url/v1/sections").header(TOKEN, token)
            .put(body.toString().toRequestBody(jsonType)).build()
        return sectionsFrom(call(quick, request)["sections"]!!.jsonArray, keepPanels)
    }

    fun chat(url: String, token: String, section: String, text: String, clientId: String): ChatReply {
        val body = buildJsonObject {
            put("text", text)
            put("source", "phone")
            put("section", section)
            put("client_id", clientId)
        }
        val request = Request.Builder().url("$url/v1/chat").header(TOKEN, token)
            .post(body.toString().toRequestBody(jsonType)).build()
        val out = call(slow, request)
        return ChatReply(
            reply = out.str("reply") ?: "",
            id = out.long("id") ?: 0,
            userId = out.long("user_id") ?: 0,
            objects = out["objects"] as? JsonArray,
        )
    }

    fun messages(url: String, token: String, section: String, after: Long): List<Message> =
        get(url, token, "/v1/sections/$section/messages?after=$after&limit=200")["messages"]!!
            .jsonArray.mapNotNull { message(section, it) }

    /**
     * Reads the hub's event stream until it closes or fails; each event goes to onEvent.
     * ping asks the hub for a keepalive every that many seconds; a stream held in the
     * background asks for a slow one so the radio can sleep between them.
     */
    fun events(url: String, token: String, ping: Int? = null, started: (Call) -> Unit = {}, onEvent: (JsonObject) -> Unit) {
        val client = if (ping == null) stream else stream.newBuilder().readTimeout(ping + 60L, TimeUnit.SECONDS).build()
        val request = Request.Builder().url(if (ping == null) "$url/v1/events" else "$url/v1/events?ping=$ping").header(TOKEN, token).build()
        val call = client.newCall(request).also(started)
        call.execute().use { response ->
            if (!response.isSuccessful) throw HubException(response.code, "events ${response.code}")
            val source = response.body.source()
            while (true) {
                val line = source.readUtf8Line() ?: break
                if (line.startsWith("data:")) {
                    runCatching { Json.parseToJsonElement(line.removePrefix("data:").trim()).jsonObject }
                        .onSuccess(onEvent)
                }
            }
        }
    }

    private fun get(url: String, token: String, path: String): JsonObject =
        call(quick, Request.Builder().url("$url$path").header(TOKEN, token).build())

    private fun call(client: OkHttpClient, request: Request): JsonObject =
        client.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) {
                val detail = runCatching { Json.parseToJsonElement(text).jsonObject.str("detail") }.getOrNull()
                throw HubException(response.code, detail ?: "hub answered ${response.code}")
            }
            Json.parseToJsonElement(text).jsonObject
        }

    companion object {
        const val TOKEN = "X-Diablo-Token"

        fun section(element: JsonElement): Section? {
            val o = element as? JsonObject ?: return null
            return Section(
                id = o.str("id") ?: return null,
                name = o.str("name") ?: return null,
                icon = o.str("icon") ?: "folder",
                accent = o.str("accent") ?: "#2d81ff",
                brief = o.str("brief") ?: "",
                position = (o["position"] as? JsonPrimitive)?.intOrNull ?: 0,
                hidden = (o["hidden"] as? JsonPrimitive)?.booleanOrNull ?: false,
                deleted = (o["deleted"] as? JsonPrimitive)?.booleanOrNull ?: false,
                updated = (o["updated"] as? JsonPrimitive)?.doubleOrNull ?: 0.0,
            )
        }

        fun sectionJson(s: Section) = buildJsonObject {
            put("id", s.id)
            put("name", s.name)
            put("icon", s.icon)
            put("accent", s.accent)
            put("brief", s.brief)
            put("position", s.position)
            put("hidden", s.hidden)
            put("deleted", s.deleted)
            put("updated", s.updated)
        }

        fun message(section: String, element: JsonElement): Message? {
            val o = element as? JsonObject ?: return null
            val meta = o["meta"]?.takeIf { it !is JsonNull }
            return Message(
                localId = "r" + (o.long("id") ?: return null),
                remoteId = o.long("id"),
                section = section,
                role = o.str("role") ?: "assistant",
                text = o.str("text") ?: "",
                ts = (o["ts"] as? JsonPrimitive)?.doubleOrNull ?: 0.0,
                state = MessageState.SENT,
                meta = meta?.toString(),
            )
        }

        fun clientId(message: Message): String? = message.meta?.let {
            runCatching { Json.parseToJsonElement(it).jsonObject.str("client_id") }.getOrNull()
        }
    }
}

internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
internal fun JsonObject.long(key: String): Long? = (this[key] as? JsonPrimitive)?.longOrNull
