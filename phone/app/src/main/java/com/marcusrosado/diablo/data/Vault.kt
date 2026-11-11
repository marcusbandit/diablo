package com.marcusrosado.diablo.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Secrets such as the hub token, encrypted with a non-exportable AES key in the Android
 * keystore. Only the ciphertext reaches disk, and never the SQLite store.
 */
class Vault(context: Context) {
    private val prefs = context.getSharedPreferences("vault", Context.MODE_PRIVATE)

    fun get(name: String): String? {
        val stored = prefs.getString(name, null) ?: return null
        return runCatching {
            val raw = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, IV))
            String(cipher.doFinal(raw, IV, raw.size - IV), Charsets.UTF_8)
        }.getOrNull()
    }

    fun put(name: String, value: String?) {
        if (value.isNullOrEmpty()) {
            prefs.edit().remove(name).apply()
            return
        }
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(name, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val ALIAS = "diablo-vault"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV = 12
    }
}
