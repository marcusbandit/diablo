package com.marcusrosado.diablo.data

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build

/** The retired Diablo Fleet app. This app replaces it, so it offers to remove it once. */
object Fleet {
    const val PACKAGE = "com.marcusrosado.diablofleet"
    const val DISMISSED = "fleet-dismissed"

    fun installed(context: Context): Boolean = try {
        if (Build.VERSION.SDK_INT >= 33) {
            context.packageManager.getPackageInfo(PACKAGE, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            context.packageManager.getPackageInfo(PACKAGE, 0)
        }
        true
    } catch (error: PackageManager.NameNotFoundException) {
        false
    }

    fun uninstall(context: Context) {
        context.startActivity(
            Intent(Intent.ACTION_DELETE, Uri.parse("package:$PACKAGE")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
