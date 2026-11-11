package com.marcusrosado.diablo.render

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale

private val BodyStyle: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.reading,
    fontSize = TypeScale.normal,
    lineHeight = TypeScale.normal * 1.5f,
    color = DiabloColors.ink,
)

internal val Hairline = Dp.Hairline

@Composable
fun RichContent(
    blocks: List<Block>,
    modifier: Modifier = Modifier,
    onLink: (String) -> Unit,
) {
    SelectionContainer {
        Column(modifier = modifier) {
            blocks.forEachIndexed { index, block ->
                val previous = blocks.getOrNull(index - 1)
                val gap = when {
                    previous == null -> 0.dp
                    block is Block.Item && previous is Block.Item -> 3.dp
                    block is Block.Heading -> 14.dp
                    else -> 10.dp
                }
                Box(Modifier.padding(top = gap)) { BlockView(block, onLink) }
            }
        }
    }
}

@Composable
private fun BlockView(block: Block, onLink: (String) -> Unit) {
    when (block) {
        is Block.Heading -> Quoted(block.quote) { HeadingView(block, onLink) }
        is Block.Paragraph -> Quoted(block.quote) {
            Text(remember(block) { block.spans.toAnnotated(onLink) }, style = BodyStyle)
        }
        is Block.Item -> Quoted(block.quote) { ItemView(block, onLink) }
        is Block.Code -> Quoted(block.quote) { CodeBlock(block.lang, block.text) }
        is Block.Table -> TableBlock(block, onLink)
        is Block.Html -> SandboxedWeb(block.html, onLink, key = block)
        is Block.Svg -> SvgBlock(block, onLink)
        is Block.Image -> ImageBlock(block)
        Block.Rule -> HorizontalDivider(
            Modifier.padding(vertical = 6.dp),
            thickness = Hairline,
            color = DiabloColors.lineStrong,
        )
    }
}

@Composable
private fun Quoted(depth: Int, content: @Composable () -> Unit) {
    if (depth <= 0) {
        content()
        return
    }
    val step = 12.dp
    Box(
        Modifier
            .fillMaxWidth()
            .drawBehind {
                for (i in 0 until depth) {
                    val x = i * step.toPx()
                    drawRect(
                        DiabloColors.accent.copy(alpha = 0.7f),
                        topLeft = Offset(x, 0f),
                        size = androidx.compose.ui.geometry.Size(2.dp.toPx(), size.height),
                    )
                }
            }
            .padding(start = step * depth),
    ) { content() }
}

@Composable
private fun HeadingView(block: Block.Heading, onLink: (String) -> Unit) {
    val (size, weight, color) = when (block.level) {
        1 -> Triple(TypeScale.large, FontWeight.Bold, DiabloColors.ink)
        2 -> Triple(TypeScale.large, FontWeight.Normal, DiabloColors.ink)
        3 -> Triple(TypeScale.normal, FontWeight.Bold, DiabloColors.ink)
        4 -> Triple(TypeScale.normal, FontWeight.Medium, DiabloColors.muted)
        else -> Triple(TypeScale.small, FontWeight.Bold, DiabloColors.muted)
    }
    Text(
        remember(block) { block.spans.toAnnotated(onLink) },
        style = TextStyle(
            fontFamily = DiabloFonts.titles,
            fontSize = size,
            lineHeight = size * 1.25f,
            fontWeight = weight,
            color = color,
        ),
        modifier = Modifier.padding(top = if (block.level <= 2) 6.dp else 2.dp),
    )
}

@Composable
private fun ItemView(item: Block.Item, onLink: (String) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 18.dp * item.depth),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(Modifier.width(24.dp).height(TypeScale.normal.value.dp * 1.5f).offset(y = (-3).dp), contentAlignment = Alignment.CenterEnd) {
            val checked = item.checked
            when {
                checked != null -> CheckGlyph(checked)
                item.ordered -> Text(
                    "${item.number}.",
                    style = BodyStyle.copy(color = DiabloColors.muted, fontFamily = DiabloFonts.mono, fontSize = TypeScale.small),
                )
                else -> Box(
                    Modifier
                        .size(if (item.depth == 0) 5.dp else 4.dp)
                        .then(
                            if (item.depth == 0) Modifier.drawBehind { drawRect(DiabloColors.muted) }
                            else Modifier.border(1.dp, DiabloColors.muted),
                        ),
                )
            }
        }
        Text(
            remember(item) { item.spans.toAnnotated(onLink) },
            style = BodyStyle,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun CheckGlyph(checked: Boolean) {
    Canvas(Modifier.size(15.dp)) {
        val w = 1.5.dp.toPx()
        if (checked) {
            drawRect(DiabloColors.accent)
            val p = Path().apply {
                moveTo(size.width * 0.22f, size.height * 0.52f)
                lineTo(size.width * 0.43f, size.height * 0.72f)
                lineTo(size.width * 0.78f, size.height * 0.30f)
            }
            drawPath(p, Color.White, style = Stroke(w, cap = StrokeCap.Square))
        } else {
            drawRect(DiabloColors.lineStrong, style = Stroke(w))
        }
    }
}
