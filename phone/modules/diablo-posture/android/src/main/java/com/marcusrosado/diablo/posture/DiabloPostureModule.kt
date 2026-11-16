package com.marcusrosado.diablo.posture

import android.app.Activity
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import androidx.core.util.Consumer
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlin.math.abs

/**
 * The fold as the app's window sees it: Jetpack WindowManager's FoldingFeature (state,
 * orientation, bounds in dp of the window) and the hinge angle sensor (180 is flat).
 * React Native has no API for either.
 */
class DiabloPostureModule : Module() {
  private val main = Handler(Looper.getMainLooper())
  private var adapter: WindowInfoTrackerCallbackAdapter? = null
  private var tracked: Activity? = null
  private var observing = false
  private var fold: Map<String, Any?>? = null
  private var angle: Double? = null
  private var sensors: SensorManager? = null

  private val layout = Consumer<WindowLayoutInfo> { info ->
    fold = describe(info)
    emit()
  }

  private val hinge = object : SensorEventListener {
    override fun onSensorChanged(event: SensorEvent) {
      val value = event.values.firstOrNull()?.toDouble() ?: return
      val before = angle
      angle = value
      if (before == null || abs(before - value) >= 1.0) emit()
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
  }

  override fun definition() = ModuleDefinition {
    Name("DiabloPosture")

    Events("onChange")

    Function("get") { snapshot() }

    OnStartObserving {
      observing = true
      start()
    }

    OnStopObserving {
      observing = false
      stop()
    }

    OnActivityEntersForeground { if (observing) start() }

    OnActivityEntersBackground { stop() }

    OnDestroy { stop() }
  }

  private fun snapshot(): Map<String, Any?> = mapOf(
    "fold" to fold,
    "angle" to angle,
    "hasHinge" to (hingeSensor() != null),
  )

  private fun emit() {
    if (observing) sendEvent("onChange", snapshot())
  }

  private fun hingeSensor(): Sensor? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
    val manager = sensors ?: (appContext.reactContext?.getSystemService(SensorManager::class.java))?.also { sensors = it }
    return manager?.getDefaultSensor(Sensor.TYPE_HINGE_ANGLE)
  }

  private fun start() {
    val activity = appContext.currentActivity ?: return
    // WindowManager's extension wants its listener registered from the main thread.
    main.post {
      if (tracked !== activity) {
        stopLayout()
        val tracker = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(activity))
        tracker.addWindowLayoutInfoListener(activity, ContextCompat.getMainExecutor(activity), layout)
        adapter = tracker
        tracked = activity
      }
    }
    hingeSensor()?.let { sensors?.registerListener(hinge, it, SensorManager.SENSOR_DELAY_UI) }
  }

  private fun stopLayout() {
    adapter?.removeWindowLayoutInfoListener(layout)
    adapter = null
    tracked = null
  }

  private fun stop() {
    main.post { stopLayout() }
    sensors?.unregisterListener(hinge)
  }

  private fun describe(info: WindowLayoutInfo): Map<String, Any?>? {
    val feature = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull() ?: return null
    val density = tracked?.resources?.displayMetrics?.density ?: 1f
    val b = feature.bounds
    return mapOf(
      "state" to if (feature.state == FoldingFeature.State.HALF_OPENED) "half" else "flat",
      "orientation" to if (feature.orientation == FoldingFeature.Orientation.VERTICAL) "vertical" else "horizontal",
      "separating" to feature.isSeparating,
      "occlusion" to if (feature.occlusionType == FoldingFeature.OcclusionType.FULL) "full" else "none",
      "left" to b.left / density,
      "top" to b.top / density,
      "right" to b.right / density,
      "bottom" to b.bottom / density,
    )
  }
}
