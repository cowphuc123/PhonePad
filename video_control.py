"""Nối VideoStreamer (video.py) với kênh UDP của app Android.

App gửi 'W'+JSON mỗi 0,5 s khi muốn xem hình:
    {"w":1280,"h":720,"fps":60,"kbps":10000,"crop":{"mode":"full"|"left"|"right"|"top"|"bottom"
                                                     |"tl"|"tr"|"bl"|"br"}}
'w' = thôi xem. Quá 2 s không có 'W' -> điện thoại đó thôi xem.
Nhiều điện thoại xem cùng lúc (chơi 4 người): mỗi nguồn + cài đặt khác nhau là một ffmpeg riêng (tối đa
MAX_STREAMS); điện thoại xin đúng thứ đang phát thì xem chung luồng đó (vào giữa chừng thì chờ khung IDR
kế tiếp, tối đa 2 s). Luồng không còn ai xem thì dừng ffmpeg cho đỡ tốn GPU.
Nguồn hình: mặc định (VideoControl.target, ô "Phát lên điện thoại" trên cửa sổ PhonePad) hoặc riêng
từng điện thoại (VideoControl.sources[ip], vd. Nucleus Co-op mở 4 cửa sổ game, mỗi người xem cửa sổ của
mình). Chỉ phát một cửa sổ: chụp riêng cửa sổ đó bằng Windows.Graphics.Capture (gfxcapture, ffmpeg 9)
nên cửa sổ khác đè lên hay chuyển sang app khác vẫn chỉ thấy app đã chọn. Thu nhỏ xuống taskbar thì
Windows ngừng vẽ nó: điện thoại giữ hình cuối, mở lại là chạy tiếp. Game tắt rồi mở lại: tự tìm lại
theo tên tiến trình.
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
MAX_STREAMS = 4   # số ffmpeg chạy cùng lúc (NVENC GeForce cho tối đa 8 phiên; mỗi luồng ~10 Mbps Wi-Fi)
SCREEN = "screen"  # sources[ip] = SCREEN: điện thoại này xem toàn màn hình dù mặc định là một cửa sổ


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
    """Phát cả màn hình: vùng chụp (pixel) theo chế độ nửa / góc 1/4 của hồ sơ + cỡ khung ra."""
    sw, sh = screen_size()
    mode = (req.get("crop") or {}).get("mode", "full")
    hw, hh = sw // 2, sh // 2
    rects = {
        "left": (0, 0, hw, sh), "right": (hw, 0, sw - hw, sh),
        "top": (0, 0, sw, hh), "bottom": (0, hh, sw, sh - hh),
        "tl": (0, 0, hw, hh), "tr": (hw, 0, sw - hw, hh),
        "bl": (0, hh, hw, sh - hh), "br": (hw, hh, sw - hw, sh - hh),
    }
    crop = rects.get(mode)
    cw, ch = (crop[2], crop[3]) if crop else (sw, sh)
    w, h = _fit(req, cw, ch)
    return crop, w, h


def window_crop_and_size(req, cw, ch):
    """Phát một cửa sổ cỡ nội dung cw x ch: số pixel cắt (trái, trên, phải, dưới) theo chế độ của hồ sơ
    (game chia đôi / chia 4 màn hình chạy trong cửa sổ) + cỡ khung ra."""
    mode = (req.get("crop") or {}).get("mode", "full")
    rw, bh = cw - cw // 2, ch - ch // 2   # cắt bỏ nửa phải / nửa dưới
    crops = {
        "left": (0, 0, rw, 0), "right": (cw // 2, 0, 0, 0),
        "top": (0, 0, 0, bh), "bottom": (0, ch // 2, 0, 0),
        "tl": (0, 0, rw, bh), "tr": (cw // 2, 0, 0, bh),
        "bl": (0, ch // 2, rw, 0), "br": (cw // 2, ch // 2, 0, 0),
    }
    l, t, r, b = crops.get(mode, (0, 0, 0, 0))
    w, h = _fit(req, cw - l - r, ch - t - b)
    return (l, t, r, b), w, h


class _Stream:
    """Một ffmpeg đang phát; mọi điện thoại xin cùng nguồn + cùng cài đặt xem chung."""

    def __init__(self, vc, key, desc):
        self.key = key
        self.desc = desc
        self.sock = vc.sock
        self.subs = {}  # addr -> đã nhận khung IDR chưa (vào giữa chừng: chờ IDR rồi mới gửi)
        self.vs = video.VideoStreamer(self._send, log=print)
        self.vs._good = vc._good  # đường nén đã chạy được: dùng chung cho mọi luồng
        self.started = time.monotonic()
        self.warned = False

    def _send(self, pkt, _addr):
        """Gọi từ thread của VideoStreamer cho từng gói: gửi tới mọi điện thoại đang xem luồng này."""
        idr_start = pkt[1] & 1 and pkt[2] == 0 and pkt[3] == 0  # gói đầu của khung IDR
        for addr, ready in list(self.subs.items()):
            if not ready:
                if not idr_start:
                    continue
                self.subs[addr] = True
            try:
                self.sock.sendto(pkt, addr)
            except OSError:
                pass


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
        self.pool = concurrent.futures.ThreadPoolExecutor(1)  # start/stop chặn tới 1-3 s: không chạy trong event loop
        self.streams = {}   # khoá (nguồn + cài đặt) -> _Stream
        self.viewers = {}   # addr điện thoại -> [khoá luồng đang xem, lúc nhận 'W' cuối]
        self._good = {}     # chế độ ("screen"/"window") -> đường nén đã chạy được
        # cửa sổ PhonePad đặt: {"hwnd", "title", "exe"} = chỉ phát cửa sổ đó; None = cả màn hình.
        # Trong dict còn "state" ("ok"/"minimized"/"closed") và "size" (cỡ nội dung lần cuối thấy).
        self.target = None     # mặc định cho mọi điện thoại
        self.sources = {}      # ip -> target riêng của điện thoại đó, hoặc SCREEN
        self._info_at = {}
        asyncio.get_running_loop().create_task(self._watch())

    # --- nguồn hình ---

    def source_for(self, ip):
        """Nguồn của điện thoại ip: dict cửa sổ hoặc None (toàn màn hình)."""
        s = self.sources.get(ip, self.target)
        return None if s == SCREEN else s

    def window_targets(self):
        """Các cửa sổ đang được chọn (mặc định + riêng từng điện thoại), không trùng."""
        out = []
        for t in [self.target, *list(self.sources.values())]:
            if isinstance(t, dict) and all(t is not o for o in out):
                out.append(t)
        return out

    def resolve(self, t):
        """(hwnd, (cw, ch) hoặc None) của cửa sổ t; None nếu đã đóng. Cập nhật t["state"]."""
        if winlist is None:
            t["state"] = "closed"
            return None
        hwnd = t["hwnd"]
        if not winlist.alive(hwnd):
            # game tắt rồi mở lại: hwnd mới. Không lấy cửa sổ người chơi khác đang dùng (cùng game).
            used = {o["hwnd"] for o in self.window_targets() if o is not t and winlist.alive(o["hwnd"])}
            found = winlist.find(t["exe"], t["title"], exclude=used)
            if not found:
                t["state"] = "closed"
                return None
            hwnd = t["hwnd"] = found[0]
            t["title"] = found[1]
            t["size"] = None
        if winlist.minimized(hwnd):
            t["state"] = "minimized"
            return hwnd, t.get("size")
        r = winlist.client_rect(hwnd)
        if r and r[2] >= 16 and r[3] >= 16:
            t["size"] = (r[2], r[3])
        t["state"] = "ok"
        return hwnd, t.get("size")

    @staticmethod
    def ffmpeg_ok(kind="screen"):
        return video is not None and video.ffmpeg_path(kind) is not None

    # --- trạng thái cho cửa sổ PhonePad ---

    def viewer(self, addr):
        """(đường nén hoặc "", mô tả nguồn) nếu điện thoại addr đang xem hình, ngược lại None."""
        v = self.viewers.get(addr)
        st = self.streams.get(v[0]) if v else None
        return (st.vs.pipeline or "", st.desc) if st else None

    @property
    def watching(self):
        """[(ip, đường nén hoặc "")] các điện thoại đang xem hình."""
        out = []
        for addr in list(self.viewers):
            v = self.viewer(addr)
            if v:
                out.append((addr[0], v[0]))
        return out

    # --- gói từ điện thoại ---

    def info(self, msg, addr):
        self.udp.send(b"I" + json.dumps({"t": "videofail", "msg": msg}).encode(), addr)

    def info_once(self, msg, addr, every=5.0):
        """Báo cho điện thoại, không quá 1 lần mỗi `every` giây (app gửi 'W' 2 lần/giây)."""
        now = time.monotonic()
        if now - self._info_at.get(addr, 0.0) > every:
            self._info_at[addr] = now
            self.info(msg, addr)

    def on_packet(self, data, addr):
        t = data[0]
        if t == 0x57:  # 'W'
            try:
                req = json.loads(data[1:].decode("utf-8"))
            except ValueError:
                return
            self.on_watch(req, addr)
        elif t == 0x77:  # 'w'
            self._leave(addr)
        # 'K' (xin IDR) bị bỏ qua: request_idr phải khởi động lại ffmpeg (~1 s hình đứng),
        # lâu hơn chờ IDR định kỳ (GOP 2 s).

    def on_watch(self, req, addr):
        if video is None or not (self.ffmpeg_ok() or self.ffmpeg_ok("window")):
            self.info_once('Máy tính chưa có ffmpeg: bấm "Tải ffmpeg" trên cửa sổ PhonePad', addr)
            return
        target = self.source_for(addr[0])
        crop = window = None
        if target is not None:
            if not self.ffmpeg_ok("window"):
                self.info_once('Chế độ chỉ phát một cửa sổ cần thêm ffmpeg: bấm "Tải ffmpeg" trên '
                               'cửa sổ PhonePad (hoặc chọn "Toàn màn hình")', addr)
                return
            wt = self.resolve(target)
            if wt is None:
                self._leave(addr)
                self.info_once('Cửa sổ "%s" đã đóng. Mở lại app đó hoặc chọn "Toàn màn hình" trên '
                               'máy tính.' % target["title"], addr)
                return
            hwnd, size = wt
            if size is None:  # chưa từng thấy cửa sổ hiện (đang thu nhỏ): chờ
                self.info_once('Cửa sổ "%s" đang thu nhỏ, mở nó lên để phát.' % target["title"], addr)
                return
            wcrop, w, h = window_crop_and_size(req, *size)
            window = {"hwnd": hwnd, "crop": wcrop}
            # đổi cửa sổ / đổi cỡ -> khoá đổi -> luồng mới (di chuyển cửa sổ thì không cần)
            key = json.dumps(req, sort_keys=True) + repr((hwnd, size))
            desc = 'cửa sổ "%s" %dx%d' % (target["title"], w, h)
        else:
            crop, w, h = crop_and_size(req)
            key = json.dumps(req, sort_keys=True)
            desc = "màn hình %dx%d%s" % (w, h, " crop=%s" % (crop,) if crop else "")
        now = time.monotonic()
        v = self.viewers.get(addr)
        if v is not None and v[0] == key:
            v[1] = now
            return
        st = self.streams.get(key)
        old = self.streams.get(v[0]) if v else None
        frees = old is not None and len(old.subs) == 1  # rời luồng cũ thì luồng đó dừng, trả chỗ
        if st is None and len(self.streams) - frees >= MAX_STREAMS:
            self.info_once("Máy tính đang phát %d hình khác nhau (tối đa). Cho các điện thoại xem chung "
                           "một nguồn, hoặc tắt 📺 ở điện thoại khác." % MAX_STREAMS, addr)
            return
        self._leave(addr)  # đổi nguồn / đổi cài đặt: rời luồng cũ
        if st is None:
            # luồng mới: ffmpeg khởi động lại -> khung đầu là IDR, giải mã được ngay
            st = self.streams[key] = _Stream(self, key, desc)
            print("[video] phát %s tới %s" % (desc, addr[0]))
            self.pool.submit(st.vs.start, None, crop=crop, width=w, height=h, window=window,
                             fps=int(req.get("fps", 60)), bitrate_kbps=int(req.get("kbps", 10000)))
        else:
            print("[video] %s xem chung %s" % (addr[0], desc))
        st.subs[addr] = False
        self.viewers[addr] = [key, now]

    def _leave(self, addr):
        """Điện thoại addr thôi xem; luồng không còn ai xem thì dừng ffmpeg."""
        v = self.viewers.pop(addr, None)
        st = self.streams.get(v[0]) if v else None
        if st is None:
            return
        st.subs.pop(addr, None)
        if not st.subs:
            del self.streams[st.key]
            print("[video] dừng phát %s" % st.desc)
            self.pool.submit(st.vs.stop)

    def stop(self):
        for addr in list(self.viewers):
            self._leave(addr)

    async def _watch(self):
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            for addr, (_key, last) in list(self.viewers.items()):
                if now - last > WATCH_TIMEOUT:
                    self._leave(addr)
            for st in list(self.streams.values()):
                if (not st.warned and st.vs.frames_sent == 0 and st.vs.last_error
                        and now - st.started > NO_FRAME_WARN):
                    st.warned = True
                    for addr in list(st.subs):
                        self.info("Không phát được hình: %s" % st.vs.last_error[-200:], addr)

    def close(self):
        for st in list(self.streams.values()):
            st.vs.stop()
        self.streams.clear()
        self.viewers.clear()
        self.pool.shutdown(wait=False)
