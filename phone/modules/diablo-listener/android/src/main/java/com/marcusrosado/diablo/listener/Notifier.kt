package com.marcusrosado.diablo.listener

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationChannelGroup
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/** Which section the app shows right now, so a reply he can already see does not also ring. */
object Presence {
  @Volatile var foreground = false
  @Volatile var section: String? = null

  fun looking(id: String) = foreground && section == id
}

/** One reply for a notification. */
data class Note(val id: Long, val text: String, val ts: Double)

/**
 * Diablo's notifications. Each section is its own channel, named after it and lit in its
 * accent, and its unread replies stack into one notification that opens the section.
 */
class Notifier(private val context: Context) {
  private val manager = context.getSystemService(NotificationManager::class.java)
  private val compat = NotificationManagerCompat.from(context)

  fun allowed(): Boolean = compat.areNotificationsEnabled()

  /** Creates and renames a channel per section and drops the ones whose section is gone. */
  fun channels(sections: List<SectionInfo>) {
    manager.createNotificationChannelGroups(
      listOf(
        NotificationChannelGroup(GROUP_SECTIONS, "Sections"),
        NotificationChannelGroup(GROUP_BACKGROUND, "In the background"),
      ),
    )
    manager.createNotificationChannel(
      NotificationChannel(LISTENING, "Listening for replies", NotificationManager.IMPORTANCE_MIN).apply {
        description = "Shown while the phone keeps its link to the hub open. Turn it off to hide it; replies still arrive."
        group = GROUP_BACKGROUND
        setShowBadge(false)
      },
    )
    val keep = sections.map { channelId(it.id) }.toSet()
    sections.forEach { s ->
      manager.createNotificationChannel(
        NotificationChannel(channelId(s.id), s.name, NotificationManager.IMPORTANCE_HIGH).apply {
          description = if (s.id == "diablo") "Replies in the main conversation" else s.brief.ifBlank { "Replies in ${s.name}" }
          group = GROUP_SECTIONS
          lightColor = argb(s.accent)
          enableLights(true)
        },
      )
    }
    manager.notificationChannels
      .filter { it.id.startsWith(SECTION_PREFIX) && it.id !in keep }
      .forEach { manager.deleteNotificationChannel(it.id) }
  }

  /** Adds new replies to the section's notification. */
  fun post(section: SectionInfo, fresh: List<Note>, tag: String = tag(section.id)) {
    if (fresh.isEmpty() || !allowed()) return
    val earlier = manager.activeNotifications
      .firstOrNull { it.tag == tag }
      ?.notification?.extras?.getStringArrayList(EXTRA_LINES)
      .orEmpty()
    val lines = (earlier + fresh.map { it.text.ifBlank { "[a reply with no text]" } }).takeLast(MAX_LINES)
    val latest = lines.last()
    val style = if (lines.size == 1) {
      NotificationCompat.BigTextStyle().bigText(latest.take(MAX_TEXT))
    } else {
      NotificationCompat.InboxStyle().also { inbox ->
        lines.forEach { inbox.addLine(it.lineSequence().first().take(120)) }
        inbox.setSummaryText("${lines.size} new")
      }
    }
    val notification = NotificationCompat.Builder(context, channelId(section.id))
      .setSmallIcon(R.drawable.ic_stat_diablo)
      .setColor(argb(section.accent))
      .setLargeIcon(mark(section))
      .setContentTitle(section.name)
      .setContentText(latest.replace('\n', ' '))
      .setStyle(style)
      .setNumber(lines.size)
      .setGroup(tag)
      .setCategory(NotificationCompat.CATEGORY_MESSAGE)
      .setWhen((fresh.last().ts * 1000).toLong())
      .setShowWhen(true)
      .setAutoCancel(true)
      .setContentIntent(open(section.id))
      .addExtras(Bundle().apply { putStringArrayList(EXTRA_LINES, ArrayList(lines)) })
      .build()
    runCatching { compat.notify(tag, SECTION_ID, notification) }
  }

  fun cancel(section: String) = compat.cancel(tag(section), SECTION_ID)

  fun cancelAll() = manager.activeNotifications
    .filter { it.tag?.startsWith(SECTION_PREFIX) == true || it.tag == TEST_TAG }
    .forEach { compat.cancel(it.tag, it.id) }

  /** The quiet notification Android requires while the listener runs. */
  fun listening(text: String): Notification =
    NotificationCompat.Builder(context, LISTENING)
      .setSmallIcon(R.drawable.ic_stat_diablo)
      .setColor(0xFFFF4D2E.toInt())
      .setContentTitle("Listening for Diablo")
      .setContentText(text)
      .setPriority(NotificationCompat.PRIORITY_MIN)
      .setOngoing(true)
      .setSilent(true)
      .setShowWhen(false)
      .setContentIntent(open(null))
      .build()

  fun showListening(text: String) {
    runCatching { compat.notify(LISTENING_ID, listening(text)) }
  }

  /** Opens the app on the section through its own link, which the router hands to the screens. */
  private fun open(section: String?): PendingIntent {
    val uri = Uri.parse(if (section != null) "diablo://section/${Uri.encode(section)}" else "diablo://")
    val intent = Intent(Intent.ACTION_VIEW, uri)
      .setPackage(context.packageName)
      .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    return PendingIntent.getActivity(
      context,
      section?.hashCode() ?: 0,
      intent,
      PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
  }

  /** The section's mark: its accent as a square with the first letter. */
  private fun mark(section: SectionInfo): Bitmap {
    val size = 128
    val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
    val canvas = Canvas(bitmap)
    canvas.drawColor(argb(section.accent))
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
      color = 0xFF131112.toInt()
      textSize = size * 0.5f
      typeface = Typeface.DEFAULT_BOLD
      textAlign = Paint.Align.CENTER
    }
    val y = size / 2f - (paint.descent() + paint.ascent()) / 2f
    canvas.drawText(section.name.take(1).uppercase(), size / 2f, y, paint)
    return bitmap
  }

  companion object {
    const val LISTENING = "listening"
    const val LISTENING_ID = 1
    const val TEST_TAG = "diablo-test"
    private const val SECTION_ID = 2
    private const val SECTION_PREFIX = "section-"
    private const val GROUP_SECTIONS = "sections"
    private const val GROUP_BACKGROUND = "background"
    private const val EXTRA_LINES = "diablo.lines"
    private const val MAX_LINES = 6
    private const val MAX_TEXT = 1500

    fun channelId(section: String) = SECTION_PREFIX + section
    private fun tag(section: String) = SECTION_PREFIX + section

    fun argb(hex: String): Int = runCatching { android.graphics.Color.parseColor(hex) }.getOrDefault(0xFFFF4D2E.toInt())
  }
}
