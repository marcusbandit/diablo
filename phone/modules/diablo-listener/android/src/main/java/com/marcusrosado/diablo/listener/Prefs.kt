package com.marcusrosado.diablo.listener

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** One section as the listener needs it: enough to name a channel and colour a notification. */
data class SectionInfo(val id: String, val name: String, val accent: String, val brief: String)

/**
 * What the listener keeps between runs, so it can start after a reboot with no JavaScript:
 * the hubs in order, the sections, the on switch, the per-section seen cursors, and the
 * device token, encrypted with a key that never leaves the Android keystore.
 */
object Prefs {
  private const val FILE = "diablo-listener"
  private const val KEY_ALIAS = "diablo-listener-token"

  private fun prefs(context: Context): SharedPreferences =
    context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

  fun enabled(context: Context) = prefs(context).getBoolean("enabled", false)

  fun hubs(context: Context): List<String> {
    val raw = prefs(context).getString("hubs", "[]") ?: "[]"
    val array = runCatching { JSONArray(raw) }.getOrDefault(JSONArray())
    return (0 until array.length()).mapNotNull { array.optString(it).trimEnd('/').ifBlank { null } }
  }

  fun sections(context: Context): List<SectionInfo> {
    val raw = prefs(context).getString("sections", "[]") ?: "[]"
    val array = runCatching { JSONArray(raw) }.getOrDefault(JSONArray())
    return (0 until array.length()).mapNotNull { i ->
      val o = array.optJSONObject(i) ?: return@mapNotNull null
      val id = o.optString("id").ifBlank { return@mapNotNull null }
      if (o.optBoolean("deleted", false)) return@mapNotNull null
      SectionInfo(id, o.optString("name", id), o.optString("accent", "#ff4d2e"), o.optString("brief", ""))
    }
  }

  fun saveSections(context: Context, sections: JSONArray) {
    prefs(context).edit().putString("sections", sections.toString()).apply()
  }

  /** When notifications were turned on, in unix seconds: older replies never ring. */
  fun since(context: Context): Double = prefs(context).getString("since-s", null)?.toDoubleOrNull() ?: 0.0

  fun seen(context: Context, section: String): Long? =
    prefs(context).getLong("seen:$section", -1L).takeIf { it >= 0 }

  fun setSeen(context: Context, section: String, id: Long) {
    prefs(context).edit().putLong("seen:$section", id).apply()
  }

  fun configure(context: Context, config: Map<String, Any?>) {
    val edit = prefs(context).edit()
    val wasOn = enabled(context)
    val on = config["enabled"] as? Boolean ?: wasOn
    edit.putBoolean("enabled", on)
    (config["hubs"] as? List<*>)?.let { hubs -> edit.putString("hubs", JSONArray(hubs.mapNotNull { it as? String }).toString()) }
    (config["sections"] as? List<*>)?.let { list ->
      val array = JSONArray()
      list.forEach { item -> (item as? Map<*, *>)?.let { array.put(JSONObject(it.mapKeys { k -> k.key.toString() })) } }
      edit.putString("sections", array.toString())
    }
    if (on && !wasOn) {
      // Turning on: only replies from now on ring, not the history.
      edit.putString("since-s", (System.currentTimeMillis() / 1000.0).toString())
      prefs(context).all.keys.filter { it.startsWith("seen:") }.forEach { edit.remove(it) }
    }
    edit.apply()
    if (config.containsKey("token")) saveToken(context, config["token"] as? String)
  }

  /** On, paired, and Android lets the app post notifications. */
  fun wanted(context: Context): Boolean =
    enabled(context) && token(context) != null && NotificationManagerCompat.from(context).areNotificationsEnabled()

  fun token(context: Context): String? {
    val sealed = prefs(context).getString("token", null) ?: return null
    return runCatching {
      val bytes = Base64.decode(sealed, Base64.NO_WRAP)
      val cipher = Cipher.getInstance("AES/GCM/NoPadding")
      cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12))
      String(cipher.doFinal(bytes, 12, bytes.size - 12), Charsets.UTF_8)
    }.getOrNull()
  }

  private fun saveToken(context: Context, token: String?) {
    if (token.isNullOrBlank()) {
      prefs(context).edit().remove("token").apply()
      return
    }
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key())
    val sealed = cipher.iv + cipher.doFinal(token.toByteArray(Charsets.UTF_8))
    prefs(context).edit().putString("token", Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
  }

  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(
      KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .setKeySize(256)
        .build(),
    )
    return generator.generateKey()
  }
}
