package com.phonepad.app

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import kotlin.math.min

/** Màn hình chơi: hình máy tính (tuỳ chọn) + các nút điều khiển + thanh nhỏ phía trên. */
class PlayActivity : Activity(), InputLink.Listener, VideoLink.Listener {
    private lateinit var pc: PcInfo
    private lateinit var root: FrameLayout
    private lateinit var surfaceView: SurfaceView
    private lateinit var controls: ControlsView
    private lateinit var statusPill: TextView
    private lateinit var profilePill: TextView
    private lateinit var videoPill: TextView
    private var link: InputLink? = null
    private var video: VideoLink? = null
    private var videoReq: String? = null
    private var surfaceReady = false
    private var wifiLock: WifiManager.WifiLock? = null
    private var profiles: List<Profile> = emptyList()
    private var rtt = -1f
    private var linkOk = true
    private var videoStats = ""
    private var openedEditorForDefaults = false
    private val prefs by lazy { getSharedPreferences("pp", MODE_PRIVATE) }
    private var videoOn: Boolean
        get() = prefs.getBoolean("video_on", false)
        set(v) = prefs.edit().putBoolean("video_on", v).apply()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pc = intent.getPc()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (getSystemService(PowerManager::class.java)?.isSustainedPerformanceModeSupported == true) {
            window.setSustainedPerformanceMode(true) // giữ xung CPU ổn định, tránh giật khi máy nóng
        }

