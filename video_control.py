"""Nối VideoStreamer (video.py) với kênh UDP của app Android.

App gửi 'W'+JSON mỗi 0,5 s khi muốn xem hình:
    {"w":1280,"h":720,"fps":60,"kbps":10000,"crop":{"mode":"full"|"left"|"right"|"top"|"bottom"}}
'w' = thôi xem. Quá 2 s không có 'W' -> dừng ffmpeg cho đỡ tốn GPU.
Chỉ một điện thoại xem hình một lúc: điện thoại xin sau sẽ lấy luồng.
Máy tính có thể chọn chỉ phát một cửa sổ (VideoControl.target, chọn trên cửa sổ PhonePad): chụp riêng
cửa sổ đó bằng Windows.Graphics.Capture (gfxcapture, ffmpeg 9) nên cửa sổ khác đè lên hay chuyển sang
app khác vẫn chỉ thấy app đã chọn. Thu nhỏ xuống taskbar thì Windows ngừng vẽ nó: điện thoại giữ hình
cuối, mở lại là chạy tiếp. Game tắt rồi mở lại: tự tìm lại theo tên tiến trình.
"""
import asyncio
import concurrent.futures
import ctypes
import json
import socket
import time

try:
    import video
except Exception:  # thiếu file / lỗi import: app vẫn dùng được nút bấm
    video = None
try:
    import winlist
except Exception:
    winlist = None

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


