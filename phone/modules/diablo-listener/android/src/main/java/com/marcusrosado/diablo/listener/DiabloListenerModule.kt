package com.marcusrosado.diablo.listener

import android.content.Context
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * The JavaScript side of the background listener: it hands over the hubs, the sections, the
 * token and the switch, says which section is on screen, and posts the test notification.
 */
class DiabloListenerModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  override fun definition() = ModuleDefinition {
    Name("DiabloListener")

    Function("configure") { config: Map<String, Any?> ->
      val tokenChanged = config.containsKey("token") && (config["token"] as? String) != Prefs.token(context)
      Prefs.configure(context, config)
      Notifier(context).channels(Prefs.sections(context))
      if (Prefs.enabled(context) == false) Notifier(context).cancelAll()
      ListenerService.update(context, reconnect = tokenChanged)
      state()
    }

    Function("state") { state() }

    Function("setPresence") { foreground: Boolean, section: String? ->
      Presence.foreground = foreground
      Presence.section = section
      if (foreground && section != null) Notifier(context).cancel(section)
    }

    Function("cancel") { section: String -> Notifier(context).cancel(section) }

    Function("test") { section: String, text: String ->
      val n = Notifier(context)
      n.channels(Prefs.sections(context))
      val info = Prefs.sections(context).firstOrNull { it.id == section }
        ?: SectionInfo(section, "Diablo", "#ff4d2e", "")
      n.post(info, listOf(Note(0, text, System.currentTimeMillis() / 1000.0)), Notifier.TEST_TAG)
    }

    OnActivityEntersForeground { Presence.foreground = true }

    OnActivityEntersBackground { Presence.foreground = false }
  }

  private fun state(): Map<String, Any?> = mapOf(
    "enabled" to Prefs.enabled(context),
    "allowed" to Notifier(context).allowed(),
    "paired" to (Prefs.token(context) != null),
    "running" to Prefs.wanted(context),
  )
}
