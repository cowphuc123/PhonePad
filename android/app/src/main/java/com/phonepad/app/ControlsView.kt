package com.phonepad.app

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.MotionEvent
import android.view.View
import kotlin.math.hypot
import kotlin.math.min

/**
 * Vẽ bố cục và xử lý cảm ứng đa điểm. Logic giống static/index.html:
 * nút giữ / bật-tắt, joystick 8 hướng (4 phím), joystick analog, D-pad.
 */
@SuppressLint("ViewConstructor")
class ControlsView(ctx: Context) : View(ctx) {

    /** Kết nối hiện tại (tạo lại mỗi lần app quay lại màn hình). */
    var input: InputLink? = null

    var profile: Profile? = null
        set(v) { releaseAll(); field = v; invalidate() }
    /** true khi có hình máy tính phía sau: nút trong hơn để nhìn rõ hình. */
    var overVideo = false
        set(v) { field = v; invalidate() }
    var vibrate = true

    private val vib = ctx.getSystemService(Vibrator::class.java)
    private val dp = resources.displayMetrics.density

    private class Touch(val c: Control) {
        var dirs: Set<Char> = emptySet()
        var kx = 0f
        var ky = 0f
    }

    private val touches = HashMap<Int, Touch>()      // pointerId -> ngón tay đang giữ
    private val toggled = HashSet<String>()          // id nút bật/tắt đang bật

    // ---------- hình học ----------
    private fun unit() = min(width, height).toFloat()
    private fun sizePx(c: Control) = c.s / 100f * unit()
    private fun cx(c: Control) = c.x / 100f * width
    private fun cy(c: Control) = c.y / 100f * height

    private fun bounds(c: Control, out: RectF): RectF {
        val s = sizePx(c)
        var w = s
        var h = s
        if (c.type == "button" && c.shape == "wide") { w = s * 1.7f; h = s * 0.72f }
        out.set(cx(c) - w / 2, cy(c) - h / 2, cx(c) + w / 2, cy(c) + h / 2)
        return out
    }

    private val tmp = RectF()
    private fun hit(x: Float, y: Float): Control? {
        val cs = profile?.controls ?: return null
        // duyệt ngược: nút vẽ sau nằm trên
        for (i in cs.indices.reversed()) {
            val c = cs[i]
            bounds(c, tmp)
            val slack = 0.08f * tmp.width()
            tmp.inset(-slack, -slack)
            if (!tmp.contains(x, y)) continue
            if (c.type == "button" && c.shape != "circle") return c
            val r = tmp.width() / 2
            if (hypot(x - tmp.centerX(), y - tmp.centerY()) <= r * if (c.type == "dpad") 1.42f else 1f) return c
        }
        return null
    }