def _fit(req, cw, ch):
    """Cỡ khung ra giữ tỉ lệ cw:ch, nằm gọn trong w x h app xin, không phóng to."""
    bw, bh = int(req.get("w", 1280)), int(req.get("h", 720))
    s = min(bw / cw, bh / ch, 1.0)
    return max(2, int(cw * s) // 2 * 2), max(2, int(ch * s) // 2 * 2)


def crop_and_size(req):
    """Phát cả màn hình: vùng chụp (pixel) theo chế độ nửa trái/phải/trên/dưới của hồ sơ + cỡ khung ra."""
    sw, sh = screen_size()
    mode = (req.get("crop") or {}).get("mode", "full")
    rects = {
        "left": (0, 0, sw // 2, sh), "right": (sw // 2, 0, sw - sw // 2, sh),
        "top": (0, 0, sw, sh // 2), "bottom": (0, sh // 2, sw, sh - sh // 2),
    }
    crop = rects.get(mode)
    cw, ch = (crop[2], crop[3]) if crop else (sw, sh)
    w, h = _fit(req, cw, ch)
    return crop, w, h


def window_crop_and_size(req, cw, ch):
    """Phát một cửa sổ cỡ nội dung cw x ch: số pixel cắt (trái, trên, phải, dưới) theo chế độ của hồ sơ
    (game chia đôi màn hình chạy trong cửa sổ) + cỡ khung ra."""
    mode = (req.get("crop") or {}).get("mode", "full")
    crops = {
        "left": (0, 0, cw - cw // 2, 0), "right": (cw // 2, 0, 0, 0),
        "top": (0, 0, 0, ch - ch // 2), "bottom": (0, ch // 2, 0, 0),
    }
    l, t, r, b = crops.get(mode, (0, 0, 0, 0))
    w, h = _fit(req, cw - l - r, ch - t - b)
    return (l, t, r, b), w, h


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
        self.vs = None  # tạo khi điện thoại xin hình lần đầu (ffmpeg có thể vừa được tải về)
        self.pool = concurrent.futures.ThreadPoolExecutor(1)  # start/stop chặn tới 1-3 s: không chạy trong event loop
        self.addr = None
        self.key = None
        self.last = 0.0
        self.started = 0.0
        self.warned = False
        self.frames0 = 0
        # cửa sổ PhonePad đặt: {"hwnd", "title", "exe"} = chỉ phát cửa sổ đó; None = cả màn hình
        self.target = None
        self.target_state = ""   # "", "ok", "minimized", "closed" - để cửa sổ PhonePad hiển thị
        self._last_size = None   # cỡ nội dung lần cuối thấy (khi đang thu nhỏ thì không đo được)
        self._info_at = 0.0
        asyncio.get_running_loop().create_task(self._watch())

    def window_target(self):
        """(hwnd, (cw, ch) hoặc None) của cửa sổ đang chọn; None nếu không chọn hoặc đã đóng."""
        t = self.target
        if t is None or winlist is None:
            self.target_state = ""
            return None
        hwnd = t["hwnd"]
        if not winlist.alive(hwnd):
            found = winlist.find(t["exe"], t["title"])  # game tắt rồi mở lại: hwnd mới
            if not found:
                self.target_state = "closed"
                return None
            hwnd = found[0]
            self.target = dict(t, hwnd=hwnd, title=found[1])
            self._last_size = None
        if winlist.minimized(hwnd):
            self.target_state = "minimized"
            return hwnd, self._last_size
        r = winlist.client_rect(hwnd)
        if r and r[2] >= 16 and r[3] >= 16:
            self._last_size = (r[2], r[3])
        self.target_state = "ok"
        return hwnd, self._last_size

    @staticmethod
    def ffmpeg_ok(kind="screen"):
        return video is not None and video.ffmpeg_path(kind) is not None

    @property
    def watching(self):
        """(IP điện thoại đang xem hình hoặc None, đường nén đang chạy hoặc None)."""
        return (self.addr[0] if self.addr else None), (self.vs.pipeline if self.vs else None)

    def info(self, msg, addr):
        self.udp.send(b"I" + json.dumps({"t": "videofail", "msg": msg}).encode(), addr)

    def info_once(self, msg, addr, every=5.0):
        """Báo cho điện thoại, không quá 1 lần mỗi `every` giây (app gửi 'W' 2 lần/giây)."""
        if time.monotonic() - self._info_at > every:
            self._info_at = time.monotonic()
            self.info(msg, addr)

    def on_packet(self, data, addr):
        t = data[0]
        if t == 0x57:  # 'W'
            try:
                req = json.loads(data[1:].decode("utf-8"))
            except ValueError:
                return
            if self.vs is None and (self.ffmpeg_ok() or self.ffmpeg_ok("window")):
                self.vs = video.VideoStreamer(self.sock.sendto, log=print)
            if self.vs is None:
                if time.monotonic() - self.last > 5:
                    self.last = time.monotonic()
                    self.info('Máy tính chưa có ffmpeg: bấm "Tải ffmpeg" trên cửa sổ PhonePad', addr)
                return
            self.last = time.monotonic()
            window = None
            if self.target is not None:
                if not self.ffmpeg_ok("window"):
                    self.info_once('Chế độ chỉ phát một cửa sổ cần thêm ffmpeg: bấm "Tải ffmpeg" trên '
                                   'cửa sổ PhonePad (hoặc chọn "Toàn màn hình")', addr)
                    return
                wt = self.window_target()
                if wt is None:
                    if self.addr is not None:
                        self.stop()
                    self.info_once('Cửa sổ "%s" đã đóng. Mở lại app đó hoặc chọn "Toàn màn hình" trên '
                                   'máy tính.' % self.target["title"], addr)
                    return
                hwnd, size = wt
                if size is None:  # chưa từng thấy cửa sổ hiện (đang thu nhỏ): chờ
                    self.info_once('Cửa sổ "%s" đang thu nhỏ, mở nó lên để phát.' % self.target["title"], addr)
                    return
                wcrop, w, h = window_crop_and_size(req, *size)
                window = {"hwnd": hwnd, "crop": wcrop}
                # đổi cửa sổ / đổi cỡ -> khoá đổi -> khởi động lại (di chuyển cửa sổ thì không cần)
                key = json.dumps(req, sort_keys=True) + repr((hwnd, size))
            else:
                key = json.dumps(req, sort_keys=True)
            if addr != self.addr or key != self.key:
                # điện thoại mới / đổi cài đặt: khởi động lại ffmpeg -> khung đầu là IDR, giải mã được ngay
                self.addr, self.key = addr, key
                self.started = time.monotonic()
                self.warned = False
                self.frames0 = self.vs.frames_sent
                if window:
                    crop = None
                    print('[video] phát cửa sổ "%s" tới %s: %dx%d' % (self.target["title"], addr[0], w, h))
                else:
                    crop, w, h = crop_and_size(req)
                    print("[video] phát tới %s: %dx%d crop=%s" % (addr[0], w, h, crop))
                self.pool.submit(self.vs.start, addr, crop=crop, width=w, height=h, window=window,
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
