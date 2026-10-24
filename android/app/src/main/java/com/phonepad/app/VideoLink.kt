package com.phonepad.app

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.os.Process
import android.view.Surface
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException

/**
 * Nhận hình máy tính qua UDP và giải mã phần cứng thẳng ra Surface.
 *
 * Gói từ máy tính (little-endian, header 16 byte):
 *   u8 'V', u8 flags (bit0 = khung có IDR/SPS), u16 chỉ số gói, u16 tổng số gói, u16 dự trữ,
 *   u32 số khung, u32 thời điểm chụp (µs, đồng hồ máy tính), rồi dữ liệu H.264 Annex-B.
 * Gói gửi máy tính: 'W'+JSON (xin xem, lặp mỗi 0,5 s), 'w' (thôi xem).
 * Mất gói thì bỏ khung, hình tự lành ở khung IDR kế tiếp (máy tính phát IDR mỗi 2 s).
 */
class VideoLink(
    private val pc: PcInfo,
    private val surface: Surface,
    private val request: JSONObject,          // {"w":1280,"h":720,"fps":60,"kbps":10000,"crop":{...}}
    private val clockOffsetUs: () -> Long?,   // đồng hồ máy tính - đồng hồ điện thoại (từ InputLink)
    private val listener: Listener,
) {
    interface Listener {
        fun onVideoSize(w: Int, h: Int)
        fun onVideoStats(fps: Float, mbps: Float, lossPct: Float, latencyMs: Float)
        fun onInfo(msg: JSONObject)   // vd {"t":"videofail","msg":"..."} khi máy tính không phát được hình
    }

    private class Frame(val num: Long, val count: Int, val flags: Int, val capture: Long) {
        val parts = arrayOfNulls<ByteArray>(count)
        var got = 0
        var size = 0
    }

    private val sock = DatagramSocket(null).apply {
        receiveBufferSize = 2 shl 20 // đệm lớn để không rớt gói khi khung IDR to
        bind(null)
        soTimeout = 250
    }
    private val target = InetSocketAddress(pc.host, pc.udp)
    @Volatile private var running = false
    private lateinit var codec: MediaCodec

    // thống kê (đọc/ghi từ 2 luồng, chỉ dùng để hiển thị nên không cần chính xác tuyệt đối)
    @Volatile private var bytesIn = 0L
    @Volatile private var pktsIn = 0L
    @Volatile private var pktsLost = 0L

    fun start() {
        codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        val f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, request.optInt("w", 1280), request.optInt("h", 720))
        if (Build.VERSION.SDK_INT >= 30) f.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
        f.setInteger(MediaFormat.KEY_PRIORITY, 0) // ưu tiên thời gian thực
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 2 * 1024 * 1024) // đủ chỗ cho khung IDR lớn
        // Khoá riêng của từng hãng chip để tắt bộ đệm sắp xếp khung (giống Moonlight); máy không hiểu thì bỏ qua
        f.setInteger("vendor.qti-ext-dec-picture-order.enable", 1)
        f.setInteger("vendor.qti-ext-dec-low-latency.enable", 1)
        f.setInteger("vendor.rtc-ext-dec-low-latency.enable", 1)
        f.setInteger("vendor.hisi-ext-low-latency-video-dec.video-scene-for-low-latency-req", 1)
        f.setInteger("vendor.low-latency.enable", 1)
        codec.configure(f, surface, null, 0)
        codec.start()
        running = true
        rx.start()
        out.start()
    }

    fun stop() {
        running = false
        try { send(byteArrayOf('w'.code.toByte())) } catch (_: Exception) {}
        sock.close()
        rx.join(500)
        out.join(500)
        try { codec.stop() } catch (_: Exception) {}
        codec.release()
    }

    private fun send(b: ByteArray) = sock.send(DatagramPacket(b, b.size, target))

    private val rx = Thread({
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY)
        val buf = ByteArray(2048)
        val pk = DatagramPacket(buf, buf.size)
        val frames = LinkedHashMap<Long, Frame>()
        val ask = ("W" + request.toString()).toByteArray()
        var lastAsk = 0L
        var lastDone = -1L
        var waitKey = true
        while (running) {
            val now = System.nanoTime()
            if (now - lastAsk > 500_000_000L) { lastAsk = now; try { send(ask) } catch (_: Exception) {} }
            try {
                pk.length = buf.size
                sock.receive(pk)
            } catch (_: SocketTimeoutException) {
                continue
            } catch (_: Exception) {
                break
            }
            val n = pk.length
            if (n > 1 && buf[0] == 'I'.code.toByte()) {
                try { listener.onInfo(JSONObject(String(buf, 1, n - 1, Charsets.UTF_8))) } catch (_: Exception) {}
                continue
            }
            if (n <= 16 || buf[0] != 'V'.code.toByte()) continue
            pktsIn++
            bytesIn += n
            val flags = buf[1].toInt() and 0xFF
            val idx = u16(buf, 2)
            val count = u16(buf, 4)
            val num = u32(buf, 8)
            val cap = u32(buf, 12)
            if (count == 0 || idx >= count) continue
            if (lastDone >= 0 && num < lastDone - 600) {   // máy tính khởi động lại luồng hình
                frames.clear(); lastDone = -1; waitKey = true
            }
            if (num <= lastDone) continue                  // gói đến muộn của khung đã xong/đã bỏ
            var fr = frames[num]
            if (fr == null) {
                if (frames.size >= 3) { val old = frames.values.first(); frames.remove(old.num); pktsLost += old.count - old.got }
                fr = Frame(num, count, flags, cap)
                frames[num] = fr
            }
            if (fr.parts[idx] == null) {
                fr.parts[idx] = buf.copyOfRange(16, n)
                fr.got++
                fr.size += n - 16
            }
            if (fr.got < fr.count) continue
            // khung đủ gói: bỏ các khung cũ hơn còn thiếu, rồi giải mã
            val it = frames.values.iterator()
            while (it.hasNext()) {
                val o = it.next()
                if (o.num < num) { pktsLost += o.count - o.got; it.remove() }
            }
            frames.remove(num)
            lastDone = num
            if (waitKey) {
                if (fr.flags and 1 == 0) continue  // chưa có SPS/IDR thì chưa giải mã được
                waitKey = false
            }
            queue(fr)
        }
    }, "pp-video-rx")

    private fun queue(fr: Frame) {
        val i = try { codec.dequeueInputBuffer(5_000) } catch (_: Exception) { return }
        if (i < 0) return // bộ giải mã đầy: bỏ khung còn hơn để trễ dồn lại
        val b = codec.getInputBuffer(i) ?: return
        b.clear()
        if (b.remaining() < fr.size) { codec.queueInputBuffer(i, 0, 0, 0, 0); return }
        for (p in fr.parts) b.put(p!!)
        codec.queueInputBuffer(i, 0, fr.size, fr.capture, 0)
    }

    private val out = Thread({
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY)
        val info = MediaCodec.BufferInfo()
        var t0 = System.nanoTime()
        var frames = 0
        var latSum = 0.0
        var latN = 0
        var lastBytes = 0L
        var lastIn = 0L
        var lastLost = 0L
        while (running) {
            val i = try { codec.dequeueOutputBuffer(info, 20_000) } catch (_: Exception) { break }
            if (i >= 0) {
                clockOffsetUs()?.let { off ->
                    val nowPc = (System.nanoTime() / 1000 + off) and 0xFFFFFFFFL
                    val d = (nowPc - info.presentationTimeUs) and 0xFFFFFFFFL
                    if (d < 2_000_000L) { latSum += d / 1000.0; latN++ }
                }
                try { codec.releaseOutputBuffer(i, true) } catch (_: Exception) { break }
                frames++
            } else if (i == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                val f = codec.outputFormat
                var w = f.getInteger(MediaFormat.KEY_WIDTH)
                var h = f.getInteger(MediaFormat.KEY_HEIGHT)
                if (f.containsKey("crop-right") && f.containsKey("crop-left")) w = f.getInteger("crop-right") - f.getInteger("crop-left") + 1
                if (f.containsKey("crop-bottom") && f.containsKey("crop-top")) h = f.getInteger("crop-bottom") - f.getInteger("crop-top") + 1
                listener.onVideoSize(w, h)
            }
            val now = System.nanoTime()
            if (now - t0 >= 1_000_000_000L) {
                val sec = (now - t0) / 1e9f
                val inP = pktsIn - lastIn
                val lostP = pktsLost - lastLost
                listener.onVideoStats(
                    frames / sec,
                    (bytesIn - lastBytes) * 8 / sec / 1e6f,
                    if (inP + lostP > 0) 100f * lostP / (inP + lostP) else 0f,
                    if (latN > 0) (latSum / latN).toFloat() else -1f,
                )
                t0 = now; frames = 0; latSum = 0.0; latN = 0
                lastBytes = bytesIn; lastIn = pktsIn; lastLost = pktsLost
            }
        }
    }, "pp-video-out")

    private fun u16(b: ByteArray, o: Int) = (b[o].toInt() and 0xFF) or ((b[o + 1].toInt() and 0xFF) shl 8)
    private fun u32(b: ByteArray, o: Int): Long =
        (u16(b, o).toLong()) or (u16(b, o + 2).toLong() shl 16)
}
