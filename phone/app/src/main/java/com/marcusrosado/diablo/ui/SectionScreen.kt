package com.marcusrosado.diablo.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.staggeredgrid.LazyVerticalStaggeredGrid
import androidx.compose.foundation.lazy.staggeredgrid.StaggeredGridCells
import androidx.compose.foundation.lazy.staggeredgrid.StaggeredGridItemSpan
import androidx.compose.foundation.lazy.staggeredgrid.rememberLazyStaggeredGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberTopAppBarState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.marcusrosado.diablo.core.Message
import com.marcusrosado.diablo.core.Section
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.Fleet
import com.marcusrosado.diablo.data.Link
import com.marcusrosado.diablo.render.Objects
import com.marcusrosado.diablo.render.PanelActions
import com.marcusrosado.diablo.render.ProvidePanelActions
import com.marcusrosado.diablo.render.list
import com.marcusrosado.diablo.render.str
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

private val WIDE_KINDS = setOf("stats", "notice")

/**
 * One section: what Diablo knows for this focus, as cards from the hub's modules, with the
 * conversation behind a message bar at the bottom. Two columns of cards when the Fold is open.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SectionScreen(model: DiabloModel, section: Section, onChat: (typing: Boolean) -> Unit, onSettings: () -> Unit) {
    val panels by model.panels.collectAsStateWithLifecycle()
    val fleet by model.fleet.collectAsStateWithLifecycle()
    val messages by model.messages.collectAsStateWithLifecycle()
    val link by model.link.collectAsStateWithLifecycle()
    val notice by model.notice.collectAsStateWithLifecycle()
    val blocks = panels[section.id].orEmpty()
    val scroll = TopAppBarDefaults.exitUntilCollapsedScrollBehavior(rememberTopAppBarState())
    val grid = rememberLazyStaggeredGridState()
    val snack = remember { SnackbarHostState() }

    LaunchedEffect(notice) {
        val shown = notice ?: return@LaunchedEffect
        if (shown.section == section.id) {
            model.noticeShown(shown)
            snack.showSnackbar(shown.text)
        }
    }

    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            LargeTopAppBar(
                title = { Text(section.name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                actions = {
                    if (link.host == null && !link.checking) {
                        Icon(Icons.Outlined.CloudOff, contentDescription = "Offline", tint = MaterialTheme.colorScheme.error, modifier = Modifier.padding(end = 8.dp))
                    }
                    IconButton(onClick = onSettings) { Icon(Icons.Outlined.Settings, contentDescription = "Settings") }
                },
                scrollBehavior = scroll,
            )
        },
        bottomBar = { AskBar(section) { onChat(true) } },
        snackbarHost = { SnackbarHost(snack) },
    ) { padding ->
        ProvidePanelActions(
            PanelActions(
                say = { text, send ->
                    if (send) {
                        model.send(text)
                        onChat(false)
                    } else {
                        model.say(text, false)
                        onChat(true)
                    }
                },
                act = model::act,
                open = model::openModule,
            ),
        ) {
            LazyVerticalStaggeredGrid(
                state = grid,
                columns = StaggeredGridCells.Adaptive(340.dp),
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(
                    start = 16.dp, end = 16.dp,
                    top = padding.calculateTopPadding() + 4.dp,
                    bottom = padding.calculateBottomPadding() + 16.dp,
                ),
                verticalItemSpacing = 12.dp,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (link.host == null && !link.checking) {
                    item(span = StaggeredGridItemSpan.FullLine) { OfflineCard(link) }
                }
                if (fleet && section.id == "diablo") {
                    item(span = StaggeredGridItemSpan.FullLine) { FleetCard(model) }
                }
                val heroes = blocks.filter { it.str("kind") == "goal" }
                val chips = blocks.filter { it.str("kind") == "actions" }.flatMap { it.list("items") }
                val cards = blocks.filter { it.str("kind") != "goal" && it.str("kind") != "actions" }
                heroes.forEachIndexed { i, block ->
                    item(key = "hero$i", span = StaggeredGridItemSpan.FullLine) { Objects.Render(block) }
                }
                if (chips.isNotEmpty()) {
                    item(key = "chips", span = StaggeredGridItemSpan.FullLine) {
                        Objects.Render(JsonObject(mapOf("kind" to JsonPrimitive("actions"), "items" to JsonArray(chips))))
                    }
                }
                cards.forEachIndexed { i, block ->
                    val wide = block.str("kind") in WIDE_KINDS
                    item(key = "b$i-${block.str("kind")}", span = if (wide) StaggeredGridItemSpan.FullLine else StaggeredGridItemSpan.SingleLane) {
                        Objects.Render(block)
                    }
                }
                if (messages.isNotEmpty()) {
                    item(key = "conversation", span = StaggeredGridItemSpan.FullLine) {
                        ConversationCard(messages.takeLast(2)) { onChat(false) }
                    }
                } else if (blocks.isEmpty()) {
                    item(span = StaggeredGridItemSpan.FullLine) { EmptyCard(section) }
                }
            }
        }
    }
}

/** The way into the conversation: looks like the field it opens. */
@Composable
private fun AskBar(section: Section, onOpen: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Surface(
            onClick = onOpen,
            shape = RoundedCornerShape(28.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier
                .windowInsetsPadding(WindowInsets.navigationBars.only(WindowInsetsSides.Bottom))
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 10.dp),
        ) {
            Row(Modifier.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Outlined.Chat, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(14.dp))
                Text(
                    if (section.id == "diablo") "Message Diablo" else "Message Diablo about ${section.name.lowercase()}",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun ConversationCard(last: List<Message>, onOpen: () -> Unit) {
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Outlined.Chat, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(10.dp))
                Text("Latest", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
            }
            last.forEach { m ->
                Row {
                    Text(
                        if (m.role == "user") "You" else "Diablo",
                        style = MaterialTheme.typography.labelLarge,
                        color = if (m.role == "user") MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary,
                        modifier = Modifier.width(56.dp),
                    )
                    Text(m.text, style = MaterialTheme.typography.bodyMedium, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun EmptyCard(section: Section) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionMark(section, size = 48.dp)
            Text("Nothing here yet", style = MaterialTheme.typography.titleLarge)
            Text(
                if (section.id == "diablo") "Everything you say here, on Telegram or out loud reaches the same Diablo."
                else "Tell Diablo anything about ${section.name.lowercase()}. He keeps it with everything else he knows.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun OfflineCard(link: Link) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
    ) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.CloudOff, null, tint = MaterialTheme.colorScheme.onErrorContainer)
            Spacer(Modifier.width(14.dp))
            Column {
                Text("Offline", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onErrorContainer)
                Text(
                    "Showing what this phone kept." + if (link.queued > 0) " ${link.queued} waiting to send." else "",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
        }
    }
}

@Composable
private fun FleetCard(model: DiabloModel) {
    val context = LocalContext.current
    LifecycleResumeEffect(Unit) {
        model.fleetCheck()
        onPauseOrDispose { }
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Remove Diablo Fleet?", style = MaterialTheme.typography.titleMedium)
            Text(
                "The old app spoke to the fleet viewer, which is gone. This app takes its place.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.align(Alignment.End)) {
                TextButton(onClick = { model.dismissFleet() }) { Text("Keep it") }
                FilledTonalButton(onClick = { Fleet.uninstall(context) }) { Text("Remove") }
            }
        }
    }
}

/** The link to the hub in one line, for settings. */
@Composable
fun LinkLine(link: Link, modifier: Modifier = Modifier) {
    val (ok, text) = when {
        link.checking -> null to "Looking for a host"
        link.host != null && link.problem == null -> true to "Connected to ${link.host}${link.ms?.let { ", $it ms" } ?: ""}"
        link.host != null -> false to "${link.host}: ${link.problem}"
        else -> false to "Offline, reading from this phone"
    }
    Text(
        text + if (link.queued > 0) ", ${link.queued} queued" else "",
        style = MaterialTheme.typography.bodyMedium,
        color = when (ok) {
            true -> MaterialTheme.colorScheme.onSurfaceVariant
            false -> MaterialTheme.colorScheme.error
            null -> MaterialTheme.colorScheme.onSurfaceVariant
        },
        modifier = modifier,
    )
}