        root = FrameLayout(this).apply { setBackgroundColor(BG) }
        surfaceView = SurfaceView(this)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(h: SurfaceHolder) { surfaceReady = true; startVideo() }
            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, ht: Int) {}
            override fun surfaceDestroyed(h: SurfaceHolder) { surfaceReady = false; stopVideo() }
        })
        root.addView(surfaceView, FrameLayout.LayoutParams(-1, -1, Gravity.CENTER))
        controls = ControlsView(this).apply { vibrate = prefs.getBoolean("vibrate", true) }
        root.addView(controls, FrameLayout.LayoutParams(-1, -1))

        val bar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        fun gap() = LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(6) }
        statusPill = pill("● …")
        profilePill = pill("Bố cục ▾") { showMenu() }
        videoPill = pill("📺") { setVideo(!videoOn) }
        bar.addView(statusPill)
        bar.addView(profilePill, gap())
        bar.addView(videoPill, gap())
        bar.addView(pill("✎ Sửa") { openEditor() }, gap())
        bar.addView(pill("⏏") { finish() }, gap())
        root.addView(bar, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply { topMargin = dp(6) })
        setContentView(root)
        applyVideoUi()
    }

    override fun onStart() {
        super.onStart()
        goImmersive()
        link = InputLink(pc, this).also { it.start(); controls.input = it }
        val wm = applicationContext.getSystemService(WifiManager::class.java)
        @Suppress("DEPRECATION")
        val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
        wifiLock = wm?.createWifiLock(mode, "phonepad")?.apply { setReferenceCounted(false); acquire() }
        loadLayouts()
    }

    override fun onStop() {
        super.onStop()
        stopVideo()
        controls.releaseAll()
        controls.input = null
        link?.stop()
        link = null
        wifiLock?.release()
        wifiLock = null
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goImmersive() else controls.releaseAll()
    }

    // ---------- bố cục ----------
    private fun loadLayouts() {
        Thread {
            val r = try { Layouts.parse(httpGet(pc, "/api/layouts")) } catch (e: Exception) { null }
            runOnUiThread {
                when {
                    r == null -> Toast.makeText(this, "Không tải được bố cục từ máy tính", Toast.LENGTH_LONG).show()
                    r.isEmpty() && !openedEditorForDefaults -> {
                        // Máy tính chưa có layouts.json: mở trình sửa, nó sẽ tạo và lưu bố cục mẫu.
                        openedEditorForDefaults = true
                        Toast.makeText(this, "Đang tạo bố cục mẫu…", Toast.LENGTH_SHORT).show()
                        openEditor()
                    }
                    else -> {
                        profiles = r
                        val cur = prefs.getString("cur_profile", null)
                        selectProfile(r.firstOrNull { it.id == cur } ?: r.firstOrNull())
                    }
                }
            }
        }.start()
    }

    private fun selectProfile(p: Profile?) {
        controls.profile = p
        profilePill.text = (p?.name ?: "Chưa có bố cục") + " ▾"
        if (p != null) prefs.edit().putString("cur_profile", p.id).apply()
        if (video != null && videoRequest().toString() != videoReq) { stopVideo(); startVideo() } // vùng hình đổi theo hồ sơ
    }

    private fun showMenu() {
        val m = PopupMenu(this, profilePill)
        profiles.forEachIndexed { i, p -> m.menu.add(0, i, i, p.name) }
        val hd = prefs.getBoolean("video_1080", false)
        m.menu.add(1, 1001, 1001, if (hd) "Chất lượng hình: 1080p (đổi sang 720p)" else "Chất lượng hình: 720p (đổi sang 1080p)")
        m.menu.add(1, 1000, 1000, if (controls.vibrate) "Tắt rung" else "Bật rung")
        m.setOnMenuItemClickListener {
            when (it.itemId) {
                1000 -> {
                    controls.vibrate = !controls.vibrate
                    prefs.edit().putBoolean("vibrate", controls.vibrate).apply()
                }
                1001 -> {
                    prefs.edit().putBoolean("video_1080", !hd).apply()
                    if (video != null) { stopVideo(); startVideo() }
                }
                else -> selectProfile(profiles[it.itemId])
            }
            true
        }
        m.show()
    }

    private fun openEditor() {
        controls.releaseAll()
        startActivity(Intent(this, EditActivity::class.java).putPc(pc))
    }

    // ---------- hình máy tính ----------
    private fun videoRequest(): JSONObject {
        val hd = prefs.getBoolean("video_1080", false)
        return JSONObject()
            .put("w", if (hd) 1920 else 1280).put("h", if (hd) 1080 else 720)
            .put("fps", 60).put("kbps", if (hd) 18000 else 10000)
            .put("crop", controls.profile?.video ?: JSONObject().put("mode", "full"))
    }

    private fun setVideo(on: Boolean) {
        videoOn = on
        applyVideoUi()
    }

    private fun applyVideoUi() {
        val on = videoOn
        surfaceView.visibility = if (on) View.VISIBLE else View.GONE // ẩn -> Surface bị huỷ -> tự dừng luồng hình
        root.setBackgroundColor(if (on) Color.BLACK else BG)
        controls.overVideo = on
        videoPill.alpha = if (on) 1f else 0.55f
        if (!on) { videoStats = ""; updateStatus() }
    }

    private fun startVideo() {
        if (!videoOn || !surfaceReady || video != null) return
        val req = videoRequest()
        videoReq = req.toString()
        video = try {
            VideoLink(pc, surfaceView.holder.surface, req, { link?.clockOffsetUs }, this).also { it.start() }
        } catch (e: Exception) {
            Toast.makeText(this, "Không mở được bộ giải mã hình: ${e.message}", Toast.LENGTH_LONG).show()
            null
        }
    }

    private fun stopVideo() {
        video?.stop()
        video = null
    }

    override fun onVideoSize(w: Int, h: Int) = runOnUiThread {
        // giữ đúng tỉ lệ khung hình, căn giữa (viền đen hai bên trên điện thoại màn dài)
        val s = min(root.width / w.toFloat(), root.height / h.toFloat())
        surfaceView.layoutParams = FrameLayout.LayoutParams((w * s).toInt(), (h * s).toInt(), Gravity.CENTER)
    }

    override fun onVideoStats(fps: Float, mbps: Float, lossPct: Float, latencyMs: Float) = runOnUiThread {
        videoStats = buildString {
            append(" · 📺 ${fps.toInt()}fps")
            if (latencyMs >= 0) append(" ${latencyMs.toInt()}ms")
            if (lossPct >= 0.5f) append(" mất ${"%.1f".format(lossPct)}%")
            if (fps < 1f) append(" (chờ hình…)")
        }
        updateStatus()
    }

    // ---------- trạng thái ----------
    private fun updateStatus() {
        statusPill.text = when {
            !linkOk -> "● mất kết nối"
            rtt < 0 -> "● …"
            else -> "● ${rtt.toInt()} ms$videoStats"
        }
        statusPill.setTextColor(if (linkOk && rtt >= 0) 0xFF3DDC84.toInt() else 0xFFFF5C5C.toInt())
    }

    // ---- InputLink.Listener (gọi từ luồng mạng) ----
    override fun onRtt(ms: Float) = runOnUiThread {
        rtt = if (rtt < 0) ms else rtt * 0.7f + ms * 0.3f
        updateStatus()
    }

    override fun onLink(ok: Boolean) = runOnUiThread {
        linkOk = ok
        if (!ok) rtt = -1f
        updateStatus()
    }

    override fun onInfo(msg: JSONObject) = runOnUiThread {
        if (msg.optString("t") in setOf("padfail", "videofail")) Toast.makeText(this, msg.optString("msg"), Toast.LENGTH_LONG).show()
    }
}
