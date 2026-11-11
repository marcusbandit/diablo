package com.marcusrosado.diablo.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.marcusrosado.diablo.R

/** The three sizes the rendering code was built on, kept in step with the Material type scale. */
object TypeScale {
    val small = 13.sp
    val normal = 16.sp
    val large = 22.sp
}

/**
 * Named colours for the rendering code, read from the Material colour scheme in use, so replies,
 * tables and code follow the wallpaper colours and light or dark like the rest of the app.
 */
object DiabloColors {
    var bg by mutableStateOf(Color(0xFF111318)); private set
    var raised by mutableStateOf(Color(0xFF1D2024)); private set
    var sunken by mutableStateOf(Color(0xFF282A2F)); private set
    var ink by mutableStateOf(Color(0xFFE2E2E9)); private set
    var muted by mutableStateOf(Color(0xFFC4C6D0)); private set
    var faint by mutableStateOf(Color(0xFF8E9099)); private set
    var line by mutableStateOf(Color(0xFF44474F)); private set
    var lineStrong by mutableStateOf(Color(0xFF8E9099)); private set
    var accent by mutableStateOf(Color(0xFFAAC7FF)); private set
    var accentInk by mutableStateOf(Color(0xFFAAC7FF)); private set
    var ok by mutableStateOf(Color(0xFF3FD6B8)); private set
    var warn by mutableStateOf(Color(0xFFFFB4AB)); private set
    val tCode = Color(0xFF8B5CF6)
    val tTable = Color(0xFF12A58A)
    val tLights = Color(0xFFFFD166)
    val tGit = Color(0xFF3FB96F)

    internal fun follow(scheme: ColorScheme, dark: Boolean) {
        bg = scheme.surface
        raised = scheme.surfaceContainer
        sunken = scheme.surfaceContainerHighest
        ink = scheme.onSurface
        muted = scheme.onSurfaceVariant
        faint = scheme.outline
        line = scheme.outlineVariant
        lineStrong = scheme.outline
        accent = scheme.primary
        accentInk = scheme.primary
        ok = if (dark) Color(0xFF3FD6B8) else Color(0xFF00786A)
        warn = scheme.error
    }
}

object DiabloFonts {
    val reading: FontFamily = FontFamily.Default
    val titles: FontFamily = FontFamily.Default
    val mono = FontFamily(
        Font(R.font.jetbrainsmono_regular, FontWeight.Normal),
        Font(R.font.jetbrainsmono_bold, FontWeight.Bold),
    )
}

private val FallbackDark = darkColorScheme(
    primary = Color(0xFFAAC7FF), onPrimary = Color(0xFF0A305F),
    primaryContainer = Color(0xFF284777), onPrimaryContainer = Color(0xFFD6E3FF),
    secondary = Color(0xFFBEC6DC), onSecondary = Color(0xFF283141),
    secondaryContainer = Color(0xFF3E4759), onSecondaryContainer = Color(0xFFDAE2F9),
    tertiary = Color(0xFFDDBCE0), onTertiary = Color(0xFF3F2844),
    tertiaryContainer = Color(0xFF573E5C), onTertiaryContainer = Color(0xFFFAD8FD),
    surface = Color(0xFF111318), onSurface = Color(0xFFE2E2E9), onSurfaceVariant = Color(0xFFC4C6D0),
    surfaceContainerLowest = Color(0xFF0C0E13), surfaceContainerLow = Color(0xFF191C20),
    surfaceContainer = Color(0xFF1D2024), surfaceContainerHigh = Color(0xFF282A2F),
    surfaceContainerHighest = Color(0xFF33353A), background = Color(0xFF111318),
    outline = Color(0xFF8E9099), outlineVariant = Color(0xFF44474F),
)

private val FallbackLight = lightColorScheme(
    primary = Color(0xFF415F91), onPrimary = Color.White,
    primaryContainer = Color(0xFFD6E3FF), onPrimaryContainer = Color(0xFF284777),
    secondary = Color(0xFF565F71), onSecondary = Color.White,
    secondaryContainer = Color(0xFFDAE2F9), onSecondaryContainer = Color(0xFF3E4759),
    tertiary = Color(0xFF705575), onTertiary = Color.White,
    tertiaryContainer = Color(0xFFFAD8FD), onTertiaryContainer = Color(0xFF573E5C),
    surface = Color(0xFFF9F9FF), onSurface = Color(0xFF191C20), onSurfaceVariant = Color(0xFF44474F),
    surfaceContainerLowest = Color.White, surfaceContainerLow = Color(0xFFF3F3FA),
    surfaceContainer = Color(0xFFEDEDF4), surfaceContainerHigh = Color(0xFFE7E8EE),
    surfaceContainerHighest = Color(0xFFE2E2E9), background = Color(0xFFF9F9FF),
    outline = Color(0xFF74777F), outlineVariant = Color(0xFFC4C6D0),
)

/** Material 3 with the wallpaper's colours on Android 12 and up, light or dark with the system. */
@Composable
fun DiabloTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> FallbackDark
        else -> FallbackLight
    }
    DiabloColors.follow(scheme, dark)
    SideEffect { DiabloColors.follow(scheme, dark) }
    MaterialTheme(colorScheme = scheme) {
        Surface(color = scheme.surface, contentColor = scheme.onSurface, content = content)
    }
}
