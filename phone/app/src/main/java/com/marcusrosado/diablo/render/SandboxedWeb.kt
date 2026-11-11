package com.marcusrosado.diablo.render

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.rememberNestedScrollInteropConnection

private const val CSP =
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; media-src data:"

private const val STYLE = """
html,body{margin:0;padding:0;background:transparent}
body{color:#f2f1ee;font:16px/1.5 serif;overflow-wrap:anywhere}
*{box-sizing:border-box;max-width:100%}
a{color:#74aaff}
h1,h2,h3,h4{font-family:sans-serif;line-height:1.25;margin:.6em 0 .3em}
p{margin:.5em 0}
svg,img,video{max-width:100%;height:auto}
.svg{display:block;line-height:0}
table{border-collapse:collapse}
td,th{border:1px solid rgba(242,241,238,.18);padding:4px 8px}
code,pre{font-family:monospace;font-size:13px;background:rgba(242,241,238,.09)}
pre{padding:8px;overflow-x:auto}
hr{border:0;border-top:1px solid rgba(242,241,238,.18)}
"""

internal fun wrapHtml(body: String): String =
    "<!doctype html><html><head><meta charset=\"utf-8\">" +
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
        "<meta http-equiv=\"Content-Security-Policy\" content=\"$CSP\">" +
        "<style>$STYLE</style></head><body>$body</body></html>"

private class SandboxWebView(context: Context) : WebView(context) {
    var onWidthChanged: ((Int) -> Unit)? = null
    var onContentHeight: ((Int) -> Unit)? = null

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && w != oldw) onWidthChanged?.invoke(w)
    }
}

@SuppressLint("SetJavaScriptEnabled")
private fun createSandboxWebView(context: Context, onLink: () -> ((String) -> Unit)): SandboxWebView {
    val view = SandboxWebView(context)
    with(view.settings) {
        javaScriptEnabled = false
        allowFileAccess = false
        allowContentAccess = false
        blockNetworkLoads = true
        domStorageEnabled = false
        databaseEnabled = false
        setGeolocationEnabled(false)
        setSupportZoom(false)
        builtInZoomControls = false
        safeBrowsingEnabled = true
        textZoom = 100
    }
    view.setBackgroundColor(Color.TRANSPARENT)
    view.isVerticalScrollBarEnabled = false
    view.isHorizontalScrollBarEnabled = false
    view.overScrollMode = WebView.OVER_SCROLL_NEVER
    view.webViewClient = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (url.scheme == "http" || url.scheme == "https") onLink()(url.toString())
            return true
        }

        override fun onPageFinished(view: WebView, url: String?) {
            view.postVisualStateCallback(0L, object : WebView.VisualStateCallback() {
                override fun onComplete(requestId: Long) {
                    (view as SandboxWebView).onContentHeight?.invoke(view.contentHeight)
                }
            })
        }
    }
    return view
}

/**
 * Renders untrusted HTML with no script, no network and no file access. Height follows the
 * content without JavaScript: the view starts 1 dp tall, reads contentHeight once the page has
 * been drawn, then grows to it up to [maxHeight] and scrolls inside beyond that.
 */
@Composable
fun SandboxedWeb(
    html: String,
    onLink: (String) -> Unit,
    modifier: Modifier = Modifier,
    maxHeight: Dp = 480.dp,
    key: Any = html,
) {
    val context = LocalContext.current
    val currentOnLink by rememberUpdatedState(onLink)
    var contentDp by remember(key) { mutableIntStateOf(0) }
    val page = remember(html) { wrapHtml(html) }
    val view = remember(key) { createSandboxWebView(context) { currentOnLink } }
    DisposableEffect(view) {
        onDispose {
            view.onWidthChanged = null
            view.onContentHeight = null
            view.destroy()
        }
    }
    DisposableEffect(view, page) {
        var loadedWidth = 0
        fun load() {
            view.loadDataWithBaseURL(null, page, "text/html", "utf-8", null)
        }
        view.onContentHeight = { contentDp = it }
        view.onWidthChanged = { w ->
            if (w != loadedWidth) {
                loadedWidth = w
                contentDp = 0
                load()
            }
        }
        if (view.width > 0) view.onWidthChanged?.invoke(view.width)
        onDispose { }
    }
    val height = if (contentDp <= 0) 1.dp else minOf(contentDp.dp, maxHeight)
    AndroidView(
        factory = { view },
        modifier = modifier
            .fillMaxWidth()
            .height(height)
            .nestedScroll(rememberNestedScrollInteropConnection()),
    )
}
