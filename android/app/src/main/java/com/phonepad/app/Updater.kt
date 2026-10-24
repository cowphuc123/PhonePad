package com.phonepad.app

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Tự cập nhật từ GitHub Releases: lấy bản mới nhất, tải APK, kiểm tra SHA-256 (GitHub ghi trong
 * trường "digest" của file), rồi cài bằng PackageInstaller (Android hỏi người dùng xác nhận).
 * APK mới phải ký cùng khoá với bản đang cài, nếu không Android từ chối cài đè.
 */
object Updater {
    private const val API = "https://api.github.com/repos/cowphuc123/PhonePad/releases/latest"
    const val RELEASES = "https://github.com/cowphuc123/PhonePad/releases/latest"

    data class Info(val version: String, val url: String, val size: Long, val sha256: String?)

    /** "v0.10" -> [0, 10]; so sánh từng số. */
    private fun parse(v: String): List<Int> = Regex("\\d+").findAll(v).map { it.value.toInt() }.toList()

    fun newer(a: String, b: String): Boolean {
        val x = parse(a); val y = parse(b)
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }
            if (d != 0) return d > 0
        }
        return false
    }

    /** Chạy trên thread nền. Trả về bản mới hơn bản đang cài, hoặc null. */
    fun check(): Info? {
        val c = URL(API).openConnection() as HttpURLConnection
        c.connectTimeout = 5000
        c.readTimeout = 8000
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", USER_AGENT)
        val j = JSONObject(c.inputStream.use { it.readBytes().toString(Charsets.UTF_8) })
        val tag = j.getString("tag_name")
        if (!newer(tag, BuildConfig.VERSION_NAME)) return null
        val assets = j.getJSONArray("assets")
        for (i in 0 until assets.length()) {
            val a = assets.getJSONObject(i)
            if (a.getString("name").endsWith(".apk")) {
                val digest = a.optString("digest", "")
                return Info(tag.trimStart('v'), a.getString("browser_download_url"), a.getLong("size"),
                    if (digest.startsWith("sha256:")) digest.removePrefix("sha256:") else null)
            }
        }
        return null
    }

    /** Chạy trên thread nền. progress(đã tải, tổng) */
    fun download(ctx: Context, info: Info, progress: (Long, Long) -> Unit): File {
        val out = File(ctx.cacheDir, "update.apk")
        val c = URL(info.url).openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = 15000
        c.setRequestProperty("User-Agent", USER_AGENT)
        val md = MessageDigest.getInstance("SHA-256")
        var done = 0L
        c.inputStream.use { input ->
            out.outputStream().use { o ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    o.write(buf, 0, n)
                    md.update(buf, 0, n)
                    done += n
                    progress(done, info.size)
                }
            }
        }
        if (done != info.size) throw IllegalStateException("Tải thiếu ($done/${info.size} byte)")
        val hex = md.digest().joinToString("") { "%02x".format(it) }
        if (info.sha256 != null && !hex.equals(info.sha256, ignoreCase = true)) {
            out.delete()
            throw IllegalStateException("File tải về bị hỏng (SHA-256 không khớp)")
        }
        return out
    }

    /** Android 8+: app phải được phép "cài ứng dụng không rõ nguồn". Trả về false nếu đã mở màn hình cho phép. */
    fun ensureInstallAllowed(a: Activity): Boolean {
        if (Build.VERSION.SDK_INT < 26 || a.packageManager.canRequestPackageInstalls()) return true
        Toast.makeText(a, "Bật \"Cho phép từ nguồn này\" cho PhonePad rồi bấm Cập nhật lại", Toast.LENGTH_LONG).show()
        a.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + a.packageName)))
        return false
    }

    fun install(ctx: Context, apk: File) {
        val pi = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(ctx.packageName)
        val id = pi.createSession(params)
        pi.openSession(id).use { s ->
            s.openWrite("phonepad.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                s.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val pending = PendingIntent.getBroadcast(ctx, id,
                Intent(ctx, InstallResultReceiver::class.java), flags)
            s.commit(pending.intentSender)
        }
    }
}

/** Nhận kết quả cài: cần người dùng xác nhận thì mở hộp thoại của Android; lỗi thì báo. */
class InstallResultReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS -> {}
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "lỗi không rõ"
                Toast.makeText(ctx, "Cập nhật không được: $msg", Toast.LENGTH_LONG).show()
            }
        }
    }
}
