package com.marcusrosado.diablo.assist

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import java.util.concurrent.Executors
import kotlin.math.sqrt

/**
 * One conversation with him on the phone, shared by the assistant session and the overlay so a
 * turn keeps going when the session closes. Voice first: a session opens listening, what he
 * hears goes to the hub with the screen, and the reply is shown and spoken.
 */
object Talk {
  enum class Phase { RESTING, LISTENING, THINKING, SPEAKING, TROUBLE }

  data class Now(val phase: Phase, val heard: String, val reply: String)

  private val main = Handler(Looper.getMainLooper())
  private val work = Executors.newSingleThreadExecutor()
  private val listeners = LinkedHashSet<(Now) -> Unit>()

  var now = Now(Phase.RESTING, "", "")
    private set

  private var screen: ScreenRead? = null
  private var shot: String? = null
  /** The screen goes with the first line of a session only; later lines are about the talk. */
  private var screenSent = false
  private var turn = 0

  private var recognizer: SpeechRecognizer? = null
  private var recording: Thread? = null
  @Volatile private var stopRecording = false
  private var tts: TextToSpeech? = null
  private var ttsReady = false

  fun listen(fn: (Now) -> Unit) {
    listeners.add(fn)
    fn(now)
  }

  fun unlisten(fn: (Now) -> Unit) {
    listeners.remove(fn)
  }

  private fun set(next: Now) {
    now = next
    main.post { listeners.toList().forEach { it(next) } }
  }

  val busy get() = now.phase == Phase.THINKING || now.phase == Phase.SPEAKING

  /** A new session: the screen he was called over replaces the last one. */
  fun begin() {
    screen = null
    shot = null
    screenSent = false
    if (!busy) set(Now(Phase.RESTING, "", ""))
  }

  fun screen(read: ScreenRead) {
    screen = read.withShot(shot)
  }

  fun shot(jpeg: String) {
    shot = jpeg
    screen = screen?.withShot(jpeg)
  }

  fun micAllowed(context: Context) =
    context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

  /** Listens for one line, on the phone's own recognizer when it has one, else through the hub. */
  fun hear(context: Context) {
    stopHearing()
    stopSpeaking()
    if (!micAllowed(context)) {
      set(Now(Phase.TROUBLE, "", "Let me hear you first, sir: allow the microphone in the app."))
      return
    }
    set(Now(Phase.LISTENING, "", ""))
    if (SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) onDevice(context.applicationContext) else throughHub(context.applicationContext)
  }

  fun stopHearing() {
    recognizer?.let {
      it.cancel()
      it.destroy()
    }
    recognizer = null
    stopRecording = true
    recording = null
    if (now.phase == Phase.LISTENING) set(Now(Phase.RESTING, "", now.reply))
  }

  private fun onDevice(context: Context) {
    val r = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
    recognizer = r
    r.setRecognitionListener(object : RecognitionListener {
      override fun onReadyForSpeech(params: Bundle?) {}
      override fun onBeginningOfSpeech() {}
      override fun onRmsChanged(rmsdB: Float) {}
      override fun onBufferReceived(buffer: ByteArray?) {}
      override fun onEndOfSpeech() {}
      override fun onEvent(eventType: Int, params: Bundle?) {}

      override fun onPartialResults(partial: Bundle?) {
        val text = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull() ?: return
        if (recognizer === r && text.isNotBlank()) set(Now(Phase.LISTENING, text, ""))
      }

      override fun onResults(results: Bundle?) {
        if (recognizer !== r) return
        val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim() ?: ""
        r.destroy()
        recognizer = null
        if (text.isEmpty()) set(Now(Phase.RESTING, "", "")) else send(context, text)
      }

      override fun onError(error: Int) {
        if (recognizer !== r) return
        r.destroy()
        recognizer = null
        when (error) {
          SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> set(Now(Phase.RESTING, "", ""))
          // The phone's recognizer cannot do it after all: the hub's ears can.
          else -> throughHub(context)
        }
      }
    })
    val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
      .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
      .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
      .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
    r.startListening(intent)
  }

  /** Records until he stops talking, then the hub transcribes the clip. */
  private fun throughHub(context: Context) {
    set(Now(Phase.LISTENING, "", ""))
    stopRecording = false
    val thread = Thread {
      val wav = try {
        record()
      } catch (e: SecurityException) {
        null
      }
      if (stopRecording && recording !== Thread.currentThread()) return@Thread
      recording = null
      if (wav == null) {
        set(Now(Phase.RESTING, "", ""))
        return@Thread
      }
      set(Now(Phase.THINKING, "", ""))
      val text = try {
        HubLink.transcribe(context, wav)
      } catch (e: Exception) {
        trouble(e)
        return@Thread
      }
      if (text.isEmpty()) set(Now(Phase.RESTING, "", "")) else main.post { send(context, text) }
    }
    recording = thread
    thread.start()
  }

