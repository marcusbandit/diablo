package com.marcusrosado.diablo.render

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.iconFor
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * What a panel's blocks can do when tapped. The screen that shows a panel provides it; a
 * block shown inside a reply gets the no-op default and stays read-only.
 */
class PanelActions(
    val say: (text: String, send: Boolean) -> Unit = { _, _ -> },
    val act: (JsonObject) -> Unit = {},
    val open: (module: String) -> Unit = {},
)

val LocalPanelActions = staticCompositionLocalOf { PanelActions() }

@Composable
fun ProvidePanelActions(actions: PanelActions, content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalPanelActions provides actions, content = content)
}

internal fun registerPanelKinds(register: (String, @Composable (JsonObject) -> Unit) -> Unit) {
    register("goal") { Goal(it) }
    register("stats") { Stats(it) }
    register("progress") { Progress(it) }
    register("muscles") { Muscles(it) }
    register("bars") { Bars(it) }
    register("list") { Rows(it) }
    register("lamps") { Lamps(it) }
    register("actions") { Actions(it) }
    register("notice") { Notice(it) }
}

/** Series colours from the theme, so charts follow the wallpaper colours. */
@Composable
private fun seriesColors(): List<Color> {
    val c = MaterialTheme.colorScheme
    return listOf(c.primary, c.tertiary, c.secondary, c.onSurfaceVariant)
}

@Composable
private fun PanelCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp)) { content() }
    }
}

@Composable
private fun CardTitle(title: String?, icon: String?, caption: String? = null) {
    if (title.isNullOrBlank()) return
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (icon != null) {
            Icon(iconFor(icon), null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(10.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            if (!caption.isNullOrBlank()) Text(caption, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    Spacer(Modifier.height(14.dp))
}

@Composable
private fun Goal(obj: JsonObject) {
    val value = obj.num("value") ?: 0.0
    val max = (obj.num("max") ?: 1.0).coerceAtLeast(1.0)
    val share by animateFloatAsState(
        (value / max).toFloat().coerceIn(0f, 1f),
        spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessLow),
        label = "goal",
    )
    val done = value >= max
    val track = MaterialTheme.colorScheme.surfaceContainerHighest
    val fill = if (done) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.primary
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
    ) {
        Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(132.dp), contentAlignment = Alignment.Center) {
                Canvas(Modifier.size(132.dp).semantics { contentDescription = "${value.toInt()} of ${max.toInt()}" }) {
                    val stroke = 14.dp.toPx()
                    val inset = stroke / 2
                    val arc = Size(size.width - stroke, size.height - stroke)
                    drawArc(track, 0f, 360f, false, Offset(inset, inset), arc, style = Stroke(stroke, cap = StrokeCap.Round))
                    if (share > 0f) drawArc(fill, -90f, 360f * share, false, Offset(inset, inset), arc, style = Stroke(stroke, cap = StrokeCap.Round))
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(value.toInt().toString(), style = MaterialTheme.typography.displaySmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
                    Text("of ${max.toInt()}", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.75f))
                }
            }
            Spacer(Modifier.width(20.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(obj.str("title").orEmpty(), style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
                obj.str("caption")?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onPrimaryContainer) }
                for (badge in obj.list("badges").mapNotNull { it.asObj() }) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(iconFor(badge.str("icon")), null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                        Spacer(Modifier.width(6.dp))
                        Text(badge.str("label").orEmpty(), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
                    }
                }
            }
        }
    }
}

@Composable
private fun Stats(obj: JsonObject) {
    val actions = LocalPanelActions.current
    val items = obj.list("items").mapNotNull { it.asObj() }
    Column {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            for (item in items) {
                val open = item.str("open")
                val content: @Composable () -> Unit = {
                    Column(Modifier.padding(16.dp)) {
                        Icon(iconFor(item.str("icon")), null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
                        Spacer(Modifier.height(12.dp))
                        Row(verticalAlignment = Alignment.Bottom) {
                            Text(item.str("value").orEmpty(), style = if (items.size >= 3) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineMedium, maxLines = 1)
                            item.str("unit")?.takeIf { it.isNotBlank() }?.let {
                                Spacer(Modifier.width(4.dp))
                                Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, modifier = Modifier.padding(bottom = 4.dp), overflow = TextOverflow.Ellipsis)
                            }
                        }
                        Text(item.str("label").orEmpty(), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                val colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow)
                if (open != null) {
                    Card(onClick = { actions.open(open) }, modifier = Modifier.weight(1f), colors = colors) { content() }
                } else {
                    Card(modifier = Modifier.weight(1f), colors = colors) { content() }
                }
            }
        }
        obj.str("caption")?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 8.dp, start = 4.dp))
        }
    }
}

