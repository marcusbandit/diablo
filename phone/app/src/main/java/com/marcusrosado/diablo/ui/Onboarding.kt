package com.marcusrosado.diablo.ui

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.Probe
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import com.marcusrosado.diablo.render.Objects

private const val PAGES = 5

/**
 * First launch: what the app is, then each setup step in turn. Every step can be skipped;
 * the settings screen holds the same controls, and onboarding can be replayed from there.
 */
@Composable
fun Onboarding(model: DiabloModel) {
    var page by rememberSaveable { mutableIntStateOf(0) }
    val linked by model.linked.collectAsStateWithLifecycle()
    LaunchedEffect(linked) { if (linked) { page = 2; model.dismissLink() } }
    BackHandler(enabled = page > 0) { page-- }

    Box(Modifier.fillMaxSize().background(DiabloColors.bg), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier
                .widthIn(max = 560.dp)
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .imePadding()
                .padding(24.dp),
        ) {
            Steps(page)
            Spacer(Modifier.height(28.dp))
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                when (page) {
                    0 -> Sections(model)
                    1 -> Tailscale(model)
                    2 -> PairStep(model)
                    3 -> NotifyStep(model)
                    else -> Ready()
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (page < PAGES - 1) {
                    LineButton("Continue", color = DiabloColors.accentInk) { page++ }
                    if (page > 0) LineButton("Skip for now") { page++ }
                } else {
                    LineButton("Open Diablo", color = DiabloColors.accentInk) { model.finishOnboarding() }
                }
            }
        }
    }
}

@Composable
private fun Steps(page: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(PAGES) { i ->
            Box(Modifier.width(if (i == page) 28.dp else 12.dp).height(4.dp).clip(androidx.compose.foundation.shape.RoundedCornerShape(50)).background(if (i <= page) DiabloColors.accent else DiabloColors.line))
        }
        Spacer(Modifier.width(12.dp))
        Text("Step ${page + 1} of $PAGES", style = LabelStyle)
    }
}

@Composable
private fun Sections(model: DiabloModel) {
    val sections by model.sections.collectAsStateWithLifecycle()
    Text("One Diablo, many sections", style = BigTitleStyle)
    Text(
        "Diablo is one brain with one memory: Telegram, the voice in the room and every section here feed it. " +
            "A section only gives him a focus, so \"log 20 push-ups\" in Workout is a workout, and he still knows everything else.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Column(Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(16.dp)).background(DiabloColors.raised).padding(vertical = 6.dp)) {
        sections.forEach { s ->
            Row(Modifier.padding(horizontal = 14.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
                SectionMark(s)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(s.name, style = TitleStyle)
                    Text(if (s.id == "diablo") "the whole of him" else s.brief, style = LabelStyle, maxLines = 1)
                }
            }
        }
    }
    Text("Add, remove, rename, recolour and sort them any time in Settings, the gear at the top. No new app needed.", style = LabelStyle.copy(color = DiabloColors.muted))
    Spacer(Modifier.height(6.dp))
    Text("You talk, he shows", style = TitleStyle)
    Text(
        "Each section shows what he knows right now, like your push-up ring and muscle groups in Workout, or the lamps in Home. " +
            "Tap a card to switch a lamp or log a set. The message bar at the bottom opens the conversation.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Objects.Render(SAMPLE)
}

private val SAMPLE = Json.parseToJsonElement(
    """{"kind":"goal","title":"Push-ups today","icon":"dumbbell","value":40,"max":75,"caption":"On pace, 35 to go","badges":[{"icon":"flame","label":"5 day streak"},{"icon":"calendar","label":"Day 8 of 30"}]}""",
).jsonObject

private fun vpnUp(context: Context): Boolean {
    val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
    return cm.allNetworks.any { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true }
}

@Composable
private fun Tailscale(model: DiabloModel) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var vpn by remember { mutableStateOf(vpnUp(context)) }
    var probe by remember { mutableStateOf<Probe?>(null) }
    var checking by remember { mutableStateOf(false) }
    val hosts by model.hosts.collectAsStateWithLifecycle()

    fun check() {
        checking = true
        vpn = vpnUp(context)
        scope.launch {
            probe = model.probe("")
            checking = false
        }
    }
    LaunchedEffect(Unit) { check() }

    Text("Reach him over Tailscale", style = BigTitleStyle)
    Text(
        "Diablo's hub runs on diablo-pi at home. Your phone reaches it through Tailscale, from anywhere, " +
            "without opening anything to the internet. Nothing leaves your tailnet.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    val reached = probe?.ok == true
    if (!reached) Status(vpn, if (vpn) "Tailscale is on" else "Tailscale looks off on this phone")
    hosts.forEach { h -> Text("${h.name}  ${h.url}", style = LabelStyle) }
    probe?.let { Status(it.ok, it.text) }
    if (checking) Text("Checking...", style = LabelStyle)
    if (reached) {
        Text("This phone reaches the hub. Nothing to do here.", style = ReadingStyle.copy(color = DiabloColors.muted))
        return
    }
    Text(
        "1. Open the Tailscale app.\n2. Tap Connect and sign in with the account the Pi is on.\n3. Come back here and tap Check again.",
        style = ReadingStyle,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        LineButton("Open Tailscale") { openTailscale(context) }
        LineButton("Check again") { check() }
    }
}

private fun openTailscale(context: Context) {
    val launch = context.packageManager.getLaunchIntentForPackage("com.tailscale.ipn")
    val intent = launch ?: Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=com.tailscale.ipn"))
    runCatching { context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

@Composable
private fun Ready() {
    Text("Ready", style = BigTitleStyle)
    Text(
        "Everything you read here is also kept on the phone. With no host up, every section and its history " +
            "still opens, and what you write waits in a queue until a host answers.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Text("Replies come as text, tables, code, pictures, GIFs and moving drawings, the way Diablo sends them.", style = ReadingStyle.copy(color = DiabloColors.muted))
    Text("You can see this again from Settings, then Show onboarding again.", style = LabelStyle)
}

@Composable
private fun Status(ok: Boolean, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(if (ok) DiabloColors.ok else DiabloColors.warn))
        Spacer(Modifier.width(10.dp))
        Text(text, style = ReadingStyle)
    }
}
