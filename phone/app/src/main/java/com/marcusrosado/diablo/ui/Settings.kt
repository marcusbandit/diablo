package com.marcusrosado.diablo.ui

import androidx.activity.compose.BackHandler
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.marcusrosado.diablo.core.Host
import com.marcusrosado.diablo.core.Section
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.Markdown
import kotlinx.coroutines.launch

/** Sections, hosts and pairing: everything that changes without a new build. */
@Composable
fun Settings(model: DiabloModel, onBack: () -> Unit, onGallery: () -> Unit) {
    BackHandler(onBack = onBack)
    val sections by model.sections.collectAsStateWithLifecycle()
    val hosts by model.hosts.collectAsStateWithLifecycle()
    val link by model.link.collectAsStateWithLifecycle()
    var editing by remember { mutableStateOf<Section?>(null) }
    var addingHost by remember { mutableStateOf(false) }

    Box(Modifier.fillMaxSize().background(DiabloColors.bg), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier
                .widthIn(max = 680.dp)
                .fillMaxSize()
                .statusBarsPadding()
                .verticalScroll(rememberScrollState())
                .navigationBarsPadding()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                androidx.compose.material3.IconButton(onClick = onBack) {
                    androidx.compose.material3.Icon(androidx.compose.material.icons.Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
                }
                Spacer(Modifier.width(8.dp))
                Text("Settings", style = androidx.compose.material3.MaterialTheme.typography.headlineMedium)
            }

            Heading("Sections", "The places in the navigation bar. They share one memory; each one only gives Diablo a focus.")
            sections.forEachIndexed { index, s ->
                SectionRow(
                    s,
                    first = index == 0,
                    last = index == sections.lastIndex,
                    onUp = { model.move(s.id, -1) },
                    onDown = { model.move(s.id, 1) },
                    onEdit = { editing = s },
                )
            }
            LineButton("Add a section", color = DiabloColors.accentInk) {
                editing = Section("", "", "folder", Palette[(sections.size) % Palette.size], "", sections.size, false, false, 0.0)
            }

            Heading("Hosts", "Tried in this order over Tailscale. The first that answers is used.")
            LinkLine(link)
            hosts.forEach { h -> HostRow(h, onRemove = { model.removeHost(h.name) }) }
            LineButton("Add a host", color = DiabloColors.accentInk) { addingHost = true }

            Heading("This phone", "The hub only answers devices it has paired. This phone's key is kept encrypted by Android's keystore.")
            PhoneSettings(model)

            Heading("Notifications", "A notification when Diablo writes in any section while the app is in the background.")
            NotifySettings(model)

            Heading("About", null)
            Text(
                "Markdown through the ${if (Markdown.native) "Rust core" else "fallback parser (the Rust core did not load)"}.",
                style = ReadingStyle.copy(color = DiabloColors.muted),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LineButton("Show onboarding again") { model.replayOnboarding() }
                LineButton("Render gallery", onClick = onGallery)
            }
        }
    }

    editing?.let { s ->
        SectionEditor(
            section = s,
            isNew = s.id.isEmpty(),
            existing = sections.map { it.id },
            onSave = { model.saveSection(it).also { error -> if (error == null) editing = null } },
            onRemove = { model.removeSection(s.id); editing = null },
            onDismiss = { editing = null },
        )
    }
    if (addingHost) HostEditor(onSave = { model.saveHost(it).also { e -> if (e == null) addingHost = false } }, onDismiss = { addingHost = false })
}

@Composable
private fun Heading(title: String, detail: String?) {
    Column(Modifier.padding(top = 14.dp)) {
        Text(title, style = androidx.compose.material3.MaterialTheme.typography.titleSmall, color = androidx.compose.material3.MaterialTheme.colorScheme.primary)
        if (detail != null) Text(detail, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium, color = androidx.compose.material3.MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
    }
}

@Composable
private fun SectionRow(s: Section, first: Boolean, last: Boolean, onUp: () -> Unit, onDown: () -> Unit, onEdit: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(16.dp)).background(DiabloColors.raised).tap(onEdit).padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SectionMark(s, dim = s.hidden)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(s.name, style = TitleStyle.copy(color = if (s.hidden) DiabloColors.faint else DiabloColors.ink))
            Text(
                if (s.hidden) "hidden" else if (s.id == "diablo") "the whole of him" else s.brief.ifBlank { "no brief" },
                style = LabelStyle,
                maxLines = 1,
            )
        }
        Arrow("Up", enabled = !first, onClick = onUp)
        Arrow("Down", enabled = !last, onClick = onDown)
    }
}

@Composable
private fun Arrow(label: String, enabled: Boolean, onClick: () -> Unit) {
    val icon = when (label) {
        "Up" -> androidx.compose.material.icons.Icons.Outlined.KeyboardArrowUp
        "Down" -> androidx.compose.material.icons.Icons.Outlined.KeyboardArrowDown
        else -> androidx.compose.material.icons.Icons.Outlined.Delete
    }
    androidx.compose.material3.IconButton(onClick = onClick, enabled = enabled) {
        androidx.compose.material3.Icon(icon, contentDescription = label)
    }
}