    // ---------- cảm ứng ----------
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                if (e.actionMasked == MotionEvent.ACTION_DOWN) requestUnbufferedDispatch(e) // nhận MOVE ngay, không chờ vsync
                val i = e.actionIndex
                val c = hit(e.getX(i), e.getY(i)) ?: return true
                start(e.getPointerId(i), c, e.getX(i), e.getY(i))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until e.pointerCount) {
                    val t = touches[e.getPointerId(i)] ?: continue
                    if (t.c.type != "button") move(t, e.getX(i), e.getY(i))
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> end(e.getPointerId(e.actionIndex))
            MotionEvent.ACTION_CANCEL -> releaseAll()
        }
        return true
    }

    private fun start(pid: Int, c: Control, x: Float, y: Float) {
        if (c.type == "button") {
            if (c.toggle) {
                if (toggled.remove(c.id)) input?.release(c.act) else { toggled.add(c.id); input?.press(c.act) }
            } else {
                touches[pid] = Touch(c)
                input?.press(c.act)
            }
            buzz()
            invalidate()
            return
        }
        val t = Touch(c)
        touches[pid] = t
        move(t, x, y)
    }

    private fun move(t: Touch, x: Float, y: Float) {
        val c = t.c
        val rad = sizePx(c) / 2
        var dx = (x - cx(c)) / rad
        var dy = (y - cy(c)) / rad
        val m = hypot(dx, dy)
        if (m > 1f) { dx /= m; dy /= m }
        t.kx = dx; t.ky = dy
        if (c.type == "stick" && c.analog.isNotEmpty()) {
            input?.setAxis(c.analog, dx, dy)
            invalidate()
            return
        }
        val now = dirSet(dx, dy, if (c.type == "dpad") 0.2f else 0.3f)
        if (now != t.dirs) {
            for (k in t.dirs) if (k !in now) input?.release(c.dirs[k].orEmpty())
            var added = false
            for (k in now) if (k !in t.dirs) { input?.press(c.dirs[k].orEmpty()); added = true }
            if (added) buzz()
            t.dirs = now
        }
        invalidate()
    }

    private fun end(pid: Int) {
        val t = touches.remove(pid) ?: return
        val c = t.c
        when {
            c.type == "button" -> input?.release(c.act)
            c.analog.isNotEmpty() -> input?.setAxis(c.analog, 0f, 0f)
            else -> for (k in t.dirs) input?.release(c.dirs[k].orEmpty())
        }
        invalidate()
    }

    fun releaseAll() {
        touches.clear()
        toggled.clear()
        input?.clearAll()
        invalidate()
    }

    /** 8 hướng, mỗi hướng 45°: sin(22,5°) ≈ 0,383. */
    private fun dirSet(dx: Float, dy: Float, dead: Float): Set<Char> {
        val m = hypot(dx, dy)
        if (m < dead) return emptySet()
        val nx = dx / m
        val ny = dy / m
        val s = HashSet<Char>(2)
        if (nx > 0.383f) s.add('r')
        if (nx < -0.383f) s.add('l')
        if (ny > 0.383f) s.add('d')
        if (ny < -0.383f) s.add('u')
        return s
    }

    private fun buzz() {
        if (vibrate) vib?.vibrate(VibrationEffect.createOneShot(8, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    // ---------- vẽ ----------
    private val ON = Color.rgb(0x3D, 0x8B, 0xFF)
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }

    private fun setPaints(on: Boolean) {
        stroke.strokeWidth = 2 * dp
        if (on) {
            fill.color = Color.argb(if (overVideo) 120 else 97, 0x3D, 0x8B, 0xFF)
            stroke.color = ON
        } else {
            fill.color = Color.argb(if (overVideo) 40 else 18, 255, 255, 255)
            stroke.color = Color.argb(if (overVideo) 150 else 72, 255, 255, 255)
        }
    }

    private fun drawText(canvas: Canvas, s: String, x: Float, y: Float, size: Float, color: Int) {
        text.textSize = size
        text.color = color
        if (overVideo) text.setShadowLayer(3 * dp, 0f, 0f, Color.BLACK) else text.clearShadowLayer()
        canvas.drawText(s, x, y - (text.descent() + text.ascent()) / 2, text)
    }

    override fun onDraw(canvas: Canvas) {
        val p = profile ?: return
        val active = HashMap<String, Touch>()
        for (t in touches.values) active[t.c.id] = t
        for (c in p.controls) {
            val t = active[c.id]
            when (c.type) {
                "button" -> drawButton(canvas, c, t != null || c.id in toggled)
                "stick" -> drawStick(canvas, c, t)
                else -> drawDpad(canvas, c, t)
            }
        }
    }

    private fun drawButton(canvas: Canvas, c: Control, on: Boolean) {
        val r = bounds(c, tmp)
        setPaints(on)
        val s = sizePx(c)
        when (c.shape) {
            "circle" -> {
                canvas.drawCircle(r.centerX(), r.centerY(), s / 2, fill)
                canvas.drawCircle(r.centerX(), r.centerY(), s / 2, stroke)
            }
            "wide" -> {
                canvas.drawRoundRect(r, s * 0.2f, s * 0.2f, fill)
                canvas.drawRoundRect(r, s * 0.2f, s * 0.2f, stroke)
            }
            else -> {
                canvas.drawRoundRect(r, s * 0.18f, s * 0.18f, fill)
                canvas.drawRoundRect(r, s * 0.18f, s * 0.18f, stroke)
            }
        }
        val label = c.label.ifEmpty { Labels.of(c.act) }
        var ts = s * if (c.shape == "wide") 0.24f else 0.3f
        if (label.length > 4) ts = s * 0.2f
        drawText(canvas, label, r.centerX(), r.centerY(), ts, Color.WHITE)
    }

    private fun drawStick(canvas: Canvas, c: Control, t: Touch?) {
        val s = sizePx(c)
        val x = cx(c)
        val y = cy(c)
        setPaints(false)
        canvas.drawCircle(x, y, s / 2, fill)
        canvas.drawCircle(x, y, s / 2, stroke)
        setPaints(t != null)
        val kx = x + (t?.kx ?: 0f) * s / 2 * 0.6f
        val ky = y + (t?.ky ?: 0f) * s / 2 * 0.6f
        if (t == null) fill.color = Color.argb(if (overVideo) 90 else 56, 255, 255, 255)
        canvas.drawCircle(kx, ky, s * 0.21f, fill)
        canvas.drawCircle(kx, ky, s * 0.21f, stroke)
        val muted = Color.argb(if (overVideo) 230 else 150, 0xC0, 0xC4, 0xD0)
        if (c.analog.isNotEmpty()) {
            drawText(canvas, c.analog, kx, ky, s * 0.11f, muted)
            return
        }
        val off = s * 0.39f
        for ((k, dx, dy) in listOf(Triple('u', 0f, -off), Triple('d', 0f, off), Triple('l', -off, 0f), Triple('r', off, 0f))) {
            val on = t != null && k in t.dirs
            drawText(canvas, Labels.of(c.dirs[k].orEmpty()), x + dx, y + dy, s * 0.11f, if (on) ON else muted)
        }
    }

    private fun drawDpad(canvas: Canvas, c: Control, t: Touch?) {
        val s = sizePx(c)
        val left = cx(c) - s / 2
        val top = cy(c) - s / 2
        val a = s * 0.34f
        val arms = listOf('u' to (s * 0.33f to 0f), 'd' to (s * 0.33f to s - a), 'l' to (0f to s * 0.33f), 'r' to (s - a to s * 0.33f))
        for ((k, pos) in arms) {
            val on = t != null && k in t.dirs
            setPaints(on)
            tmp.set(left + pos.first, top + pos.second, left + pos.first + a, top + pos.second + a)
            canvas.drawRoundRect(tmp, a * 0.16f, a * 0.16f, fill)
            canvas.drawRoundRect(tmp, a * 0.16f, a * 0.16f, stroke)
            val lab = Labels.of(c.dirs[k].orEmpty()).replace(Regex("^D(?=[↑↓←→])"), "")
            drawText(canvas, lab, tmp.centerX(), tmp.centerY(), s * 0.11f, Color.WHITE)
        }
    }
}
