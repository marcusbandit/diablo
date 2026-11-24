package com.marcusrosado.diablo.assist

import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession

/** Android binds this while Diablo holds the assistant role. The bubble asks it to open a session again. */
class DiabloAssistService : VoiceInteractionService() {
  override fun onReady() {
    super.onReady()
    running = this
  }

  override fun onShutdown() {
    if (running === this) running = null
    super.onShutdown()
  }

  companion object {
    @Volatile var running: DiabloAssistService? = null
      private set

    /**
     * Opens a session again, reading the screen; `type` opens it with the typing bar, `listen`
     * makes him listen even over a reply he is still saying.
     */
    fun reopen(type: Boolean, listen: Boolean = false): Boolean {
      val service = running ?: return false
      val args = Bundle().apply {
        putBoolean(DiabloSession.TYPE, type)
        putBoolean(DiabloSession.LISTEN, listen)
      }
      service.showSession(args, VoiceInteractionSession.SHOW_WITH_ASSIST or VoiceInteractionSession.SHOW_WITH_SCREENSHOT)
      return true
    }
  }
}