@Composable
private fun HostRow(h: Host, onRemove: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(16.dp)).background(DiabloColors.raised).padding(start = 14.dp, top = 8.dp, bottom = 8.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(h.name, style = TitleStyle)
            val state = when {
                h.lastMs != null && h.failures == 0u -> "answered in ${h.lastMs} ms"
                h.failures > 0u -> "${h.failures} misses in a row"
                else -> "not tried yet"
            }
            Text("${h.url}, $state", style = LabelStyle)
        }
        Arrow("Remove", enabled = true, onClick = onRemove)
    }
}

@Composable
private fun SectionEditor(
    section: Section,
    isNew: Boolean,
    existing: List<String>,
    onSave: (Section) -> String?,
    onRemove: () -> Unit,
    onDismiss: () -> Unit,
) {
    var name by remember { mutableStateOf(section.name) }
    var brief by remember { mutableStateOf(section.brief) }
    var accent by remember { mutableStateOf(section.accent) }
    var icon by remember { mutableStateOf(section.icon) }
    var hidden by remember { mutableStateOf(section.hidden) }
    var error by remember { mutableStateOf<String?>(null) }
    val preview = section.copy(name = name.ifBlank { "?" }, accent = accent, icon = icon, hidden = false)

    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .clip(androidx.compose.foundation.shape.RoundedCornerShape(28.dp))
                .background(androidx.compose.material3.MaterialTheme.colorScheme.surfaceContainerHigh)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionMark(preview, size = 36.dp)
                Spacer(Modifier.width(12.dp))
                Text(if (isNew) "New section" else section.name, style = androidx.compose.material3.MaterialTheme.typography.headlineSmall)
            }
            Text("Name", style = LabelStyle)
            Field(name, { name = it; error = null }, "Workout, Projects, School...", Modifier.fillMaxWidth())
            if (section.id != "diablo") {
                Text("Focus", style = LabelStyle)
                Text("What Diablo should focus on here. He reads it before every answer in this section.", style = LabelStyle.copy(color = DiabloColors.muted))
                Field(brief, { brief = it }, "Track sets and reps, suggest the next session", Modifier.fillMaxWidth().heightIn(min = 96.dp), singleLine = false)
            }
            Text("Icon", style = LabelStyle)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SectionIcons.forEach { name ->
                    androidx.compose.material3.FilledTonalIconToggleButton(checked = name == icon, onCheckedChange = { icon = name }) {
                        androidx.compose.material3.Icon(iconFor(name), contentDescription = name)
                    }
                }
            }
            Text("Colour", style = LabelStyle)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Palette.forEach { c ->
                    Box(
                        Modifier
                            .size(36.dp)
                            .clip(androidx.compose.foundation.shape.CircleShape)
                            .background(accentOf(c))
                            .border(if (c == accent) 3.dp else 0.dp, androidx.compose.material3.MaterialTheme.colorScheme.onSurface, androidx.compose.foundation.shape.CircleShape)
                            .tap { accent = c },
                    )
                }
            }
            if (section.id != "diablo") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Hide from the navigation bar", style = ReadingStyle, modifier = Modifier.weight(1f))
                    Switch(hidden, { hidden = it })
                }
            }
            error?.let { Text(it, style = ReadingStyle.copy(color = DiabloColors.warn)) }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LineButton("Save", color = DiabloColors.accentInk, enabled = name.isNotBlank()) {
                    val id = if (isNew) uniqueId(name, existing) else section.id
                    error = onSave(section.copy(id = id, name = name.trim(), brief = brief.trim(), accent = accent, icon = icon, hidden = hidden))
                }
                LineButton("Cancel", onClick = onDismiss)
                if (!isNew && section.id != "diablo") LineButton("Remove", color = DiabloColors.warn, onClick = onRemove)
            }
        }
    }
}

private fun uniqueId(name: String, existing: List<String>): String {
    val base = name.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-').take(28).ifBlank { "section" }
    var id = base
    var n = 2
    while (id in existing) id = "$base-${n++}"
    return id
}

@Composable
private fun HostEditor(onSave: (Host) -> String?, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("http://") }
    var error by remember { mutableStateOf<String?>(null) }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.background(DiabloColors.bg).border(Dp.Hairline, DiabloColors.lineStrong).padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("New host", style = BigTitleStyle)
            Text("A machine on your tailnet that runs the hub. Use its Tailscale address and the hub port, for example http://100.64.0.1:8755.", style = ReadingStyle.copy(color = DiabloColors.muted, fontSize = TypeScale.small))
            Field(name, { name = it }, "Name, for example nemurenai", Modifier.fillMaxWidth())
            Field(url, { url = it }, "http://100.x.y.z:8755", Modifier.fillMaxWidth())
            error?.let { Text(it, style = ReadingStyle.copy(color = DiabloColors.warn)) }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LineButton("Add", color = DiabloColors.accentInk, enabled = name.isNotBlank() && url.length > 8) {
                    error = onSave(Host(name.trim(), url.trim().trimEnd('/'), 10, null, null, 0u))
                }
                LineButton("Cancel", onClick = onDismiss)
            }
        }
    }
}
