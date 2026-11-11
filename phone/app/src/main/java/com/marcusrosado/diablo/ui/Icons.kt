package com.marcusrosado.diablo.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Alarm
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Bedtime
import androidx.compose.material.icons.outlined.Brightness5
import androidx.compose.material.icons.outlined.BrightnessHigh
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Chat
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Code
import androidx.compose.material.icons.outlined.DirectionsRun
import androidx.compose.material.icons.outlined.EmojiEvents
import androidx.compose.material.icons.outlined.FitnessCenter
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.LocalFireDepartment
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Restaurant
import androidx.compose.material.icons.outlined.School
import androidx.compose.material.icons.outlined.SelfImprovement
import androidx.compose.material.icons.outlined.ShoppingCart
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material.icons.outlined.Timer
import androidx.compose.material.icons.outlined.TipsAndUpdates
import androidx.compose.material.icons.outlined.TrendingUp
import androidx.compose.material.icons.outlined.Weekend
import androidx.compose.material.icons.outlined.WbIncandescent
import androidx.compose.material.icons.outlined.Work
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.LightMode
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material.icons.outlined.QuestionAnswer
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.ui.graphics.vector.ImageVector

/**
 * Icons by name, the names the hub and the section list use. An unknown name gets a neutral
 * mark, so a hub that knows a newer name never breaks an older app.
 */
private val byName: Map<String, ImageVector> = mapOf(
    "flame" to Icons.Outlined.LocalFireDepartment,
    "fire" to Icons.Outlined.LocalFireDepartment,
    "dumbbell" to Icons.Outlined.FitnessCenter,
    "fitness" to Icons.Outlined.FitnessCenter,
    "run" to Icons.Outlined.DirectionsRun,
    "folder" to Icons.Outlined.Folder,
    "code" to Icons.Outlined.Code,
    "lightbulb" to Icons.Outlined.Lightbulb,
    "lamp" to Icons.Outlined.WbIncandescent,
    "home" to Icons.Outlined.Home,
    "alarm" to Icons.Outlined.Alarm,
    "reminder" to Icons.Outlined.NotificationsActive,
    "calendar" to Icons.Outlined.CalendarMonth,
    "chat" to Icons.Outlined.Chat,
    "ask" to Icons.Outlined.QuestionAnswer,
    "insights" to Icons.Outlined.Insights,
    "trend" to Icons.Outlined.TrendingUp,
    "trophy" to Icons.Outlined.EmojiEvents,
    "done" to Icons.Outlined.CheckCircle,
    "stretch" to Icons.Outlined.SelfImprovement,
    "timer" to Icons.Outlined.Timer,
    "clock" to Icons.Outlined.Schedule,
    "off" to Icons.Outlined.PowerSettingsNew,
    "dim" to Icons.Outlined.Bedtime,
    "bright" to Icons.Outlined.BrightnessHigh,
    "day" to Icons.Outlined.LightMode,
    "cosy" to Icons.Outlined.Weekend,
    "sun" to Icons.Outlined.Brightness5,
    "add" to Icons.Outlined.Add,
    "music" to Icons.Outlined.MusicNote,
    "food" to Icons.Outlined.Restaurant,
    "school" to Icons.Outlined.School,
    "shopping" to Icons.Outlined.ShoppingCart,
    "work" to Icons.Outlined.Work,
    "star" to Icons.Outlined.Star,
    "idea" to Icons.Outlined.TipsAndUpdates,
)

fun iconFor(name: String?): ImageVector = byName[name?.lowercase()] ?: Icons.Outlined.AutoAwesome

val SectionIcons: List<String> = listOf("flame", "dumbbell", "home", "lightbulb", "folder", "code", "calendar", "music", "food", "school", "work", "star")
