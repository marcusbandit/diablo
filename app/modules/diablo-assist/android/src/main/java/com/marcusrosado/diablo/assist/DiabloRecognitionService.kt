package com.marcusrosado.diablo.assist

import android.content.Intent
import android.os.Bundle
import android.os.RemoteException
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/**
 * An assistant has to name a speech recognizer, and Android may make it the phone's default.
 * This one hands every request to the phone's own on-device recognizer, so other apps' voice
 * input keeps working the way it did.
 */
class DiabloRecognitionService : RecognitionService() {
  private var inner: SpeechRecognizer? = null

  override fun onStartListening(intent: Intent, callback: Callback) {
    inner?.destroy()
    if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) {
      safe { callback.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY) }
      return
    }
    val r = SpeechRecognizer.createOnDeviceSpeechRecognizer(this)
    inner = r
    r.setRecognitionListener(object : RecognitionListener {
      override fun onReadyForSpeech(params: Bundle?) = safe { callback.readyForSpeech(params ?: Bundle()) }
      override fun onBeginningOfSpeech() = safe { callback.beginningOfSpeech() }
      override fun onRmsChanged(rmsdB: Float) = safe { callback.rmsChanged(rmsdB) }
      override fun onBufferReceived(buffer: ByteArray?) = safe { if (buffer != null) callback.bufferReceived(buffer) }
      override fun onEndOfSpeech() = safe { callback.endOfSpeech() }
      override fun onError(error: Int) = safe { callback.error(error) }
      override fun onResults(results: Bundle?) = safe { callback.results(results ?: Bundle()) }
      override fun onPartialResults(partialResults: Bundle?) = safe { callback.partialResults(partialResults ?: Bundle()) }
      override fun onEvent(eventType: Int, params: Bundle?) {}
    })
    r.startListening(intent)
  }

  override fun onStopListening(callback: Callback) {
    inner?.stopListening()
  }

  override fun onCancel(callback: Callback) {
    inner?.cancel()
  }

  override fun onDestroy() {
    inner?.destroy()
    inner = null
    super.onDestroy()
  }

  private inline fun safe(block: () -> Unit) {
    try {
      block()
    } catch (_: RemoteException) {
      // The app that asked is gone.
    }
  }
}
