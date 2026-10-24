package com.phonepad.app

import android.os.Process
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.net.URL
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

const val DEFAULT_UDP_PORT = 8766

/** Một máy tính đang chạy PhonePad server. */
data class PcInfo(val host: String, val name: String, val http: Int, val udp: Int, val pad: Boolean)

object Discovery {
    private val ASK = "PPD?".toByteArray()

    /** Hỏi trên mọi mạng (Wi-Fi, chia sẻ qua USB...) và gom các máy trả lời. Chạy trên luồng nền. */
    fun scan(timeoutMs: Int = 1200, onlyHost: String? = null): List<PcInfo> {
        val found = LinkedHashMap<String, PcInfo>()
        DatagramSocket().use { s ->
            s.broadcast = true
            s.soTimeout = 150
            val targets = if (onlyHost != null) listOf(InetAddress.getByName(onlyHost)) else broadcastAddresses()
            val deadline = System.currentTimeMillis() + timeoutMs
            var nextAsk = 0L
            val buf = ByteArray(2048)
            while (System.currentTimeMillis() < deadline) {
                if (System.currentTimeMillis() >= nextAsk) {
                    nextAsk = System.currentTimeMillis() + 400
                    for (t in targets) try {
                        s.send(DatagramPacket(ASK, ASK.size, t, DEFAULT_UDP_PORT))
                    } catch (_: IOException) {
                    }
                }
                try {
                    val pk = DatagramPacket(buf, buf.size)
                    s.receive(pk)
                    if (pk.length > 4 && String(buf, 0, 4) == "PPD!") {
                        val j = JSONObject(String(buf, 4, pk.length - 4, Charsets.UTF_8))
                        val host = pk.address.hostAddress ?: continue
                        found[host] = PcInfo(host, j.optString("name", host), j.optInt("http", 8765),
                            j.optInt("udp", DEFAULT_UDP_PORT), j.optBoolean("pad"))
                        if (onlyHost != null) break
                    }
                } catch (_: SocketTimeoutException) {
                } catch (_: Exception) {
                }
            }
        }
        return found.values.toList()
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val out = LinkedHashSet<InetAddress>()
        out.add(InetAddress.getByName("255.255.255.255"))
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                for (ia in ni.interfaceAddresses) {
                    if (ia.address is Inet4Address) ia.broadcast?.let { out.add(it) }
                }
            }
        } catch (_: Exception) {
        }
        return out.toList()
    }
}

fun httpGet(pc: PcInfo, path: String): String {
    val c = URL("http://${pc.host}:${pc.http}$path").openConnection()
    c.connectTimeout = 2000
    c.readTimeout = 3000
    c.useCaches = false
    return c.getInputStream().use { it.readBytes().toString(Charsets.UTF_8) }
}

/**
 * Gửi trạng thái nút qua UDP. Mỗi gói mang đủ trạng thái (các phím đang giữ + 2 cần analog),
 * gửi ngay khi có thay đổi và lặp lại ~30 lần/giây -> mất gói không làm kẹt phím.
 */
class InputLink(private val pc: PcInfo, private val listener: Listener) {
    interface Listener {
        fun onRtt(ms: Float)
        fun onLink(ok: Boolean)
        fun onInfo(msg: JSONObject)
    }

    private val sock = DatagramSocket()
    private val target = InetSocketAddress(pc.host, pc.udp)
    private val lock = Any()
    private val counts = HashMap<String, Int>()
    private val axes = FloatArray(4)
    private var axisFlags = 0
    private val wake = ArrayBlockingQueue<Boolean>(1)
    @Volatile private var running = true
    @Volatile private var lastPongNs = System.nanoTime()

    /** Đồng hồ máy tính (µs, 32 bit) trừ đồng hồ điện thoại – lấy từ lần ping có RTT nhỏ nhất gần đây. */
    @Volatile var clockOffsetUs: Long? = null
        private set
    private val offsetSamples = ArrayDeque<Pair<Long, Long>>() // (rttUs, offsetUs)
    private var linkOk = true

