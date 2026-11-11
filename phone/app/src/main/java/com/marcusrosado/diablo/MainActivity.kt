package com.marcusrosado.diablo

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.marcusrosado.diablo.data.DiabloModel
import com.marcusrosado.diablo.notify.Notifier
import com.marcusrosado.diablo.notify.Presence
import com.marcusrosado.diablo.render.Gallery
import com.marcusrosado.diablo.ui.App
import com.marcusrosado.diablo.ui.DiabloTheme

class MainActivity : ComponentActivity() {
    private val model: DiabloModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val gallery = intent.getBooleanExtra("gallery", false)
        if (savedInstanceState == null) {
            opened(intent)
            openLink(intent)
        }
        setContent {
            DiabloTheme {
                if (gallery) Gallery() else App(model)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        opened(intent)
        openLink(intent)
    }

    override fun onStart() {
        super.onStart()
        model.shown()
    }

    override fun onResume() {
        super.onResume()
        Presence.foreground = true
    }

    override fun onPause() {
        Presence.foreground = false
        super.onPause()
    }

    override fun onStop() {
        model.hidden()
        super.onStop()
    }

    private fun opened(intent: Intent) {
        intent.getStringExtra(Notifier.EXTRA_SECTION)?.let(model::open)
    }

    private fun openLink(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme == "diablo" && data.host == "pair") model.pairFromLink(data.toString())
    }
}
