package com.marcusrosado.diablo.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.render.Gallery

/**
 * Root of the app: onboarding until it has run once, then the sections as destinations of the
 * navigation bar (a rail when the Fold is open), each with its conversation one tap away.
 */
@Composable
fun App(model: DiabloModel = viewModel()) {
    val onboarded by model.onboarded.collectAsStateWithLifecycle()
    val jumps by model.jumps.collectAsStateWithLifecycle()
    var screen by rememberSaveable { mutableStateOf("main") }
    var typing by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(jumps) {
        if (jumps > 0) {
            typing = false
            screen = "chat"
            model.jumpHandled()
        }
    }

    if (!onboarded) {
        Onboarding(model)
        return
    }
    LinkDialog(model)
    AnimatedContent(
        targetState = screen,
        transitionSpec = {
            val forward = targetState != "main"
            (slideInHorizontally { if (forward) it / 4 else -it / 4 } + fadeIn()) togetherWith
                (slideOutHorizontally { if (forward) -it / 4 else it / 4 } + fadeOut())
        },
        label = "screen",
    ) { shown ->
        when (shown) {
            "settings" -> Settings(model, onBack = { screen = "main" }, onGallery = { screen = "gallery" })
            "gallery" -> {
                BackHandler { screen = "settings" }
                Gallery()
            }
            "chat" -> ChatScreen(model, focus = typing, onBack = { screen = "main" })
            else -> Sections(model, onChat = { wantsKeyboard -> typing = wantsKeyboard; screen = "chat" }, onSettings = { screen = "settings" })
        }
    }
}

@Composable
private fun Sections(model: DiabloModel, onChat: (Boolean) -> Unit, onSettings: () -> Unit) {
    val sections by model.sections.collectAsStateWithLifecycle()
    val current by model.current.collectAsStateWithLifecycle()
    val visible = sections.filter { !it.hidden || it.id == current }
    val section = sections.firstOrNull { it.id == current } ?: sections.firstOrNull()
    val tabs = rememberSaveableStateHolder()
    NavigationSuiteScaffold(
        navigationSuiteItems = {
            visible.forEach { s ->
                item(
                    selected = s.id == section?.id,
                    onClick = { model.select(s.id) },
                    icon = { Icon(iconFor(s.icon), contentDescription = null) },
                    label = { Text(s.name, maxLines = 1) },
                )
            }
        },
    ) {
        if (section != null) {
            key(section.id) {
                tabs.SaveableStateProvider(section.id) { SectionScreen(model, section, onChat = onChat, onSettings = onSettings) }
            }
        }
    }
}
