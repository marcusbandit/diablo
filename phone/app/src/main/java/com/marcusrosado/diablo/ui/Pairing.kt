package com.marcusrosado.diablo.ui

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.data.PairCode

/** Onboarding's pairing step: what pairing buys, then how to get and use a code. */
@Composable
fun PairStep(model: DiabloModel) {
    val sections by model.sections.collectAsStateWithLifecycle()
    val device by model.device.collectAsStateWithLifecycle()

    Text("Let him know it's you", style = BigTitleStyle)
    Text(
        "Pair this phone once and it reads every section, talks to Diablo and keeps a copy of each thread " +
            "for when no host is up. The hub answers no one it has not paired.",
        style = ReadingStyle.copy(color = DiabloColors.muted),
    )
    Unlocks(sections.filter { !it.hidden }.take(3).map { it.name to (if (it.id == "diablo") "the same thread as Telegram" else it.brief) })
    Text(
        "It costs one code from Diablo. The code lasts ten minutes and works once, so it is useless to anyone " +
            "who sees it later. The phone gets its own key, kept encrypted by Android's keystore, and you can " +
            "lock it out from Telegram any time with /devices.",
        style = LabelStyle.copy(color = DiabloColors.muted),
    )
    val pairing by model.pairing.collectAsStateWithLifecycle()
    if (device != null && pairing.ok != false) {
        if (pairing.text != null) PairResult(model) else Status(true, "Paired as $device. Nothing to do here.")
        return
    }
    PairPanel(model)
}

/** Getting a code and handing it to the hub, shared by onboarding and settings. */
@Composable
fun PairPanel(model: DiabloModel) {
    val context = LocalContext.current
    val pairing by model.pairing.collectAsStateWithLifecycle()
    var code by remember { mutableStateOf("") }
    var scanProblem by remember { mutableStateOf<String?>(null) }

    Step("1", "Ask Diablo for a code. On Telegram send him /pair, or on the desktop open diablo-pi:8755/app and choose Pair a device.")
    LineButton("Open Telegram") { openTelegram(context) }
    Step("2", "Scan the QR code it shows, or type the eight letters.")
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
        LineButton("Scan QR code", color = DiabloColors.accentInk, enabled = !pairing.working) {
            scanProblem = null
            scan(context, onCode = { model.pairFrom(it) }, onProblem = { scanProblem = it })
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
        Field(code, { code = it.take(16) }, "ABCD-EFGH", Modifier.weight(1f), style = ReadingStyle.copy(fontFamily = DiabloFonts.mono, fontWeight = FontWeight.Medium))
        LineButton("Pair", color = DiabloColors.accentInk, enabled = PairCode.valid(PairCode.normalize(code)) && !pairing.working) {
            model.pairFrom(code)
        }
    }
    scanProblem?.let { Status(false, it) }
    PairResult(model)
}

@Composable
private fun PairResult(model: DiabloModel) {
    val pairing by model.pairing.collectAsStateWithLifecycle()
    pairing.unknownHost?.let { code ->
        Status(false, "This code points at ${code.host}, a hub this phone has not used. Only pair with a hub you run yourself.")
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            LineButton("Pair with it", color = DiabloColors.accentInk) { model.confirmHost(true) }
            LineButton("Cancel") { model.confirmHost(false) }
        }
        return
    }
    val text = pairing.text ?: return
    Status(pairing.ok ?: true, text, waiting = pairing.working)
}

@Composable
private fun Unlocks(rows: List<Pair<String, String>>) {
    Column(Modifier.fillMaxWidth().background(DiabloColors.raised).padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("ONCE PAIRED", style = LabelStyle)
        rows.forEach { (name, line) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(name, style = TitleStyle, modifier = Modifier.width(96.dp))
                Text(line, style = LabelStyle.copy(color = DiabloColors.muted), maxLines = 1)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Offline", style = TitleStyle, modifier = Modifier.width(96.dp))
            Text("every thread stays readable here", style = LabelStyle.copy(color = DiabloColors.muted), maxLines = 1)
        }
    }
}

@Composable
private fun Step(number: String, text: String) {
    Row {
        Text(number, style = TitleStyle.copy(color = DiabloColors.accentInk), modifier = Modifier.width(22.dp))
        Text(text, style = ReadingStyle)
    }
}

@Composable
private fun Status(ok: Boolean, text: String, waiting: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(if (waiting) DiabloColors.faint else if (ok) DiabloColors.ok else DiabloColors.warn))
        Spacer(Modifier.width(10.dp))
        Text(text, style = ReadingStyle)
    }
}

private fun scan(context: Context, onCode: (String) -> Unit, onProblem: (String) -> Unit) {
    val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
    GmsBarcodeScanning.getClient(context, options).startScan()
        .addOnSuccessListener { barcode -> barcode.rawValue?.let(onCode) ?: onProblem("That QR code holds no text.") }
        .addOnFailureListener { onProblem("The scanner did not open (${it.message}). Type the code instead.") }
}

private fun openTelegram(context: Context) {
    val launch = context.packageManager.getLaunchIntentForPackage("org.telegram.messenger")
        ?: Intent(Intent.ACTION_VIEW, android.net.Uri.parse("https://t.me/"))
    runCatching { context.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

/** Settings: which name this phone is paired under, pairing again, and unpairing. */
@Composable
fun PhoneSettings(model: DiabloModel) {
    val device by model.device.collectAsStateWithLifecycle()
    val link by model.link.collectAsStateWithLifecycle()
    var again by remember { mutableStateOf(false) }
    val refused = link.refused
    if (device != null && !refused && !again) {
        Status(true, "Paired as $device.")
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            LineButton("Pair again") { again = true }
            LineButton("Unpair this phone") { model.unpair() }
        }
        return
    }
    Status(false, if (refused) "The hub no longer knows this phone. Pair it again." else "This phone is not paired yet.")
    PairPanel(model)
}

/** A pairing that came in through a tapped link, shown over the app until it is dismissed. */
@Composable
fun LinkDialog(model: DiabloModel) {
    val linked by model.linked.collectAsStateWithLifecycle()
    val pairing by model.pairing.collectAsStateWithLifecycle()
    if (!linked) return
    Dialog(onDismissRequest = { model.dismissLink() }) {
        Column(
            Modifier.background(DiabloColors.bg).border(Dp.Hairline, DiabloColors.lineStrong).padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Pairing this phone", style = BigTitleStyle)
            PairResult(model)
            if (pairing.unknownHost == null) {
                LineButton(if (pairing.working) "Hide" else "Done", color = DiabloColors.accentInk) { model.dismissLink() }
            }
        }
    }
}
