package com.phonepad.app

import org.json.JSONObject

/** Một nút / joystick / D-pad. Cùng định dạng JSON với trình sửa web (static/index.html). */
class Control(
    val id: String,
    val type: String,          // "button" | "stick" | "dpad"
    val x: Float,              // tâm, % chiều ngang
    val y: Float,              // tâm, % chiều dọc
    val s: Float,              // kích thước, % cạnh ngắn màn hình (vmin)
    val act: String,           // hành động của nút, vd "k:KeyW", "g:A"
    val label: String,
    val shape: String,         // "circle" | "square" | "wide"
    val toggle: Boolean,
    val analog: String,        // "" | "L" | "R" (joystick analog Xbox)
    val dirs: Map<Char, String>,
)

/** video: cài đặt hình máy tính của hồ sơ, vd {"mode":"right"} – xem static/index.html. null = toàn màn hình. */
class Profile(val id: String, val name: String, val controls: List<Control>, val video: JSONObject?)

object Layouts {
    fun parse(json: String): List<Profile> {
        val arr = JSONObject(json).optJSONArray("profiles") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val p = arr.getJSONObject(i)
            val cs = p.optJSONArray("controls")
            val controls = if (cs == null) emptyList() else (0 until cs.length()).map { j ->
                val c = cs.getJSONObject(j)
                val d = c.optJSONObject("dirs")
                Control(
                    id = c.optString("id"),
                    type = c.optString("type", "button"),
                    x = c.optDouble("x", 50.0).toFloat(),
                    y = c.optDouble("y", 50.0).toFloat(),
                    s = c.optDouble("s", 14.0).toFloat(),
                    act = c.optString("act"),
                    label = c.optString("label"),
                    shape = c.optString("shape", "circle"),
                    toggle = c.optBoolean("toggle"),
                    analog = c.optString("analog"),
                    dirs = "udlr".associateWith { k -> d?.optString(k.toString()).orEmpty() },
                )
            }
            Profile(p.optString("id"), p.optString("name", "Bố cục"), controls, p.optJSONObject("video"))
        }
    }
}

/** Nhãn ngắn hiển thị trên nút – giống bảng ACT trong static/index.html. */
object Labels {
    private val map = HashMap<String, String>().apply {
        for (c in 'A'..'Z') put("k:Key$c", c.toString())
        for (c in '0'..'9') put("k:Digit$c", c.toString())
        listOf(
            "Space" to "Space", "Enter" to "Enter", "Escape" to "Esc", "Tab" to "Tab", "Backspace" to "⌫",
            "ShiftLeft" to "Shift", "ShiftRight" to "R-Shift", "ControlLeft" to "Ctrl", "ControlRight" to "R-Ctrl",
            "AltLeft" to "Alt", "AltRight" to "R-Alt", "CapsLock" to "Caps", "ArrowUp" to "↑", "ArrowDown" to "↓",
            "ArrowLeft" to "←", "ArrowRight" to "→", "Insert" to "Ins", "Delete" to "Del", "Home" to "Home",
            "End" to "End", "PageUp" to "PgUp", "PageDown" to "PgDn", "Minus" to "-", "Equal" to "=",
            "BracketLeft" to "[", "BracketRight" to "]", "Semicolon" to ";", "Quote" to "'", "Backquote" to "`",
            "Backslash" to "\\", "Comma" to ",", "Period" to ".", "Slash" to "/",
            "NumpadAdd" to "Num+", "NumpadSubtract" to "Num-", "NumpadMultiply" to "Num*",
            "NumpadDivide" to "Num/", "NumpadDecimal" to "Num.", "NumpadEnter" to "NumEnter",
        ).forEach { (k, v) -> put("k:$k", v) }
        for (i in 1..12) put("k:F$i", "F$i")
        for (i in 0..9) put("k:Numpad$i", "Num$i")
        put("m:left", "Chuột T"); put("m:right", "Chuột P"); put("m:middle", "Chuột G")
        listOf("A", "B", "X", "Y", "LB", "RB", "Back", "Start", "LS", "RS").forEach { put("g:$it", it) }
        put("g:Guide", "Xbox"); put("t:LT", "LT"); put("t:RT", "RT")
        put("g:DUp", "D↑"); put("g:DDown", "D↓"); put("g:DLeft", "D←"); put("g:DRight", "D→")
    }

    fun of(act: String): String = if (act.isEmpty()) "" else map[act] ?: act
}
