package com.marcusrosado.diablo.render

import android.content.ClipData
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val CodeStyle: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.mono,
    fontSize = TypeScale.small,
    lineHeight = TypeScale.small * 1.5f,
    color = DiabloColors.ink,
    fontFeatureSettings = "calt 0, liga 0",
)

private val LabelStyle: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.mono,
    fontSize = TypeScale.small,
    color = DiabloColors.faint,
)

@Composable
fun CodeBlock(lang: String?, text: String, modifier: Modifier = Modifier, title: String? = null) {
    val clipboard = LocalClipboard.current
    val scope = rememberCoroutineScope()
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) {
        if (copied) {
            delay(1500)
            copied = false
        }
    }
    Column(
        modifier
            .fillMaxWidth()
            .background(DiabloColors.sunken)
            .border(Hairline, DiabloColors.line),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .border(Hairline, DiabloColors.line)
                .padding(start = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(title ?: lang ?: "code", style = LabelStyle)
            Text(
                if (copied) "Copied" else "Copy",
                style = LabelStyle.copy(color = if (copied) DiabloColors.ok else DiabloColors.accentInk),
                modifier = Modifier
                    .clickable {
                        scope.launch {
                            clipboard.setClipEntry(ClipEntry(ClipData.newPlainText("code", text)))
                            copied = true
                        }
                    }
                    .padding(horizontal = 12.dp, vertical = 8.dp),
            )
        }
        Text(
            text.trimEnd('\n'),
            style = CodeStyle,
            softWrap = false,
            modifier = Modifier
                .horizontalScroll(rememberScrollState())
                .padding(10.dp),
        )
    }
}
