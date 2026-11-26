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
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream
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

  /** The phone's own recognizer, fed the clip as it is recorded, only to show his words live. */
  private var recognizer: SpeechRecognizer? = null
  @Volatile private var feed: OutputStream? = null
  private var source: ParcelFileDescriptor? = null
  /** What the live recognizer has settled so far, and its guess at the piece being said. */
  private var settled = ""
  private var live = ""
  private var wordsAt = 0L
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
    // A question he is still waiting on stays: the session may open again just to hear the answer.
    if (!busy && !now.asks) set(Now(Phase.RESTING, "", ""))
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

  /**
   * Listens for one line: the phone records until he has been quiet for QUIET_MS and the hub
   * transcribes the clip. Meanwhile the phone's own recognizer hears the same audio, so his words
   * show while he speaks.
   */
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
    freshWords(context.applicationContext)
    set(Now(Phase.LISTENING, "", asked, asks = asked.isNotEmpty()))
    throughHub(context.applicationContext)
  }

  /** The words the hub says speech to text gets wrong, asked for again every few minutes. */
  private fun freshWords(context: Context) {
    val at = System.currentTimeMillis()
    if (at - wordsAt < WORDS_MS) return
    wordsAt = at
    voice.execute {
      try {
        HubLink.fetchWords(context)
      } catch (_: Exception) {
        // The last words the phone was given still hold.
      }
    }
  }

  fun stopHearing() {
    stopLive()
    stopRecording = true
    recording = null
    micLevel = 0f
    if (now.phase == Phase.LISTENING) set(Now(Phase.RESTING, "", now.reply, now.asks))
  }

  /** He tapped while being heard: what was said so far goes now, without waiting for the pause. */
  fun finishHearing() {
    if (now.phase != Phase.LISTENING) return
    if (recording != null) finishRecording = true
  }

  private fun idle() = Now(Phase.RESTING, "", asked, asks = asked.isNotEmpty())

  private fun heardLive() = listOf(settled, live).filter { it.isNotBlank() }.joinToString(" ").trim()

  /**
   * Starts the phone's recognizer on a pipe the recording writes into. It only shows words; the
   * line that is sent comes from the hub. When it ends a piece on its own, a fresh one takes over.
   */
  private fun startLive(context: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || !SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) return
    val pipe = try {
      ParcelFileDescriptor.createPipe()
    } catch (_: IOException) {
      return
    }
    val r = try {
      SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
    } catch (_: Exception) {
      pipe.forEach { it.close() }
      return
    }
    recognizer = r
    source = pipe[0]
    val show = {
      if (recognizer === r && now.phase == Phase.LISTENING) {
        val text = heardLive()
        if (text.isNotEmpty()) set(Now(Phase.LISTENING, words(context).fix(text), asked, asks = asked.isNotEmpty()))
      }
    }
    val keep = { bundle: Bundle? ->
      val text = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim() ?: ""
      if (text.isNotEmpty()) settled = listOf(settled, text).filter { it.isNotBlank() }.joinToString(" ")
      live = ""
    }
    r.setRecognitionListener(object : RecognitionListener {
      override fun onReadyForSpeech(params: Bundle?) {}
      override fun onBeginningOfSpeech() {}
      override fun onRmsChanged(rmsdB: Float) {}
      override fun onBufferReceived(buffer: ByteArray?) {}
      override fun onEndOfSpeech() {}
      override fun onEvent(eventType: Int, params: Bundle?) {}

      override fun onPartialResults(partial: Bundle?) {
        if (recognizer !== r) return
        live = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim() ?: return
        show()
      }

      override fun onSegmentResults(segment: Bundle) {
        if (recognizer !== r) return
        keep(segment)
        show()
      }

      override fun onEndOfSegmentedSession() {}

      override fun onResults(results: Bundle?) {
        if (recognizer !== r) return
        keep(results)
        show()
        stopLive()
        if (now.phase == Phase.LISTENING && recording != null) startLive(context)
      }

      override fun onError(error: Int) {
        if (recognizer === r) stopLive()
      }
    })
    val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
      .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
      .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
      .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
      .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, pipe[0])
      .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
      .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
      .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, RATE)
      .putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_AUDIO_SOURCE)
    val bias = words(context).rights
    if (bias.isNotEmpty()) intent.putStringArrayListExtra(RecognizerIntent.EXTRA_BIASING_STRINGS, ArrayList(bias))
    feed = ParcelFileDescriptor.AutoCloseOutputStream(pipe[1])
    try {
      r.startListening(intent)
    } catch (_: Exception) {
      stopLive()
    }
  }

  private fun stopLive() {
    val f = feed
    feed = null
    try {
      f?.close()
    } catch (_: IOException) {
      // Already closed by the other side.
    }
    recognizer?.let {
      try {
        it.cancel()
        it.destroy()
      } catch (_: Exception) {
        // A recognizer that already died needs nothing more.
      }
    }
    recognizer = null
    try {
      source?.close()
    } catch (_: IOException) {
      // Nothing left to release.
    }
    source = null
  }

  /** Records until he stops talking, then the hub transcribes the clip. */
  private fun throughHub(context: Context) {
    set(Now(Phase.LISTENING, "", asked, asks = asked.isNotEmpty()))
    stopRecording = false
    finishRecording = false
    settled = ""
    live = ""
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
      main.post { stopLive() }
      if (wav == null) {
        set(idle())
        return@Thread
      }
      val shown = now.heard
      set(Now(Phase.THINKING, shown, ""))
      val text = try {
        HubLink.transcribe(context, wav)
      } catch (e: Exception) {
        // The hub could not hear it: what the phone heard itself still goes, if it heard anything.
        if (shown.isEmpty()) {
          trouble(e)
          return@Thread
        }
        shown
      }
      if (mine != turn) return@Thread
      if (text.isEmpty()) set(idle()) else main.post { if (mine == turn) send(context, words(context).fix(text)) }
    }
    recording = thread
    thread.start()
    startLive(context)
  }

  private const val RATE = 16000
  private const val QUIET_MS = 2000
  private const val WORDS_MS = 10 * 60_000L

  private fun words(context: Context) = HubLink.words(context)

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
        feed?.let {
          try {
            it.write(bytes.array(), 0, n * 2)
          } catch (_: IOException) {
            if (feed === it) feed = null
          }
        }
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
        if (spoke && quietMs > QUIET_MS) break
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

  /**
   * Speaks the reply in the voice the hub renders and shows it the moment the sound starts, so the
   * words and his voice arrive together. Without the voice, the reply shows silently.
   */
  private fun say(context: Context, reply: String, asks: Boolean, mine: Int) {
    if (mine != turn) return
    val heard = now.heard
    val show = {
      if (mine == turn && now.phase == Phase.THINKING) set(Now(Phase.SPEAKING, heard, reply, asks))
    }
    val done = { spoken: Boolean ->
      main.post {
        show()
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
        if (mine != turn || now.phase != Phase.THINKING) return@post
        if (file == null) done(false) else play(context, file, show, done)
      }
    }
  }

  private fun play(context: Context, file: File, started: () -> Unit, done: (Boolean) -> Unit) {
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
      p.setOnPreparedListener {
        if (player === p) {
          it.start()
          started()
        }
      }
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

  /**
   * His reply as the card shows it: bold stays bold, the other markdown marks go, links keep
   * their words and list lines get a dot.
   */
  fun readable(text: String): CharSequence {
    val lines = text.trim().lines().filter { !it.trim().startsWith("```") }.map { line ->
      line
        .replace(Regex("^#{1,6}\\s+"), "")
        .replace(Regex("^\\s*[-*+]\\s+(\\[[ xX]]\\s+)?"), "\u2022 ")
        .replace(Regex("^>\\s?"), "")
        .replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1")
        .replace("`", "")
    }
    val out = android.text.SpannableStringBuilder()
    val bold = Regex("\\*\\*(.+?)\\*\\*|__(.+?)__")
    lines.forEachIndexed { i, line ->
      if (i > 0) out.append('\n')
      var at = 0
      for (m in bold.findAll(line)) {
        out.append(line.substring(at, m.range.first).replace(Regex("(?<!\\w)[*_](\\S(?:.*?\\S)?)[*_](?!\\w)"), "$1"))
        val start = out.length
        out.append(m.groupValues[1].ifEmpty { m.groupValues[2] })
        out.setSpan(android.text.style.StyleSpan(android.graphics.Typeface.BOLD), start, out.length, android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        at = m.range.last + 1
      }
      out.append(line.substring(at).replace(Regex("(?<!\\w)[*_](\\S(?:.*?\\S)?)[*_](?!\\w)"), "$1"))
    }
    return out
  }

  /** Markdown and links read badly aloud. */
  private fun plain(text: String) = text
    .replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1")
    .replace(Regex("https?://\\S+"), "the link")
    .replace(Regex("[*_`#>]+"), "")
    .trim()
}