@Composable
private fun Progress(obj: JsonObject) {
    val value = obj.num("value") ?: 0.0
    val max = (obj.num("max") ?: 1.0).coerceAtLeast(1.0)
    PanelCard {
        CardTitle(obj.str("label"), obj.str("icon"), obj.str("caption"))
        Meter((value / max).toFloat(), MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(6.dp))
        Text("${value.toInt()} of ${max.toInt()}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Meter(share: Float, color: Color, secondary: Float = 0f, secondaryColor: Color = Color.Transparent, height: Int = 8) {
    val track = MaterialTheme.colorScheme.surfaceContainerHighest
    val animated by animateFloatAsState(share.coerceIn(0f, 1f), label = "meter")
    Canvas(Modifier.fillMaxWidth().height(height.dp).clip(RoundedCornerShape(50))) {
        val r = CornerRadius(size.height / 2, size.height / 2)
        drawRoundRect(track, cornerRadius = r)
        if (secondary > 0f) drawRoundRect(secondaryColor, size = Size(size.width * secondary.coerceIn(0f, 1f), size.height), cornerRadius = r)
        if (animated > 0f) drawRoundRect(color, size = Size((size.width * animated).coerceAtLeast(size.height), size.height), cornerRadius = r)
    }
}

@Composable
private fun Muscles(obj: JsonObject) {
    val work = MaterialTheme.colorScheme.primary
    val stretch = MaterialTheme.colorScheme.tertiary.copy(alpha = 0.55f)
    PanelCard {
        CardTitle(obj.str("title"), "fitness", obj.str("caption"))
        val groups = obj.list("groups").mapNotNull { it.asObj() }
        groups.forEachIndexed { g, group ->
            Text(group.str("name").orEmpty(), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.height(8.dp))
            for (item in group.list("items").mapNotNull { it.asObj() }) {
                val w = (item.num("work") ?: 0.0).toFloat()
                val s = (item.num("stretch") ?: 0.0).toFloat()
                Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        item.str("label").orEmpty(),
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (w > 0.05f) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.width(104.dp),
                        maxLines = 1,
                    )
                    Box(Modifier.weight(1f)) { Meter(w, work, secondary = s, secondaryColor = stretch) }
                    Text("${(w * 100).toInt()}%", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(44.dp).padding(start = 8.dp))
                }
            }
            if (g < groups.lastIndex) Spacer(Modifier.height(14.dp))
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Legend("Worked", work)
            Legend("Stretched", stretch)
        }
    }
}