    fun press(act: String) {
        if (act.isEmpty()) return
        synchronized(lock) { counts[act] = (counts[act] ?: 0) + 1 }
        wake.offer(true)
    }

    fun release(act: String) {
        if (act.isEmpty()) return
        synchronized(lock) {
            val n = counts[act] ?: return
            if (n <= 1) counts.remove(act) else counts[act] = n - 1
        }
        wake.offer(true)
    }

    fun setAxis(side: String, x: Float, y: Float) {
        synchronized(lock) {
            val i = if (side == "L") 0 else 2
            axes[i] = x; axes[i + 1] = y
            axisFlags = axisFlags or (if (side == "L") 1 else 2)
        }
        wake.offer(true)
    }

    fun clearAll() {
        synchronized(lock) { counts.clear(); axes.fill(0f) }
        wake.offer(true)
    }

    private val sender = Thread({
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY)
        val buf = ByteBuffer.allocate(1400).order(ByteOrder.LITTLE_ENDIAN)
        val ping = ByteBuffer.allocate(9).order(ByteOrder.LITTLE_ENDIAN)
        var seq = 0
        var lastPing = 0L
        while (running) {
            wake.poll(33, TimeUnit.MILLISECONDS)
            if (!running) break
            buf.clear()
            buf.put('S'.code.toByte())
            synchronized(lock) {
                buf.putInt(seq++)
                for (v in axes) buf.putShort((v.coerceIn(-1f, 1f) * 32767f).toInt().toShort())
                buf.put(axisFlags.toByte())
                var first = true
                for (a in counts.keys) {
                    val b = a.toByteArray(Charsets.UTF_8)
                    if (buf.remaining() < b.size + 1) break
                    if (!first) buf.put('\n'.code.toByte())
                    buf.put(b)
                    first = false
                }
            }
            send(buf.array(), buf.position())
            val now = System.nanoTime()
            if (now - lastPing > 500_000_000L) {
                lastPing = now
                ping.clear(); ping.put('P'.code.toByte()); ping.putLong(now)
                send(ping.array(), 9)
            }
            val ok = now - lastPongNs < 2_000_000_000L
            if (ok != linkOk) { linkOk = ok; listener.onLink(ok) }
        }
    }, "pp-input")

    private val receiver = Thread({
        val buf = ByteArray(2048)
        val pk = DatagramPacket(buf, buf.size)
        while (running) {
            try {
                pk.length = buf.size
                sock.receive(pk)
                when (buf[0].toInt().toChar()) {
                    'Q' -> if (pk.length >= 9) {
                        val sent = ByteBuffer.wrap(buf, 1, 8).order(ByteOrder.LITTLE_ENDIAN).long
                        val now = System.nanoTime()
                        lastPongNs = now
                        listener.onRtt((now - sent) / 1e6f)
                        if (pk.length >= 13) {
                            val serverUs = ByteBuffer.wrap(buf, 9, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
                            val midUs = (sent + now) / 2000
                            offsetSamples.addLast((now - sent) / 1000 to (serverUs - midUs))
                            if (offsetSamples.size > 20) offsetSamples.removeFirst()
                            clockOffsetUs = offsetSamples.minByOrNull { it.first }!!.second
                        }
                    }
                    'I' -> listener.onInfo(JSONObject(String(buf, 1, pk.length - 1, Charsets.UTF_8)))
                }
            } catch (_: Exception) {
                if (!running) break
            }
        }
    }, "pp-input-rx")

    private fun send(data: ByteArray, len: Int) {
        try {
            sock.send(DatagramPacket(data, len, target))
        } catch (_: IOException) {
        }
    }

    fun start() {
        sender.start()
        receiver.start()
    }

    /** Nhả hết phím, báo server ngắt kết nối, đóng socket. */
    fun stop() {
        running = false
        wake.offer(true)
        Thread {
            synchronized(lock) { counts.clear(); axes.fill(0f) }
            repeat(2) { send(byteArrayOf('X'.code.toByte()), 1) }
            sock.close()
        }.start()
    }
}
