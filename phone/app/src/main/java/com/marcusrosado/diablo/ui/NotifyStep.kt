package com.marcusrosado.diablo.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings as SystemSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.marcusrosado.diablo.core.Section
import com.marcusrosado.diablo.data.DiabloModel

private val Samples = mapOf(
    "diablo" to "The T3 handoff finished: the pairing fix is merged and the build is green.",
    "workout" to "Logged 5 sets of squats at 100 kg. Thursday: deadlifts, add 2.5 kg.",
    "projects" to "Wake report: nemurenai rebooted at 04:12, every service came back.",
)

private fun sample(section: Section) = Samples[section.id] ?: "Done with what you asked in ${section.name}. The details are in the thread."

/** The onboarding step for notifications: what one looks like first, then the ask. */
@Composable
fun NotifyStep(model: DiabloModel) {
    val sections by model.sections.collectAsStateWithLifecycle()
    Text("Hear back while you're away", style = BigTitleStyle)
    Text(
        "Long work finishes later: a T3 handoff, a wake report, a thread he is watching. " +
            "With notifications on, his answer reaches you even when the app is closed.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Previews(model, sections)
    Text(
        "Each section is its own notification channel in its own colour, so you can mute one and keep another loud. " +
            "Tap a notification and that section opens. Your own messages and the section you are reading never ring.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Text(
        "To hear him the phone keeps one quiet link to the hub open over Tailscale, and Android shows a small " +
            "Listening notice at the bottom of the shade while it does. No Google push: nothing leaves your tailnet.",
        style = LabelStyle.copy(color = DiabloColors.muted),
    )
    NotifyControls(model, onboarding = true)
}

/** The same switch in settings, with a quiet pointer when it is off. */
@Composable
fun NotifySettings(model: DiabloModel) {
    NotifyControls(model, onboarding = false)
}

@Composable
private fun NotifyControls(model: DiabloModel, onboarding: Boolean) {
    val context = LocalContext.current
    val setting by model.notify.collectAsStateWithLifecycle()
    var allowed by remember { mutableStateOf(model.notificationsAllowed()) }
    var asked by remember { mutableStateOf(false) }
    var tested by remember { mutableStateOf(false) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        allowed = model.notificationsAllowed()
        if (allowed && asked && setting != "1") model.setNotifications(true)
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        asked = true
        allowed = granted
        if (granted) model.setNotifications(true)
    }
    fun turnOn() {
        if (allowed || Build.VERSION.SDK_INT < 33) {
            allowed = model.notificationsAllowed()
            if (allowed) model.setNotifications(true) else asked = true
        } else {
            ask.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    val on = setting == "1" && allowed

    when {
        on -> {
            NotifyStatus(true, if (onboarding) "Notifications are on." else "On. Each section has its own channel.")
            if (tested) Text("Sent. Pull down the shade: that is how his replies arrive.", style = ReadingStyle.copy(color = DiabloColors.muted))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LineButton("Send a test") { model.testNotification(); tested = true }
                if (!onboarding) {
                    LineButton("Channels") { openNotificationSettings(context) }
                    LineButton("Turn off") { model.setNotifications(false) }
                }
            }
        }
        asked && !allowed -> {
            NotifyStatus(false, "Android is blocking notifications for Diablo.")
            Text(
                "1. Tap Open settings.\n2. Switch on All Diablo notifications.\n3. Come back here; it turns on by itself.",
                style = ReadingStyle,
            )
            LineButton("Open settings") { openNotificationSettings(context) }
        }
        else -> {
            if (!onboarding) {
                Text(
                    "Off. Turn them on to hear when Diablo reports back while the app is closed.",
                    style = ReadingStyle.copy(color = DiabloColors.muted),
                )
            }
            LineButton("Turn on notifications", color = DiabloColors.accentInk) { turnOn() }
        }
    }
}

/** What his notifications look like, from his real sections and his real last replies. */
@Composable
private fun Previews(model: DiabloModel, sections: List<Section>) {
    val picks = remember(sections) {
        val others = sections.filter { it.id != "diablo" && !it.hidden }
        listOfNotNull(
            others.firstOrNull { model.lastReply(it.id) != null } ?: others.firstOrNull(),
            sections.firstOrNull { it.id == "diablo" },
        ).take(2).map { it to (model.lastReply(it.id) ?: sample(it)) }
    }
    Column(Modifier.fillMaxWidth().background(DiabloColors.sunken).padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        picks.forEachIndexed { i, (s, text) ->
            PreviewCard(s, text, if (i == 0) "now" else "12 min")
        }
    }
}

@Composable
private fun PreviewCard(section: Section, text: String, time: String) {
    val accent = accentOf(section.accent)
    Row(Modifier.fillMaxWidth().background(DiabloColors.raised).padding(12.dp)) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).background(accent))
                Spacer(Modifier.width(8.dp))
                Text("Diablo  ·  ${section.name}  ·  $time", style = LabelStyle.copy(color = accent))
            }
            Spacer(Modifier.size(6.dp))
            Text(section.name, style = TitleStyle)
            Text(text, style = ReadingStyle.copy(color = DiabloColors.muted, fontSize = TypeScale.small), maxLines = 2)
        }
        Spacer(Modifier.width(12.dp))
        SectionMark(section, size = 36.dp)
    }
}

@Composable
private fun NotifyStatus(ok: Boolean, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(if (ok) DiabloColors.ok else DiabloColors.warn))
        Spacer(Modifier.width(10.dp))
        Text(text, style = ReadingStyle)
    }
}

private fun openNotificationSettings(context: Context) {
    val intent = Intent(SystemSettings.ACTION_APP_NOTIFICATION_SETTINGS)
        .putExtra(SystemSettings.EXTRA_APP_PACKAGE, context.packageName)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}