@Composable
private fun Legend(name: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(10.dp)) { drawCircle(color) }
        Spacer(Modifier.width(6.dp))
        Text(name, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Bars(obj: JsonObject) {
    val labels = obj.list("labels").map { it.asText() }
    val palette = seriesColors()
    val series = obj.list("series").mapNotNull { it.asObj() }.mapIndexed { i, s ->
        Triple(s.str("name").orEmpty(), palette[i % palette.size], s.list("values").map { (it as? JsonPrimitive)?.content?.toDoubleOrNull() ?: 0.0 })
    }
    if (labels.isEmpty() || series.isEmpty()) return
    val stacked = obj.bool("stacked") ?: false
    val totals = labels.indices.map { i -> if (stacked) series.sumOf { it.third.getOrElse(i) { 0.0 } } else series.maxOf { it.third.getOrElse(i) { 0.0 } } }
    val top = (totals.maxOrNull() ?: 0.0).coerceAtLeast(1.0)
    val grid = MaterialTheme.colorScheme.outlineVariant
    PanelCard {
        CardTitle(obj.str("title"), obj.str("icon"), obj.str("caption"))
        Canvas(Modifier.fillMaxWidth().height(140.dp).semantics { contentDescription = obj.str("title").orEmpty() }) {
            val slot = size.width / labels.size
            val width = slot * 0.62f
            val radius = CornerRadius(width / 2.5f, width / 2.5f)
            for (step in 1..3) {
                val y = size.height * step / 4
                drawLine(grid.copy(alpha = 0.5f), Offset(0f, y), Offset(size.width, y), 1f)
            }
            for (i in labels.indices) {
                val x = i * slot + (slot - width) / 2
                if (!stacked) {
                    val each = width / series.size
                    series.forEachIndexed { j, part ->
                        val v = part.third.getOrElse(i) { 0.0 }
                        if (v <= 0) return@forEachIndexed
                        val h = (v / top * size.height).toFloat()
                        drawRoundRect(part.second, Offset(x + j * each, size.height - h), Size(each * 0.85f, h), CornerRadius(each / 3, each / 3))
                    }
                    continue
                }
                var floor = size.height
                for ((j, part) in series.withIndex()) {
                    val v = part.third.getOrElse(i) { 0.0 }
                    if (v <= 0) continue
                    val h = (v / top * size.height).toFloat()
                    val topPart = series.drop(j + 1).none { it.third.getOrElse(i) { 0.0 } > 0 }
                    if (topPart) {
                        drawRoundRect(part.second, Offset(x, floor - h), Size(width, h), radius)
                        if (h > radius.y) drawRect(part.second, Offset(x, floor - radius.y), Size(width, radius.y))
                    } else {
                        drawRect(part.second, Offset(x, floor - h), Size(width, h))
                    }
                    floor -= h
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        Row(Modifier.fillMaxWidth()) {
            labels.forEachIndexed { i, label ->
                val shown = labels.size <= 7 || i % 2 == (labels.size - 1) % 2
                Text(if (shown) label else "", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, modifier = Modifier.weight(1f), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            for ((name, color, values) in series) {
                if (values.all { it <= 0 }) continue
                Legend(name.replaceFirstChar { it.uppercase() }, color)
            }
        }
    }
}

@Composable
private fun Rows(obj: JsonObject) {
    val items = obj.list("items").mapNotNull { it.asObj() }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(top = 16.dp, bottom = 4.dp)) {
            Box(Modifier.padding(horizontal = 16.dp)) { CardTitle(obj.str("title"), obj.str("icon")) }
            items.forEachIndexed { i, item ->
                ListItem(
                    headlineContent = { Text(item.str("title").orEmpty(), maxLines = 2, overflow = TextOverflow.Ellipsis) },
                    supportingContent = item.str("detail")?.takeIf { it.isNotBlank() }?.let { { Text(it) } },
                    trailingContent = item.str("trailing")?.takeIf { it.isNotBlank() }?.let { { Text(it, style = MaterialTheme.typography.labelLarge) } },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                )
                if (i < items.lastIndex) HorizontalDivider(Modifier.padding(horizontal = 16.dp), color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
}

@Composable
private fun Lamps(obj: JsonObject) {
    val actions = LocalPanelActions.current
    val haptics = LocalHapticFeedback.current
    val module = obj.str("module") ?: "lights"
    val lamps = obj.list("lamps").mapNotNull { it.asObj() }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(vertical = 8.dp)) {
            lamps.forEachIndexed { i, lamp ->
                val id = lamp.str("id") ?: return@forEachIndexed
                val on = lamp.bool("on") ?: false
                val level = ((lamp.num("level") ?: 0.0) / 100.0).toFloat().coerceIn(0f, 1f)
                val raw = colorOf(lamp.str("color"), MaterialTheme.colorScheme.primary)
                val glow = if (raw.luminance() > 0.7f) MaterialTheme.colorScheme.primary else raw
                var dragged by remember(id, level, on) { mutableFloatStateOf(if (on) level else 0f) }
                ListItem(
                    leadingContent = {
                        Box(
                            Modifier.size(40.dp).clip(CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Canvas(Modifier.size(40.dp)) { drawCircle(if (on) glow.copy(alpha = 0.28f) else Color.Gray.copy(alpha = 0.15f)) }
                            Icon(iconFor("lamp"), null, tint = if (on) glow else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    },
                    headlineContent = { Text(lamp.str("name").orEmpty().replaceFirstChar { it.uppercase() }) },
                    supportingContent = { Text(if (dragged > 0f) "On, ${(dragged * 100).toInt()}%" else "Off") },
                    trailingContent = {
                        Switch(checked = on, onCheckedChange = {
                            haptics.performHapticFeedback(HapticFeedbackType.ToggleOn)
                            actions.act(lampAct(module, "toggle", id, null))
                        })
                    },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                )
                Slider(
                    value = dragged,
                    onValueChange = { dragged = snap(it) },
                    onValueChangeFinished = { actions.act(lampAct(module, "level", id, (dragged * 100).toInt())) },
                    modifier = Modifier.padding(start = 72.dp, end = 16.dp),
                )
                if (i < lamps.lastIndex) HorizontalDivider(Modifier.padding(horizontal = 16.dp), color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
}

private fun snap(share: Float): Float = when {
    share >= 0.96f -> 1f
    share <= 0.04f -> 0f
    else -> share
}

private fun lampAct(module: String, action: String, lamp: String, level: Int?) = buildJsonObject {
    put("module", module)
    put("action", action)
    put("args", buildJsonObject {
        put("lamp", lamp)
        if (level != null) put("level", level)
    })
}

internal fun colorOf(hex: String?, fallback: Color): Color = runCatching {
    val clean = hex?.removePrefix("#") ?: return fallback
    if (clean.length != 6) return fallback
    Color(0xFF000000 or clean.toLong(16))
}.getOrDefault(fallback)

@Composable
private fun Actions(obj: JsonObject) {
    val actions = LocalPanelActions.current
    val haptics = LocalHapticFeedback.current
    val items = obj.list("items").mapNotNull { it.asObj() }.filter { it.str("label") != null }
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(horizontal = 0.dp)) {
        items(items.size) { index ->
            val item = items[index]
            val say = item.str("say")
            val tap = item["act"] as? JsonObject
            val send = item.bool("send") ?: true
            AssistChip(
                onClick = {
                    haptics.performHapticFeedback(HapticFeedbackType.ContextClick)
                    when {
                        tap != null -> actions.act(tap)
                        say != null -> actions.say(say, send)
                    }
                },
                label = { Text(item.str("label").orEmpty()) },
                leadingIcon = { Icon(iconFor(item.str("icon")), null, Modifier.size(AssistChipDefaults.IconSize)) },
            )
        }
    }
}

@Composable
private fun Notice(obj: JsonObject) {
    PanelCard {
        Text(obj.str("text").orEmpty(), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
