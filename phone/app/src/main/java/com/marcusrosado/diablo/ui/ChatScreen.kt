package com.marcusrosado.diablo.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.marcusrosado.diablo.core.Message
import com.marcusrosado.diablo.core.MessageState
import com.marcusrosado.diablo.core.Section
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.Markdown
import com.marcusrosado.diablo.render.Objects
import com.marcusrosado.diablo.render.RichContent
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val clock = DateTimeFormatter.ofPattern("HH:mm")
private val dayFormat = DateTimeFormatter.ofPattern("EEEE d MMMM")

/** A section's conversation with Diablo: what was said, newest at the bottom, and the line to say more. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(model: DiabloModel, focus: Boolean, onBack: () -> Unit) {
    val sections by model.sections.collectAsStateWithLifecycle()
    val current by model.current.collectAsStateWithLifecycle()
    val section = sections.firstOrNull { it.id == current } ?: sections.firstOrNull() ?: return
    val messages by model.messages.collectAsStateWithLifecycle()
    val thinking by model.thinking.collectAsStateWithLifecycle()
    val busy = section.id in thinking
    val list = rememberLazyListState()
    val onLink = rememberLinkOpener()
    BackHandler(onBack = onBack)

    LaunchedEffect(section.id, messages.size, busy) {
        val last = messages.size + (if (busy) 1 else 0)
        if (last > 0) list.scrollToItem(last - 1)
    }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(section.name, style = MaterialTheme.typography.titleLarge)
                        if (busy) Text("Diablo is on it", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back") }
                },
            )
        },
        bottomBar = { Composer(model, section, focus) },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.TopCenter) {
            LazyColumn(
                state = list,
                modifier = Modifier.widthIn(max = 760.dp).fillMaxSize(),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                itemsIndexed(messages, key = { _, m -> m.localId }) { index, message ->
                    val previous = messages.getOrNull(index - 1)
                    Column {
                        if (previous == null || dayOf(previous) != dayOf(message)) DayChip(message)
                        Entry(message, onLink, onRetry = { model.retry(message.localId) })
                    }
                }
                if (busy) item(key = "thinking") {
                    LinearProgressIndicator(Modifier.fillMaxWidth(0.4f).padding(vertical = 8.dp))
                }
            }
        }
    }
}

private fun dayOf(m: Message) = Instant.ofEpochMilli((m.ts * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()

private fun timeOf(m: Message) = clock.format(Instant.ofEpochMilli((m.ts * 1000).toLong()).atZone(ZoneId.systemDefault()))

@Composable
private fun DayChip(message: Message) {
    val day = dayOf(message)
    val today = LocalDate.now()
    val text = when (day) {
        today -> "Today"
        today.minusDays(1) -> "Yesterday"
        else -> dayFormat.format(day)
    }
    Box(Modifier.fillMaxWidth().padding(vertical = 8.dp), contentAlignment = Alignment.Center) {
        Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
            Text(text, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
        }
    }
}

@Composable
private fun Entry(message: Message, onLink: (String) -> Unit, onRetry: () -> Unit) {
    val mine = message.role == "user"
    if (mine) {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
            Surface(
                shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 6.dp),
                color = MaterialTheme.colorScheme.primaryContainer,
                modifier = Modifier.widthIn(max = 320.dp),
            ) {
                Text(
                    message.text,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                )
            }
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                when (message.state) {
                    MessageState.PENDING -> {
                        Icon(Icons.Outlined.Schedule, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(4.dp))
                        Text("Waiting to send", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    MessageState.FAILED -> {
                        Icon(Icons.Outlined.ErrorOutline, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.error)
                        Spacer(Modifier.width(4.dp))
                        Text("Not sent. Tap to retry", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.tap(onRetry))
                    }
                    MessageState.SENT -> Text(timeOf(message), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    } else {
        Row(Modifier.fillMaxWidth()) {
            Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f), modifier = Modifier.size(32.dp)) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(iconFor("flame"), null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                }
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f).padding(top = 4.dp)) {
                val blocks = remember(message.text) { Markdown.parse(message.text) }
                RichContent(blocks, onLink = onLink)
                val objects = remember(message.meta) { objectsOf(message.meta) }
                objects.forEach {
                    Spacer(Modifier.height(10.dp))
                    Objects.Render(it)
                }
                Spacer(Modifier.height(4.dp))
                Text(timeOf(message), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun objectsOf(meta: String?): List<JsonObject> = runCatching {
    (Json.parseToJsonElement(meta ?: return emptyList()).jsonObject["objects"] as? JsonArray)
        ?.mapNotNull { it as? JsonObject }.orEmpty()
}.getOrDefault(emptyList())

@Composable
private fun Composer(model: DiabloModel, section: Section, focus: Boolean) {
    var text by rememberSaveable(section.id, stateSaver = TextFieldValue.Saver) { mutableStateOf(TextFieldValue("")) }
    val draft by model.draft.collectAsStateWithLifecycle()
    val focuser = remember { FocusRequester() }
    val haptics = LocalHapticFeedback.current
    LaunchedEffect(draft) {
        val wanted = draft ?: return@LaunchedEffect
        if (wanted.section != section.id) return@LaunchedEffect
        text = TextFieldValue(wanted.text, TextRange(wanted.text.length))
        model.draftTaken()
        runCatching { focuser.requestFocus() }
    }
    LaunchedEffect(focus) { if (focus) runCatching { focuser.requestFocus() } }
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Field(
                value = text,
                onChange = { text = it },
                placeholder = "Message Diablo",
                modifier = Modifier.weight(1f).focusRequester(focuser),
            )
            FilledIconButton(
                onClick = {
                    haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                    model.send(text.text)
                    text = TextFieldValue("")
                },
                enabled = text.text.isNotBlank(),
                modifier = Modifier.size(52.dp),
            ) {
                Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = "Send")
            }
        }
    }
}
