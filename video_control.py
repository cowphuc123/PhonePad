"""Nối VideoStreamer (video.py) với kênh UDP của app Android.

App gửi 'W'+JSON mỗi 0,5 s khi muốn xem hình:
    {"w":1280,"h":720,"fps":60,"kbps":10000,"crop":{"mode":"full"|"left"|"right"|"top"|"bottom"}}
'w' = thôi xem. Quá 2 s không có 'W' -> dừng ffmpeg cho đỡ tốn GPU.
Chỉ một điện thoại xem hình một lúc: điện thoại xin sau sẽ lấy luồng.
"""
import asyncio
import concurrent.futures
import ctypes
import json
import shutil
import socket
import time

try:
    import video
except Exception:  # thiếu file / lỗi import: app vẫn dùng được nút bấm
    video = None

WATCH_TIMEOUT = 2.0
NO_FRAME_WARN = 6.0


def screen_size():
    """Kích thước màn hình chính theo pixel thật (ddagrab chụp pixel thật, không theo % phóng to)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def crop_and_size(req):
    """Vùng chụp (pixel) + cỡ khung ra giữ đúng tỉ lệ, nằm gọn trong w x h app xin."""
    sw, sh = screen_size()
    mode = (req.get("crop") or {}).get("mode", "full")
    rects = {
        "left": (0, 0, sw // 2, sh), "right": (sw // 2, 0, sw - sw // 2, sh),
        "top": (0, 0, sw, sh // 2), "bottom": (0, sh // 2, sw, sh - sh // 2),
    }
    crop = rects.get(mode)
    cw, ch = (crop[2], crop[3]) if crop else (sw, sh)
    bw, bh = int(req.get("w", 1280)), int(req.get("h", 720))
    s = min(bw / cw, bh / ch, 1.0)
    w, h = int(cw * s) // 2 * 2, int(ch * s) // 2 * 2
    return crop, w, h


class VideoControl:
    def __init__(self, udp):
        self.udp = udp
        udp.on_packet = self.on_packet
        # socket riêng để thread của VideoStreamer gửi thẳng (transport asyncio không an toàn đa luồng)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        except OSError:
            pass
        self.vs = video.VideoStreamer(self.sock.sendto, log=print) if video and self.ffmpeg_ok() else None
        self.pool = concurrent.futures.ThreadPoolExecutor(1)  # start/stop chặn tới 1-3 s: không chạy trong event loop
        self.addr = None
        self.key = None
        self.last = 0.0
        self.started = 0.0
        self.warned = False
        self.frames0 = 0
        asyncio.get_running_loop().create_task(self._watch())

    @staticmethod
    def ffmpeg_ok():
        return video.FFMPEG_DIR.joinpath("ffmpeg.exe").exists() or shutil.which("ffmpeg") is not None

    def info(self, msg, addr):
        self.udp.send(b"I" + json.dumps({"t": "videofail", "msg": msg}).encode(), addr)

    def on_packet(self, data, addr):
        t = data[0]
        if t == 0x57:  # 'W'
            try:
                req = json.loads(data[1:].decode("utf-8"))
            except ValueError:
                return
            if self.vs is None:
                if time.monotonic() - self.last > 5:
                    self.last = time.monotonic()
                    self.info("Máy tính chưa có ffmpeg: chạy tools\\fetch_ffmpeg.ps1 rồi mở lại start.bat", addr)
                return
            self.last = time.monotonic()
            key = json.dumps(req, sort_keys=True)
            if addr != self.addr or key != self.key:
                # điện thoại mới / đổi cài đặt: khởi động lại ffmpeg -> khung đầu là IDR, giải mã được ngay
                self.addr, self.key = addr, key
                self.started = time.monotonic()
                self.warned = False
                self.frames0 = self.vs.frames_sent
                crop, w, h = crop_and_size(req)
                print("[video] phát tới %s: %dx%d crop=%s" % (addr[0], w, h, crop))
                self.pool.submit(self.vs.start, addr, crop=crop, width=w, height=h,
                                 fps=int(req.get("fps", 60)), bitrate_kbps=int(req.get("kbps", 10000)))
        elif t == 0x77 and addr == self.addr:  # 'w'
            self.stop()
        # 'K' (xin IDR) bị bỏ qua: request_idr phải khởi động lại ffmpeg (~1 s hình đứng),
        # lâu hơn chờ IDR định kỳ (GOP 2 s).

    def stop(self):
        if self.addr is not None:
            print("[video] dừng phát")
        self.addr = None
        self.key = None
        if self.vs is not None:
            self.pool.submit(self.vs.stop)

    async def _watch(self):
        while True:
            await asyncio.sleep(0.5)
            if self.addr is None:
                continue
            now = time.monotonic()
            if now - self.last > WATCH_TIMEOUT:
                self.stop()
            elif (not self.warned and self.vs.frames_sent == self.frames0 and self.vs.last_error
                  and now - self.started > NO_FRAME_WARN):
                self.warned = True
                self.info("Không phát được hình: %s" % self.vs.last_error[-200:], self.addr)

    def close(self):
        if self.vs is not None:
            self.vs.stop()
        self.pool.shutdown(wait=False)
