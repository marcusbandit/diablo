package com.marcusrosado.diablo.render

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale

private val CellStyle: TextStyle get() = TextStyle(
    fontFamily = DiabloFonts.reading,
    fontSize = TypeScale.small,
    lineHeight = TypeScale.small * 1.4f,
    color = DiabloColors.ink,
)

private class GridGeometry {
    var colWidths = IntArray(0)
    var rowHeights = IntArray(0)
}

@Composable
fun TableBlock(table: Block.Table, onLink: (String) -> Unit, modifier: Modifier = Modifier) {
    val cols = maxOf(table.header.size, table.rows.maxOfOrNull { it.size } ?: 0)
    if (cols == 0) return
    val geometry = remember { GridGeometry() }
    val scroll = rememberScrollState()
    Layout(
        content = {
            for (c in 0 until cols) Cell(table.header.getOrNull(c).orEmpty(), table.align.getOrNull(c), true, onLink)
            for (row in table.rows) for (c in 0 until cols) {
                Cell(row.getOrNull(c).orEmpty(), table.align.getOrNull(c), false, onLink)
            }
        },
        modifier = modifier
            .horizontalScroll(scroll)
            .drawBehind {
                val w = geometry.colWidths.sum().toFloat()
                val h = geometry.rowHeights.sum().toFloat()
                if (w == 0f) return@drawBehind
                val hairline = 1f
                drawRect(DiabloColors.raised, Offset.Zero, Size(w, geometry.rowHeights.firstOrNull()?.toFloat() ?: 0f))
                var y = 0f
                for (rh in geometry.rowHeights) {
                    drawRect(DiabloColors.line, Offset(0f, y), Size(w, hairline))
                    y += rh
                }
                drawRect(DiabloColors.line, Offset(0f, h - hairline), Size(w, hairline))
                val headerBottom = geometry.rowHeights.firstOrNull()?.toFloat() ?: 0f
                drawRect(DiabloColors.lineStrong, Offset(0f, headerBottom - hairline), Size(w, hairline))
                var x = 0f
                for (cw in geometry.colWidths) {
                    drawRect(DiabloColors.line, Offset(x, 0f), Size(hairline, h))
                    x += cw
                }
                drawRect(DiabloColors.line, Offset(w - hairline, 0f), Size(hairline, h))
            },
    ) { measurables, _ ->
        val maxCol = 260.dp.roundToPx()
        val rows = measurables.size / cols
        val colWidths = IntArray(cols) { c ->
            (0 until rows).maxOf { r ->
                measurables[r * cols + c].maxIntrinsicWidth(Constraints.Infinity)
            }.coerceAtMost(maxCol)
        }
        val placeables = measurables.mapIndexed { i, m ->
            val w = colWidths[i % cols]
            m.measure(Constraints(minWidth = w, maxWidth = w))
        }
        val rowHeights = IntArray(rows) { r -> (0 until cols).maxOf { c -> placeables[r * cols + c].height } }
        geometry.colWidths = colWidths
        geometry.rowHeights = rowHeights
        layout(colWidths.sum(), rowHeights.sum()) {
            var y = 0
            for (r in 0 until rows) {
                var x = 0
                for (c in 0 until cols) {
                    placeables[r * cols + c].placeRelative(x, y)
                    x += colWidths[c]
                }
                y += rowHeights[r]
            }
        }
    }
}

@Composable
private fun Cell(spans: List<Span>, align: Align?, header: Boolean, onLink: (String) -> Unit) {
    Text(
        remember(spans) { spans.toAnnotated(onLink) },
        style = CellStyle.copy(
            fontWeight = if (header) FontWeight.Bold else FontWeight.Normal,
            fontFamily = if (header) DiabloFonts.titles else DiabloFonts.reading,
            textAlign = when (align) {
                Align.Center -> TextAlign.Center
                Align.Right -> TextAlign.Right
                else -> TextAlign.Left
            },
        ),
        modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
    )
}
