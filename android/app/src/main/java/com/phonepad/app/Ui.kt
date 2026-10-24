package com.phonepad.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.TextView

const val BG = 0xFF111318.toInt()
const val ACCENT = 0xFF3D8BFF.toInt()
const val MUTED = 0xFF8A90A0.toInt()

fun Context.dp(v: Number): Int =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

/** Nút dạng viên thuốc, giống thanh trên của bản web. */
fun Context.pill(text: String, primary: Boolean = false, onClick: ((View) -> Unit)? = null): TextView =
    TextView(this).apply {
        this.text = text
        setTextColor(Color.WHITE)
        textSize = 14f
        gravity = Gravity.CENTER
        minHeight = dp(34)
        setPadding(dp(14), 0, dp(14), 0)
        background = GradientDrawable().apply {
            cornerRadius = dp(17).toFloat()
            setColor(if (primary) ACCENT else 0xD9282C38.toInt())
            setStroke(dp(1), 0x1FFFFFFF)
        }
        if (onClick != null) setOnClickListener(onClick)
    }

fun Activity.goImmersive() {
    if (Build.VERSION.SDK_INT >= 30) {
        window.setDecorFitsSystemWindows(false)
        window.insetsController?.let {
            it.hide(WindowInsets.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    } else {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            or View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
    }
}

fun Intent.putPc(pc: PcInfo): Intent = putExtra("host", pc.host).putExtra("name", pc.name)
    .putExtra("http", pc.http).putExtra("udp", pc.udp).putExtra("pad", pc.pad)

fun Intent.getPc(): PcInfo = PcInfo(
    getStringExtra("host") ?: "", getStringExtra("name") ?: "", getIntExtra("http", 8765),
    getIntExtra("udp", DEFAULT_UDP_PORT), getBooleanExtra("pad", false),
)
