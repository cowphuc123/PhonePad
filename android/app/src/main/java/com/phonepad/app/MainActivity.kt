package com.phonepad.app

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Màn hình kết nối: tự tìm máy tính chạy PhonePad trong mạng, hoặc nhập IP. */
class MainActivity : Activity() {
    private lateinit var list: LinearLayout
    private lateinit var status: TextView
    private lateinit var ipEdit: EditText
    private val prefs by lazy { getSharedPreferences("pp", MODE_PRIVATE) }
    @Volatile private var scanning = false
    private var autoTried = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        autoTried = savedInstanceState != null
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(20), dp(24), dp(20))
        }
        col.addView(TextView(this).apply {
            text = "PhonePad"; textSize = 26f; setTextColor(Color.WHITE); paint.isFakeBoldText = true
        })
        status = TextView(this).apply { textSize = 14f; setTextColor(MUTED); text = "Đang tìm máy tính trong mạng…" }
        col.addView(status, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4); bottomMargin = dp(12) })
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(list)

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        ipEdit = EditText(this).apply {
            hint = "IP máy tính, vd 192.168.1.3"
            setText(prefs.getString("last_host", ""))
            setTextColor(Color.WHITE); setHintTextColor(MUTED)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_GO
            setSingleLine()
            setOnEditorActionListener { _, _, _ -> connectManual(); true }
        }
        row.addView(ipEdit, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(pill("Kết nối", primary = true) { connectManual() },
            LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
        col.addView(row, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(16) })

        col.addView(TextView(this).apply {
            textSize = 13f; setTextColor(MUTED)
            text = "• Trên máy tính chạy start.bat.\n" +
                "• Điện thoại và máy tính dùng chung Wi-Fi, hoặc bật Chia sẻ Internet qua USB (trễ thấp nhất).\n" +
                "• Không tìm thấy? Kiểm tra tường lửa Windows đã cho phép Python, rồi nhập IP hiện trên trang mã QR."
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(16) })

        setContentView(ScrollView(this).apply { addView(col); setBackgroundColor(BG) })
    }

    override fun onResume() {
        super.onResume()
        scanning = true
        Thread {
            while (scanning) {
                val found = try { Discovery.scan(1200) } catch (_: Exception) { emptyList() }
                if (!scanning) break
                runOnUiThread { show(found) }
                Thread.sleep(800)
            }
        }.start()
    }

    override fun onPause() {
        super.onPause()
        scanning = false
    }

    private fun show(found: List<PcInfo>) {
        list.removeAllViews()
        status.text = if (found.isEmpty()) "Đang tìm máy tính trong mạng…" else "Chạm vào máy tính để kết nối:"
        for (pc in found) {
            list.addView(pill("🖥  ${pc.name}   ${pc.host}") { open(pc) },
                LinearLayout.LayoutParams(-1, dp(48)).apply { bottomMargin = dp(8) })
        }
        val last = prefs.getString("last_host", null)
        if (!autoTried && last != null) {
            found.firstOrNull { it.host == last }?.let { autoTried = true; open(it) }
        }
    }

    private fun connectManual() {
        val ip = ipEdit.text.toString().trim()
        if (ip.isEmpty()) return
        status.text = "Đang thử kết nối tới $ip…"
        Thread {
            val r = try { Discovery.scan(1500, onlyHost = ip) } catch (_: Exception) { emptyList() }
            runOnUiThread {
                if (r.isEmpty()) {
                    status.text = "Không thấy PhonePad tại $ip"
                    Toast.makeText(this, "Không kết nối được $ip — máy tính đã chạy start.bat chưa? Tường lửa?", Toast.LENGTH_LONG).show()
                } else open(r[0])
            }
        }.start()
    }

    private fun open(pc: PcInfo) {
        autoTried = true
        prefs.edit().putString("last_host", pc.host).apply()
        startActivity(Intent(this, PlayActivity::class.java).putPc(pc))
    }
}
