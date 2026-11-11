package com.marcusrosado.diablo.render

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale
import java.util.concurrent.ConcurrentHashMap
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull

/**
 * Renderers for Diablo's structured objects, keyed by the object's "kind" field.
 * A new kind is one call: Objects.register("name") { obj -> ... }.
 */
object Objects {
    private val renderers = ConcurrentHashMap<String, @Composable (JsonObject) -> Unit>()

    fun register(kind: String, renderer: @Composable (JsonObject) -> Unit) {
        renderers[kind] = renderer
    }

    @Composable
    fun Render(obj: JsonObject) {
        val kind = obj.str("kind") ?: "object"
        val renderer = renderers[kind]
        if (renderer != null) renderer(obj) else UnknownObject(kind, obj)
    }

    init {
        register("readout") { Readout(it) }
        register("code") { CodeObject(it) }
        register("table") { TableObject(it) }
        register("lights") { Lights(it) }
        register("git") { GitLog(it) }
        register("artifact") { Artifact(it) }
        register("question") { QuestionObject(it) }
        registerPanelKinds { kind, renderer -> register(kind, renderer) }
    }
}

internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
internal fun JsonObject.bool(key: String): Boolean? = (this[key] as? JsonPrimitive)?.booleanOrNull
internal fun JsonObject.num(key: String): Double? = (this[key] as? JsonPrimitive)?.doubleOrNull
internal fun JsonObject.list(key: String): List<JsonElement> = (this[key] as? JsonArray) ?: emptyList()
internal fun JsonElement.asObj(): JsonObject? = this as? JsonObject
internal fun JsonElement.asText(): String = (this as? JsonPrimitive)?.contentOrNull ?: toString()

private val LabelStyle: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.mono,
    fontSize = TypeScale.small,
    color = DiabloColors.faint,
)
private val TextBody: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.reading,
    fontSize = TypeScale.normal,
    lineHeight = TypeScale.normal * 1.4f,
    color = DiabloColors.ink,
)
private val Small: TextStyle get() = TextBody.copy(fontSize = TypeScale.small, lineHeight = TypeScale.small * 1.4f)
private val Title: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.titles,
    fontSize = TypeScale.normal,
    fontWeight = FontWeight.Bold,
    color = DiabloColors.ink,
)

@Composable
private fun Frame(
    label: String,
    accent: Color,
    title: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    androidx.compose.material3.Card(
        modifier = modifier.fillMaxWidth(),
        colors = androidx.compose.material3.CardDefaults.cardColors(containerColor = androidx.compose.material3.MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(4.dp).height(16.dp).background(accent, androidx.compose.foundation.shape.RoundedCornerShape(2.dp)))
                Text(title ?: label.replaceFirstChar { it.uppercase() }, style = androidx.compose.material3.MaterialTheme.typography.titleSmall)
            }
            content()
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Readout(obj: JsonObject) {
    Frame("readout", DiabloColors.accent, obj.str("title")) {
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            for (cell in obj.list("cells").mapNotNull { it.asObj() }) {
                Column {
                    Text(
                        cell["v"]?.asText().orEmpty(),
                        style = TextStyle(
                            fontFamily = DiabloFonts.titles,
                            fontSize = TypeScale.large,
                            fontWeight = FontWeight.Bold,
                            color = DiabloColors.ink,
                        ),
                    )
                    Text(cell.str("k").orEmpty(), style = LabelStyle.copy(color = DiabloColors.muted))
                }
            }
        }
    }
}

@Composable
private fun CodeObject(obj: JsonObject) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        CodeBlock(null, obj.str("code").orEmpty(), title = obj.str("title"))
    }
}

@Composable
private fun TableObject(obj: JsonObject) {
    val table = remember(obj) {
        fun row(e: JsonElement) = (e as? JsonArray).orEmpty().map { listOf(Span(it.asText())) }
        Block.Table(
            align = emptyList(),
            header = row(obj["head"] ?: JsonArray(emptyList())),
            rows = obj.list("rows").map(::row),
        )
    }
    Frame("table", DiabloColors.tTable, obj.str("title")) {
        TableBlock(table, onLink = {})
    }
}