  private const val RATE = 16000

  /** 16 kHz mono PCM as a WAV, or null when nothing was said. */
  @Suppress("MissingPermission")
  private fun record(): ByteArray? {
    val size = maxOf(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), RATE / 5)
    val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
    val pcm = ByteArrayOutputStream()
    val frame = ShortArray(RATE / 50) // 20 ms
    val bytes = ByteBuffer.allocate(frame.size * 2).order(ByteOrder.LITTLE_ENDIAN)
    var spoke = false
    var quietMs = 0
    var totalMs = 0
    rec.startRecording()
    try {
      while (!stopRecording) {
        val n = rec.read(frame, 0, frame.size)
        if (n <= 0) break
        var sum = 0.0
        bytes.clear()
        for (i in 0 until n) {
          sum += frame[i].toDouble() * frame[i]
          bytes.putShort(frame[i])
        }
        pcm.write(bytes.array(), 0, n * 2)
        val loud = sqrt(sum / n) > 900
        totalMs += 20
        if (loud) {
          spoke = true
          quietMs = 0
        } else {
          quietMs += 20
        }
        if (!spoke && totalMs > 6000) return null
        if (spoke && quietMs > 1200) break
        if (totalMs > 20000) break
      }
    } finally {
      rec.stop()
      rec.release()
    }
    if (!spoke || stopRecording) return null
    return wav(pcm.toByteArray())
  }

  private fun wav(pcm: ByteArray): ByteArray {
    val head = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
    head.put("RIFF".toByteArray()).putInt(36 + pcm.size).put("WAVE".toByteArray())
    head.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1).putInt(RATE).putInt(RATE * 2).putShort(2).putShort(16)
    head.put("data".toByteArray()).putInt(pcm.size)
    return head.array() + pcm
  }

  /** Sends a line, with the screen if it is the session's first, and shows and speaks the reply. */
  fun send(context: Context, text: String) {
    val line = text.trim()
    if (line.isEmpty()) return
    stopHearing()
    stopSpeaking()
    val app = context.applicationContext
    val mine = ++turn
    val withScreen = if (screenSent) null else screen
    screenSent = true
    set(Now(Phase.THINKING, line, ""))
    warmVoice(app)
    work.execute {
      val reply = try {
        HubLink.chat(app, line, withScreen)
      } catch (e: Exception) {
        if (mine == turn) trouble(e)
        return@execute
      }
      if (mine != turn) return@execute
      main.post { say(app, reply.ifBlank { "Done, sir." }) }
    }
  }

  private fun trouble(e: Exception) {
    val why = (e as? HubLink.Failure)?.message ?: "That failed, sir."
    set(Now(Phase.TROUBLE, now.heard, why))
  }

  private fun warmVoice(context: Context) {
    if (tts != null) return
    tts = TextToSpeech(context) { status ->
      ttsReady = status == TextToSpeech.SUCCESS
      if (ttsReady) tts?.setLanguage(Locale.UK)
    }
  }

  private fun say(context: Context, reply: String) {
    set(Now(Phase.SPEAKING, now.heard, reply))
    warmVoice(context)
    val voice = tts
    val spoken = plain(reply)
    val done = { main.post { if (now.phase == Phase.SPEAKING && now.reply == reply) set(Now(Phase.RESTING, now.heard, reply)) } }
    if (voice == null) {
      done()
      return
    }
    voice.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
      override fun onStart(utteranceId: String?) {}
      override fun onDone(utteranceId: String?) { done() }
      @Deprecated("Deprecated in Java")
      override fun onError(utteranceId: String?) { done() }
    })
    // The engine may still be starting: speak once it is ready.
    fun go(tries: Int) {
      if (ttsReady) voice.speak(spoken, TextToSpeech.QUEUE_FLUSH, null, "reply")
      else if (tries > 0) main.postDelayed({ go(tries - 1) }, 150)
      else done()
    }
    go(20)
  }

  fun stopSpeaking() {
    tts?.stop()
    if (now.phase == Phase.SPEAKING) set(Now(Phase.RESTING, now.heard, now.reply))
  }

  /** Markdown and links read badly aloud. */
  private fun plain(text: String) = text
    .replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1")
    .replace(Regex("https?://\\S+"), "the link")
    .replace(Regex("[*_`#>]+"), "")
    .trim()
}
