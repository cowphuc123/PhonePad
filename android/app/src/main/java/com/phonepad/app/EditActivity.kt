package com.phonepad.app

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient

/** Sửa bố cục bằng chính trình sửa web của máy tính (http://<pc>:8765/?edit=1). */
class EditActivity : Activity() {
    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pc = intent.getPc()
        web = WebView(this).apply {
            setBackgroundColor(BG)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.userAgentString = settings.userAgentString + " " + USER_AGENT
            webViewClient = WebViewClient()
            // Nút "✓ Xong" trên trang gọi PhonePadApp.close() sau khi đã lưu
            addJavascriptInterface(object {
                @JavascriptInterface
                fun close() = runOnUiThread { finish() }
            }, "PhonePadApp")
            loadUrl("http://${pc.host}:${pc.http}/?edit=1")
        }
        setContentView(web)
    }

    override fun onStart() {
        super.onStart()
        goImmersive()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }
}