@Composable
private fun Lights(obj: JsonObject) {
    Frame("lights", DiabloColors.tLights) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            for (lamp in obj.list("lamps").mapNotNull { it.asObj() }) {
                val on = lamp.bool("on") ?: false
                val bri = ((lamp.num("bri") ?: 0.0) / 254.0).toFloat().coerceIn(0f, 1f)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        lamp.str("name").orEmpty(),
                        style = Small.copy(color = if (on) DiabloColors.ink else DiabloColors.faint),
                        modifier = Modifier.width(110.dp),
                    )
                    Box(
                        Modifier
                            .weight(1f)
                            .height(8.dp)
                            .clip(androidx.compose.foundation.shape.RoundedCornerShape(50))
                            .background(DiabloColors.sunken)
                            .drawBehind {
                                if (on) drawRect(DiabloColors.accent, Offset.Zero, Size(size.width * bri, size.height))
                            },
                    )
                    Text(
                        if (on) "${(bri * 100).toInt()}%" else "off",
                        style = LabelStyle.copy(color = DiabloColors.muted),
                        modifier = Modifier.width(36.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun GitLog(obj: JsonObject) {
    Frame("git", DiabloColors.tGit, obj.str("title")) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            for (commit in obj.list("commits").mapNotNull { it.asObj() }) {
                val state = commit.str("rstate").orEmpty()
                val stateColor = when (state.lowercase()) {
                    "ok", "pass", "passed", "success", "merged", "done" -> DiabloColors.ok
                    "warn", "pending", "running", "queued" -> DiabloColors.warn
                    else -> DiabloColors.faint
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                    Text(
                        commit.str("kind").orEmpty(),
                        style = LabelStyle.copy(color = DiabloColors.accentInk),
                        modifier = Modifier.width(44.dp).padding(top = 2.dp),
                    )
                    Column(Modifier.weight(1f)) {
                        Text(commit.str("text").orEmpty(), style = Small)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            commit.str("rtime")?.let { Text(it, style = LabelStyle) }
                            if (state.isNotEmpty()) Text(state, style = LabelStyle.copy(color = stateColor))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Artifact(obj: JsonObject) {
    val shown = setOf("kind", "title", "version")
    Frame("artifact", DiabloColors.accentInk) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(obj.str("title") ?: "Untitled", style = Title.copy(fontSize = TypeScale.large), modifier = Modifier.alignByBaseline())
            obj.str("version")?.let {
                Text("v$it", style = LabelStyle.copy(color = DiabloColors.accentInk), modifier = Modifier.alignByBaseline())
            }
        }
        for ((key, value) in obj) {
            if (key in shown) continue
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(key, style = LabelStyle, modifier = Modifier.width(72.dp))
                Text(value.asText(), style = Small.copy(color = DiabloColors.muted), modifier = Modifier.weight(1f))
            }
        }
    }
}

private val prettyJson = Json { prettyPrint = true }

@Composable
private fun UnknownObject(kind: String, obj: JsonObject) {
    var open by remember { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxWidth()
            .background(DiabloColors.sunken)
            .border(Hairline, DiabloColors.line),
    ) {
        Text(
            (if (open) "- " else "+ ") + kind,
            style = LabelStyle,
            modifier = Modifier
                .fillMaxWidth()
                .clickable { open = !open }
                .padding(10.dp),
        )
        if (open) {
            Text(
                prettyJson.encodeToString(JsonElement.serializer(), obj),
                style = LabelStyle.copy(color = DiabloColors.muted),
                softWrap = false,
                modifier = Modifier.horizontalScroll(rememberScrollState()).padding(start = 10.dp, end = 10.dp, bottom = 10.dp),
            )
        }
    }
}


/** A question Diablo asked with choices. Replying with an option's number answers it. */
@Composable
private fun QuestionObject(obj: JsonObject) {
    val state = obj.str("state") ?: "pending"
    val recommended = obj.num("recommended")?.toInt() ?: -1
    val options = obj.list("options").mapNotNull { it.asObj() }.take(4)
    Frame("question", DiabloColors.accent, obj.str("header") ?: "Question") {
        Text(obj.str("question").orEmpty(), style = TextBody.copy(fontWeight = FontWeight.SemiBold))
        if (state == "pending") {
            options.forEachIndexed { index, option ->
                Column(
                    Modifier.fillMaxWidth().border(1.dp, DiabloColors.line, androidx.compose.foundation.shape.RoundedCornerShape(3.dp)).padding(10.dp),
                ) {
                    val mark = if (index == recommended) " (recommended)" else ""
                    Text("${index + 1}. ${option.str("label").orEmpty()}$mark", style = TextBody.copy(fontWeight = FontWeight.Bold))
                    option.str("description")?.takeIf { it.isNotBlank() }?.let { Text(it, style = Small.copy(color = DiabloColors.faint)) }
                }
            }
            val words = if (obj.bool("allow_text") == true) ", or in your own words." else "."
            Text("Reply with the number$words", style = LabelStyle)
        } else {
            val answer = obj["answer"]?.asObj()
            val said = when (state) {
                "answered" -> (answer?.list("labels")?.map { it.asText() }.orEmpty() + listOfNotNull(answer?.str("text")?.takeIf { it.isNotBlank() })).joinToString(", ")
                "expired" -> "No answer in time"
                "superseded" -> "Replaced by a newer question"
                else -> "Withdrawn"
            }
            Text(said, style = Small.copy(color = DiabloColors.muted))
        }
    }
}
