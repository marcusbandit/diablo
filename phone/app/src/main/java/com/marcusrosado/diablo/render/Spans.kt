package com.marcusrosado.diablo.render

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.fromHtml
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts

private val linkStyles: TextLinkStyles get() = TextLinkStyles(
    style = SpanStyle(color = DiabloColors.accentInk, textDecoration = TextDecoration.Underline),
)

private val codeMarker = Color(0xFF010203)

private val codeStyle: SpanStyle get() = SpanStyle(
    fontFamily = DiabloFonts.mono,
    background = DiabloColors.ink.copy(alpha = 0.09f),
)

fun List<Span>.toAnnotated(onLink: (String) -> Unit): AnnotatedString =
    if (any { it.html }) {
        val parsed = AnnotatedString.fromHtml(toHtml(), linkStyles) { link ->
            (link as? LinkAnnotation.Url)?.url?.let(onLink)
        }
        buildAnnotatedString {
            append(parsed.text)
            for (r in parsed.spanStyles) {
                val style = if (r.item.background == codeMarker) r.item.merge(codeStyle) else r.item
                addStyle(style, r.start, r.end)
            }
            for (r in parsed.paragraphStyles) addStyle(r.item, r.start, r.end)
            for (r in parsed.getLinkAnnotations(0, parsed.length)) addLink(r.item as LinkAnnotation.Url, r.start, r.end)
        }
    } else buildAnnotatedString {
        for (span in this@toAnnotated) {
            val style = span.style()
            val link = span.link
            if (link != null) {
                withLink(
                    LinkAnnotation.Url(link, linkStyles) { onLink(link) },
                ) { withStyle(style) { append(span.text) } }
            } else {
                withStyle(style) { append(span.text) }
            }
        }
    }

private fun Span.style(): SpanStyle {
    var style = if (code) codeStyle else SpanStyle()
    if (bold) style = style.merge(SpanStyle(fontWeight = FontWeight.Bold))
    if (italic) style = style.merge(SpanStyle(fontStyle = FontStyle.Italic))
    if (strike) style = style.merge(SpanStyle(textDecoration = TextDecoration.LineThrough))
    return style
}

fun List<Span>.toHtml(): String = buildString {
    for (span in this@toHtml) {
        if (span.html) {
            append(span.text)
            continue
        }
        var s = span.text.escapeHtml().replace("\n", "<br>")
        if (span.code) s = "<span style=\"background-color:#010203\">$s</span>"
        if (span.strike) s = "<s>$s</s>"
        if (span.italic) s = "<i>$s</i>"
        if (span.bold) s = "<b>$s</b>"
        span.link?.let { s = "<a href=\"${it.escapeHtml()}\">$s</a>" }
        append(s)
    }
}

fun String.escapeHtml(): String = buildString(length + 16) {
    for (c in this@escapeHtml) when (c) {
        '&' -> append("&amp;")
        '<' -> append("&lt;")
        '>' -> append("&gt;")
        '"' -> append("&quot;")
        else -> append(c)
    }
}

fun List<Span>.plainText(): String = joinToString("") { it.text }
