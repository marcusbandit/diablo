package com.marcusrosado.diablo.render

import android.os.Build
import android.util.Base64
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.compose.AsyncImage
import coil3.gif.AnimatedImageDecoder
import coil3.gif.GifDecoder
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import coil3.request.ImageRequest
import okhttp3.OkHttpClient
import coil3.svg.SvgDecoder
import com.marcusrosado.diablo.ui.DiabloColors
import com.marcusrosado.diablo.ui.DiabloFonts
import com.marcusrosado.diablo.ui.TypeScale

private val httpClient: OkHttpClient by lazy {
    OkHttpClient.Builder()
        .addInterceptor { chain ->
            chain.proceed(chain.request().newBuilder().header("User-Agent", "Diablo/0.1 (Android)").build())
        }
        .build()
}

fun buildImageLoader(context: PlatformContext): ImageLoader = ImageLoader.Builder(context)
    .components {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            add(AnimatedImageDecoder.Factory())
        } else {
            add(GifDecoder.Factory())
        }
        add(SvgDecoder.Factory())
        add(OkHttpNetworkFetcherFactory(callFactory = { httpClient }))
    }
    .build()

@Composable
fun ImageBlock(image: Block.Image, modifier: Modifier = Modifier) {
    AsyncImage(
        model = image.url,
        contentDescription = image.alt,
        contentScale = ContentScale.Inside,
        alignment = Alignment.TopStart,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(max = 480.dp),
    )
}

@Composable
fun SvgBlock(svg: Block.Svg, onLink: (String) -> Unit, modifier: Modifier = Modifier) {
    if (svg.animated) {
        SandboxedWeb(animatedSvgPage(svg.svg), onLink, modifier, key = svg)
        return
    }
    val uri = remember(svg.svg) {
        "data:image/svg+xml;base64," + Base64.encodeToString(svg.svg.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
    }
    val context = androidx.compose.ui.platform.LocalContext.current
    val request = remember(uri) {
        ImageRequest.Builder(context).data(uri).decoderFactory(SvgDecoder.Factory()).build()
    }
    val size = remember(svg.svg) { svgSize(svg.svg) }
    AsyncImage(
        model = request,
        contentDescription = null,
        contentScale = ContentScale.Fit,
        alignment = Alignment.TopStart,
        modifier = modifier
            .widthIn(max = size.first.dp)
            .fillMaxWidth()
            .aspectRatio(size.first / size.second),
    )
}

private val numberAttr = Regex("""\b(width|height)\s*=\s*["']\s*([0-9.]+)\s*(px)?\s*["']""")
private val viewBoxAttr = Regex("""viewBox\s*=\s*["']\s*[-0-9.]+[ ,]+[-0-9.]+[ ,]+([0-9.]+)[ ,]+([0-9.]+)\s*["']""")

/** Intrinsic size in dp from the root tag: width and height, else the viewBox, else a square. */
internal fun svgSize(svg: String): Pair<Float, Float> {
    val tag = Regex("<svg\\b[^>]*>", RegexOption.IGNORE_CASE).find(svg)?.value.orEmpty()
    val attrs = numberAttr.findAll(tag).associate { it.groupValues[1] to it.groupValues[2].toFloatOrNull() }
    val box = viewBoxAttr.find(tag)?.let { it.groupValues[1].toFloatOrNull() to it.groupValues[2].toFloatOrNull() }
    val w = attrs["width"] ?: box?.first
    val h = attrs["height"] ?: box?.second
    return if (w != null && h != null && w > 0f && h > 0f) w to h else 300f to 300f
}

private fun animatedSvgPage(svg: String) =
    "<div class=\"svg\">$svg</div>"

@Composable
internal fun Caption(text: String) {
    Text(
        text,
        style = TextStyle(fontFamily = DiabloFonts.mono, fontSize = TypeScale.small, color = DiabloColors.faint),
        modifier = Modifier.padding(vertical = 2.dp),
    )
}
