package com.marcusrosado.diablo.data

import java.net.URI
import java.net.URLDecoder

/** A pairing code and, when it came from a QR code or a link, the hub that minted it. */
data class PairCode(val code: String, val host: String?) {
    companion object {
        private const val LENGTH = 8
        private val alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789".toSet()

        fun normalize(raw: String): String = raw.uppercase().filter { it.isLetterOrDigit() }

        fun valid(code: String) = code.length == LENGTH && code.all { it in alphabet }

        /** Reads diablo://pair?host=..&code=.., the hub's pair page URL, or the bare letters. */
        fun parse(raw: String): PairCode? {
            val text = raw.trim()
            val uri = runCatching { URI(text) }.getOrNull()
            if (uri?.scheme != null && uri.rawQuery != null) {
                val query = uri.rawQuery.split('&').mapNotNull {
                    val (k, v) = it.split('=', limit = 2).takeIf { p -> p.size == 2 } ?: return@mapNotNull null
                    URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8")
                }.toMap()
                val code = normalize(query["code"] ?: return null)
                if (!valid(code)) return null
                val host = when (uri.scheme.lowercase()) {
                    "diablo" -> query["host"]?.takeIf { it.startsWith("http://") || it.startsWith("https://") }
                    "http", "https" -> "${uri.scheme}://${uri.rawAuthority}"
                    else -> null
                }
                return PairCode(code, host?.trimEnd('/'))
            }
            val code = normalize(text)
            return if (valid(code)) PairCode(code, null) else null
        }
    }
}
