package com.marcusrosado.diablo.assist

import android.Manifest
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/** The app's side of the assistant: hand it the hub, and see and open what it needs. */
class DiabloAssistModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  override fun definition() = ModuleDefinition {
    Name("DiabloAssist")

    Function("configure") { hubs: List<String>, token: String? ->
      HubLink.save(context, hubs, token)
    }

    Function("status") {
      val roles = context.getSystemService(RoleManager::class.java)
      mapOf(
        "assistant" to (roles?.isRoleHeld(RoleManager.ROLE_ASSISTANT) == true),
        "overlay" to Settings.canDrawOverlays(context),
        "mic" to (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED),
      )
    }

    // The assistant role cannot be asked for with a dialog: Android keeps it in its own settings page.
    Function("openAssistantSettings") {
      open(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS), Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
    }

    Function("openOverlaySettings") {
      open(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + context.packageName)))
    }
  }

  private fun open(vararg intents: Intent) {
    val starter = appContext.currentActivity ?: context
    for (intent in intents) {
      if (starter === context) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      try {
        starter.startActivity(intent)
        return
      } catch (_: Exception) {
        // This phone has no such page: try the next.
      }
    }
  }
}
