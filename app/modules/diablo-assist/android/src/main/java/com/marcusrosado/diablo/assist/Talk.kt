package com.marcusrosado.diablo.assist

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import kotlin.math.sqrt

/**
 * One conversation with him on the phone, shared by the assistant session and the overlay so a
 * turn keeps going when the session closes. Voice first: a session opens listening, what he
 * hears goes to the hub with the screen, and the reply is shown and spoken in his own voice, the
 * one the hub renders for the desk, never a voice Android picks.
 */
object Talk {
  enum class Phase { RESTING, LISTENING, THINKING, SPEAKING, TROUBLE }

  /** asks: the reply waits on Marcus. spoken: he said it aloud, so it was heard, not only read. */
  data class Now(val phase: Phase, val heard: String, val reply: String, val asks: Boolean = false, val spoken: Boolean = false)

  private const val HEARD_MS = 2_500L
  private const val READ_MS = 2_000L
  private const val PER_CHAR_MS = 50L
  private const val MAX_READ_MS = 10_000L
  private const val TROUBLE_MS = 4_000L

  /**
   * How long a finished reply stays on screen before he goes, or null while he still has
   * something to do or the reply waits on Marcus.
   */
  fun closeAfter(n: Now): Long? {
    if (n.reply.isEmpty()) return null
    val read = minOf(READ_MS + n.reply.length * PER_CHAR_MS, MAX_READ_MS)
    return when (n.phase) {
      Phase.RESTING -> if (n.asks) null else if (n.spoken) HEARD_MS else read
      Phase.TROUBLE -> maxOf(read, TROUBLE_MS)
      else -> null
    }
  }

  private val main = Handler(Looper.getMainLooper())
  private val work = Executors.newSingleThreadExecutor()
  private val voice = Executors.newCachedThreadPool()
  private val listeners = LinkedHashSet<(Now) -> Unit>()

  var now = Now(Phase.RESTING, "", "")
    private set

  private var screen: ScreenRead? = null
  private var shot: String? = null
  /** The screen goes with the first line of a session only; later lines are about the talk. */
  private var screenSent = false
  private var turn = 0

  /** How loud Marcus is right now while he is heard, 0 to 1, for the halo. */
  @Volatile var micLevel = 0f
    private set
  /** Called once he finished saying a reply that asks something: listen for the answer. */
  var again: (() -> Unit)? = null
  /** The question he is waiting on, kept on the card while the answer is heard. */
  private var asked = ""
  private var envelope: FloatArray? = null
  @Volatile private var finishRecording = false

