package com.marcusrosado.diablo.data

import com.marcusrosado.diablo.core.Store

/** Sync steps shared by the screens and the background listener. Blocking: run on Dispatchers.IO. */
object Sync {
    /** Fetches a section's messages past its cursor into the store; true if any arrived. */
    fun pull(hub: Hub, store: Store, url: String, token: String, section: String): Boolean {
        val key = "cursor:$section"
        val remote = hub.messages(url, token, section, store.get(key)?.toLongOrNull() ?: 0L)
        remote.filter { it.role == "user" }.forEach { m ->
            Hub.clientId(m)?.let { store.markSent(it, m.remoteId ?: return@let) }
        }
        if (remote.isEmpty()) return false
        store.mergeRemote(section, remote)
        store.set(key, remote.maxOf { it.remoteId ?: 0L }.toString())
        return true
    }
}
