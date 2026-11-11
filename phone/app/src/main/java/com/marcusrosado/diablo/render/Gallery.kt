package com.marcusrosado.diablo.render

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject

@Composable
fun Gallery() {
    var lastLink by remember { mutableStateOf("none yet") }
    val onLink = { url: String -> lastLink = url }
    Box(
        Modifier
            .fillMaxSize()
            .background(DiabloColors.bg),
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(
            Modifier
                .widthIn(max = 640.dp)
                .fillMaxSize()
                .statusBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Text(
                "Render gallery",
                style = TextStyle(
                    fontFamily = DiabloFonts.titles,
                    fontSize = TypeScale.large,
                    fontWeight = FontWeight.Bold,
                    color = DiabloColors.ink,
                ),
            )
            Caption("last link: $lastLink")

            Section("markdown, every block") {
                RichContent(remember { MarkdownLite.parse(GalleryContent.markdown) }, onLink = onLink)
            }
            Section("table, aligned") {
                RichContent(remember { MarkdownLite.parse(GalleryContent.table) }, onLink = onLink)
            }
            Section("code block") {
                RichContent(remember { MarkdownLite.parse(GalleryContent.code) }, onLink = onLink)
            }
            Section("inline html paragraph") {
                RichContent(GalleryContent.inlineHtml, onLink = onLink)
            }
            Section("html block, sandboxed") {
                RichContent(listOf(Block.Html(GalleryContent.htmlCard)), onLink = onLink)
            }
            Section("svg, static, native") {
                RichContent(listOf(Block.Svg(GalleryContent.flame, animated = false)), onLink = onLink)
            }
            Section("svg, SMIL animation, sandboxed") {
                RichContent(listOf(Block.Svg(GalleryContent.smilRing, animated = true)), onLink = onLink)
            }
            Section("svg, CSS keyframes, sandboxed") {
                RichContent(listOf(Block.Svg(GalleryContent.cssSpinner, animated = true)), onLink = onLink)
            }
            Section("gif, data uri") {
                RichContent(listOf(Block.Image(SAMPLE_GIF, "Orbiting dots")), onLink = onLink)
            }
            Section("animated webp, data uri") {
                RichContent(listOf(Block.Image(SAMPLE_WEBP, "Orbiting dots")), onLink = onLink)
            }
            Section("gif, https") {
                RichContent(listOf(Block.Image(GalleryContent.remoteGif, "Rotating earth")), onLink = onLink)
            }
            Section("long html, scrolls inside") {
                RichContent(listOf(Block.Html(GalleryContent.longHtml)), onLink = onLink)
            }
            Section("diablo objects") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    for (obj in GalleryContent.objects) Objects.Render(Json.parseToJsonElement(obj).jsonObject)
                }
            }
        }
    }
}

@Composable
private fun Section(label: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Caption(label)
        Box(
            Modifier
                .fillMaxWidth()
                .border(androidx.compose.ui.unit.Dp.Hairline, DiabloColors.line)
                .padding(12.dp),
        ) { content() }
    }
}