  private var recognizer: SpeechRecognizer? = null
  private var recording: Thread? = null
  @Volatile private var stopRecording = false
  private var player: MediaPlayer? = null
  private var audio: AudioManager? = null
  private var focus: AudioFocusRequest? = null
  private val speech = AudioAttributes.Builder()
    .setUsage(AudioAttributes.USAGE_ASSISTANT)
    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
    .build()

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
    // Listening for the answer to his question keeps the question on screen.
    asked = if (now.asks && now.reply.isNotEmpty() && now.phase != Phase.THINKING) now.reply else ""
    // Talking over a reply still being worked out replaces that line.
    if (now.phase == Phase.THINKING) turn++
    micLevel = 0f
    set(Now(Phase.LISTENING, "", asked, asks = asked.isNotEmpty()))
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
    micLevel = 0f
    if (now.phase == Phase.LISTENING) set(Now(Phase.RESTING, "", now.reply, now.asks))
  }

  /** He tapped while being heard: what was said so far goes now, without waiting for the pause. */
  fun finishHearing() {
    if (now.phase != Phase.LISTENING) return
    recognizer?.stopListening()
    if (recording != null) finishRecording = true
  }

  private fun idle() = Now(Phase.RESTING, "", asked, asks = asked.isNotEmpty())

  private fun onDevice(context: Context) {
    val r = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
    recognizer = r
    r.setRecognitionListener(object : RecognitionListener {
      override fun onReadyForSpeech(params: Bundle?) {}
      override fun onBeginningOfSpeech() {}
      override fun onRmsChanged(rmsdB: Float) {
        if (recognizer === r) micLevel = ((rmsdB + 2f) / 12f).coerceIn(0f, 1f)
      }
      override fun onBufferReceived(buffer: ByteArray?) {}
      override fun onEndOfSpeech() {}
      override fun onEvent(eventType: Int, params: Bundle?) {}

      override fun onPartialResults(partial: Bundle?) {
        val text = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull() ?: return
        if (recognizer === r && text.isNotBlank()) set(Now(Phase.LISTENING, text, asked, asks = asked.isNotEmpty()))
      }

      override fun onResults(results: Bundle?) {
        if (recognizer !== r) return
        val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim() ?: ""
        r.destroy()
        recognizer = null
        micLevel = 0f
        if (text.isEmpty()) set(idle()) else send(context, text)
      }

      override fun onError(error: Int) {
        if (recognizer !== r) return
        r.destroy()
        recognizer = null
        when (error) {
          SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> {
            micLevel = 0f
            set(idle())
          }
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
    set(Now(Phase.LISTENING, "", asked, asks = asked.isNotEmpty()))
    stopRecording = false
    finishRecording = false
    val mine = turn
    val thread = Thread {
      val wav = try {
        record()
      } catch (e: SecurityException) {
        null
      }
      if (stopRecording && recording !== Thread.currentThread()) return@Thread
      recording = null
      micLevel = 0f
      if (wav == null) {
        set(idle())
        return@Thread
      }
      set(Now(Phase.THINKING, "", ""))
      val text = try {
        HubLink.transcribe(context, wav)
      } catch (e: Exception) {
        trouble(e)
        return@Thread
      }
      if (mine != turn) return@Thread
      if (text.isEmpty()) set(idle()) else main.post { if (mine == turn) send(context, text) }
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
        val rms = sqrt(sum / n)
        micLevel = (rms / 3000.0).toFloat().coerceIn(0f, 1f)
        val loud = rms > 900
        totalMs += 20
        if (loud) {
          spoke = true
          quietMs = 0
        } else {
          quietMs += 20
        }
        if (!spoke && totalMs > 6000) return null
        if (spoke && quietMs > 1200) break
        if (totalMs > 20000 || finishRecording) break
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
    asked = ""
    val withScreen = if (screenSent) null else screen
    screenSent = true
    set(Now(Phase.THINKING, line, ""))
    work.execute {
      val reply = try {
        HubLink.chat(app, line, withScreen)
      } catch (e: Exception) {
        if (mine == turn) trouble(e)
        return@execute
      }
      if (mine != turn) return@execute
      main.post { say(app, reply.text.ifBlank { "Done, sir." }, reply.asks, mine) }
    }
  }

  private fun trouble(e: Exception) {
    val why = (e as? HubLink.Failure)?.message ?: "That failed, sir."
    set(Now(Phase.TROUBLE, now.heard, why))
  }

  /** Shows the reply at once and speaks it in the voice the hub renders; without it, he stays silent. */
  private fun say(context: Context, reply: String, asks: Boolean, mine: Int) {
    if (mine != turn) return
    set(Now(Phase.SPEAKING, now.heard, reply, asks))
    val done = { spoken: Boolean ->
      main.post {
        if (mine == turn && now.phase == Phase.SPEAKING && now.reply == reply) {
          set(Now(Phase.RESTING, now.heard, reply, asks, spoken))
          // He asked something: he listens for the answer by himself, a beat after he stops.
          if (asks) main.postDelayed({ if (mine == turn && now.phase == Phase.RESTING && now.reply == reply) again?.invoke() }, AGAIN_MS)
        }
      }
      Unit
    }
    val words = spoken(plain(reply))
    if (words.isEmpty()) {
      done(false)
      return
    }
    voice.execute {
      if (mine != turn) return@execute
      val file = try {
        val wav = HubLink.speech(context, words)
        if (mine != turn) return@execute
        envelope = envelopeOf(wav)
        File(context.cacheDir, "diablo-reply.wav").apply { writeBytes(wav) }
      } catch (e: Exception) {
        null
      }
      main.post {
        if (file == null || mine != turn || now.phase != Phase.SPEAKING) done(false) else play(context, file, done)
      }
    }
  }

  private fun play(context: Context, file: File, done: (Boolean) -> Unit) {
    stopPlayer()
    val manager = context.getSystemService(AudioManager::class.java)
    val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
      .setAudioAttributes(speech)
      .setOnAudioFocusChangeListener({ change ->
        if (change == AudioManager.AUDIOFOCUS_LOSS || change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) stopSpeaking()
      }, main)
      .build()
    if (manager == null || manager.requestAudioFocus(request) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
      done(false)
      return
    }
    audio = manager
    focus = request
    val p = MediaPlayer()
    player = p
    val finish = { ok: Boolean ->
      if (player === p) stopPlayer()
      done(ok)
    }
    try {
      p.setAudioAttributes(speech)
      p.setDataSource(file.path)
      p.setOnPreparedListener { if (player === p) it.start() }
      p.setOnCompletionListener { finish(true) }
      p.setOnErrorListener { _, _, _ ->
        finish(false)
        true
      }
      p.prepareAsync()
    } catch (e: Exception) {
      finish(false)
    }
  }

  /** How loud his voice is at this moment of the reply, 0 to 1, for the halo and his mouth. */
  fun voiceLevel(): Float {
    val p = player ?: return 0f
    val env = envelope ?: return 0f
    val at = try {
      p.currentPosition
    } catch (_: IllegalStateException) {
      return 0f
    }
    val i = at / ENV_MS
    return if (i in env.indices) env[i] else 0f
  }

  private const val ENV_MS = 40
  private const val AGAIN_MS = 350L

  /** The loudness of a 16 bit wav in steps of ENV_MS, scaled so the loudest step is 1. */
  private fun envelopeOf(wav: ByteArray): FloatArray? {
    if (wav.size < 12) return null
    val b = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
    var at = 12
    var rate = 24000
    var channels = 1
    while (at + 8 <= wav.size) {
      val kind = String(wav, at, 4, Charsets.US_ASCII)
      val size = b.getInt(at + 4).let { if (it < 0 || it > wav.size - at - 8) wav.size - at - 8 else it }
      val body = at + 8
      if (kind == "fmt " && size >= 16) {
        channels = maxOf(1, b.getShort(body + 2).toInt())
        rate = b.getInt(body + 4)
      } else if (kind == "data") {
        val end = body + size
        val per = maxOf(1, rate * ENV_MS / 1000 * channels)
        val steps = ArrayList<Float>()
        var i = body
        while (i + 2 <= end) {
          var sum = 0.0
          var n = 0
          while (n < per && i + 2 <= end) {
            val v = b.getShort(i).toDouble()
            sum += v * v
            i += 2
            n++
          }
          steps.add(sqrt(sum / maxOf(n, 1)).toFloat())
        }
        val top = steps.maxOrNull() ?: return null
        if (top <= 0f) return null
        return FloatArray(steps.size) { (steps[it] / top).coerceIn(0f, 1f) }
      }
      at = body + size + (size and 1)
    }
    return null
  }

  private fun stopPlayer() {
    player?.let {
      player = null
      try {
        it.stop()
      } catch (_: IllegalStateException) {
        // Never started: nothing to stop.
      }
      it.release()
    }
    focus?.let { audio?.abandonAudioFocusRequest(it) }
    focus = null
  }

  /** He was sent away: whatever he was hearing, thinking or saying ends here, and a late reply stays unsaid. */
  fun quiet() {
    turn++
    asked = ""
    stopHearing()
    stopSpeaking()
    set(Now(Phase.RESTING, "", ""))
  }

  fun stopSpeaking() {
    stopPlayer()
    if (now.phase == Phase.SPEAKING) set(now.copy(phase = Phase.RESTING))
  }

  private const val SPOKEN_MAX = 3800

  /** The hub renders at most 4000 characters: a longer reply is said up to the last full sentence that fits. */
  private fun spoken(words: String): String {
    if (words.length <= SPOKEN_MAX) return words
    val cut = words.substring(0, SPOKEN_MAX)
    val end = maxOf(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "), cut.lastIndexOf('\n'))
    return if (end > SPOKEN_MAX / 2) cut.substring(0, end + 1).trim() else cut.substringBeforeLast(' ').trim()
  }

  /** Markdown and links read badly aloud. */
  private fun plain(text: String) = text
    .replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1")
    .replace(Regex("https?://\\S+"), "the link")
    .replace(Regex("[*_`#>]+"), "")
    .trim()
}
