package com.marcusrosado.diablo.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.marcusrosado.diablo.core.Section

fun accentOf(hex: String): Color = runCatching {
    Color(android.graphics.Color.parseColor(hex))
}.getOrDefault(DiabloColors.accent)

val Palette = listOf("#2d81ff", "#3fd6b8", "#f2a516", "#e5484d", "#8b5cf6", "#7bb51c", "#ffd166", "#f2f1ee")

val LabelStyle: TextStyle get() = TextStyle(
    fontSize = TypeScale.small,
    fontWeight = FontWeight.Medium,
    color = DiabloColors.muted,
)

val TitleStyle: TextStyle get() = TextStyle(
    fontSize = TypeScale.normal,
    fontWeight = FontWeight.Medium,
    color = DiabloColors.ink,
)

val BigTitleStyle: TextStyle get() = TextStyle(
    fontSize = 28.sp(),
    fontWeight = FontWeight.Normal,
    color = DiabloColors.ink,
    lineHeight = 36.sp(),
)

val ReadingStyle: TextStyle get() = TextStyle(
    fontSize = TypeScale.normal,
    color = DiabloColors.ink,
    lineHeight = TypeScale.normal * 1.5f,
)

private fun Int.sp() = androidx.compose.ui.unit.TextUnit(this.toFloat(), androidx.compose.ui.unit.TextUnitType.Sp)

/** A section's mark: its icon in a circle of its accent. */
@Composable
fun SectionMark(section: Section, size: Dp = 40.dp, dim: Boolean = false) {
    val accent = accentOf(section.accent)
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(accent.copy(alpha = if (dim) 0.12f else 0.22f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            iconFor(section.icon),
            contentDescription = null,
            tint = if (dim) accent.copy(alpha = 0.6f) else accent,
            modifier = Modifier.size(size * 0.55f),
        )
    }
}

/**
 * The app's one button. The colour picks its weight: the accent is the main action, a muted
 * colour is a quiet text button, anything else an outlined one.
 */
@Composable
fun LineButton(text: String, modifier: Modifier = Modifier, color: Color = DiabloColors.ink, enabled: Boolean = true, onClick: () -> Unit) {
    when (color) {
        DiabloColors.accent, DiabloColors.accentInk -> Button(onClick = onClick, enabled = enabled, modifier = modifier) { Text(text) }
        DiabloColors.muted, DiabloColors.faint -> TextButton(onClick = onClick, enabled = enabled, modifier = modifier) { Text(text) }
        DiabloColors.warn -> TextButton(
            onClick = onClick,
            enabled = enabled,
            modifier = modifier,
            colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error),
        ) { Text(text) }
        else -> OutlinedButton(onClick = onClick, enabled = enabled, modifier = modifier) { Text(text) }
    }
}

@Composable
fun Field(
    value: String,
    onChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    secret: Boolean = false,
    singleLine: Boolean = true,
    style: TextStyle = ReadingStyle,
) {
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = singleLine,
        textStyle = style,
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = fieldKeyboard(secret),
        modifier = modifier.fieldLook(),
        decorationBox = { inner -> FieldBox(value.isEmpty(), placeholder, style, inner) },
    )
}

/** Field over a TextFieldValue, for text that gets filled in from outside with the cursor at its end. */
@Composable
fun Field(
    value: TextFieldValue,
    onChange: (TextFieldValue) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    singleLine: Boolean = false,
    style: TextStyle = ReadingStyle,
) {
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = singleLine,
        textStyle = style,
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = fieldKeyboard(false),
        modifier = modifier.fieldLook(),
        decorationBox = { inner -> FieldBox(value.text.isEmpty(), placeholder, style, inner) },
    )
}

private fun fieldKeyboard(secret: Boolean) = if (secret) {
    KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false)
} else {
    KeyboardOptions(capitalization = KeyboardCapitalization.Sentences)
}

private fun Modifier.fieldLook() = clip(RoundedCornerShape(24.dp)).background(DiabloColors.sunken)

@Composable
private fun FieldBox(empty: Boolean, placeholder: String, style: TextStyle, inner: @Composable () -> Unit) {
    Box(Modifier.padding(horizontal = 18.dp, vertical = 14.dp)) {
        if (empty) Text(placeholder, style = style.copy(color = DiabloColors.muted))
        inner()
    }
}

@Composable
fun rememberLinkOpener(): (String) -> Unit {
    val context = LocalContext.current
    return { url ->
        if (url.startsWith("https://") || url.startsWith("http://")) {
            runCatching {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
    }
}

fun Modifier.tap(onClick: () -> Unit) = this.clickable(onClick = onClick)
