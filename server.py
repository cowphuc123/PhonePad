"""PhonePad - biến điện thoại thành tay cầm / bàn phím ảo cho máy tính qua mạng LAN.

Chỉ nhận kết nối từ app Android PhonePad (UDP cổng 8766 cho nút bấm + hình; HTTP cổng 8765 cho
bố cục và trình sửa bố cục trong app). Trình duyệt thường bị từ chối.

Chạy:  python server.py              mở cửa sổ trạng thái (bản exe cũng chạy thế này)
       python server.py --console    chỉ in ra console, không mở cửa sổ
       python server.py --port 9000 --udp-port 9001
"""
import argparse
import asyncio
import json
import socket
import sys
import threading

from aiohttp import web

import paths
from session import PAD_LIB
from udp_server import start_udp
from video_control import VideoControl

# WebView sửa bố cục và httpGet của app gắn chuỗi này vào User-Agent
APP_UA = "PhonePadApp"
RELEASES = "https://github.com/cowphuc123/PhonePad/releases/latest"

REFUSED = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<body style="font-family:sans-serif;background:#111;color:#eee;padding:24px;line-height:1.5">
<h2>PhonePad chỉ nhận kết nối từ app Android</h2>
<p>Tải app PhonePad (.apk) tại <a style="color:#6cf" href="{0}">{0}</a>, cài lên điện thoại rồi mở app:
app tự tìm thấy máy tính này.</p></body>"""


@web.middleware
async def app_only(request, handler):
    if APP_UA not in request.headers.get("User-Agent", ""):
        return web.Response(status=403, text=REFUSED.format(RELEASES), content_type="text/html")
    return await handler(request)


async def editor(request):
    return web.FileResponse(paths.STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def get_layouts(request):
    if paths.LAYOUTS.exists():
        return web.Response(text=paths.LAYOUTS.read_text(encoding="utf-8"), content_type="application/json")
    return web.json_response({})


async def put_layouts(request):
    raw = await request.text()
    if len(raw) > 2_000_000:
        raise web.HTTPRequestEntityTooLarge(max_size=2_000_000, actual_size=len(raw))
    try:
        data = json.loads(raw)
    except ValueError:
        raise web.HTTPBadRequest(text="JSON không hợp lệ")
    paths.LAYOUTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return web.json_response({"ok": True})


def lan_ips():
    ips = []
    try:  # IP của card mạng đang ra internet (không gửi gói nào thật)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def make_app(port, udp_port):
    app = web.Application(middlewares=[app_only])
    app["port"] = port
    app["udp_port"] = udp_port
    app["udp"] = None
    app["video"] = None

    async def udp_start(_app):
        app["udp"] = await start_udp(port, udp_port)
        app["video"] = VideoControl(app["udp"])

    async def udp_stop(_app):
        if app["video"] is not None:
            app["video"].close()
        if app["udp"] is not None:
            app["udp"].close_all()
            app["udp"].transport.close()

    app.on_startup.append(udp_start)
    app.on_cleanup.append(udp_stop)

    async def status(request):
        return web.json_response({"clients": phones(app), "pad": PAD_LIB})

    app.router.add_get("/", editor)  # trình sửa bố cục, app mở bằng /?edit=1
    app.router.add_get("/api/layouts", get_layouts)
    app.router.add_post("/api/layouts", put_layouts)
    app.router.add_get("/api/status", status)
    return app


def phones(app):
    """Tên các điện thoại đang kết nối (gọi được từ thread khác)."""
    udp = app["udp"]
    if udp is None:
        return []
    try:
        return [p.sess.peer for p in list(udp.peers.values())]
    except RuntimeError:  # dict đổi kích thước giữa chừng
        return []


class ServerThread:
    """Chạy aiohttp + UDP trong một thread nền có event loop riêng (thread chính để cho cửa sổ)."""

    def __init__(self, port, udp_port):
        self.app = make_app(port, udp_port)
        self.loop = asyncio.new_event_loop()
        self.runner = None
        self.error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="PhonePadServer", daemon=True)

    def start(self, timeout=10):
        """Trả về None nếu chạy được, ngược lại là thông báo lỗi (vd. cổng đang bị dùng)."""
        self._thread.start()
        self._ready.wait(timeout)
        return self.error

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
        except OSError as e:
            self.error = ("Không mở được cổng %d/%d (PhonePad đã đang chạy?): %s"
                          % (self.app["port"], self.app["udp_port"], e))
        except Exception as e:
            self.error = "Lỗi khởi động: %r" % e
        self._ready.set()
        if self.error is None:
            self.loop.run_forever()
        self.loop.run_until_complete(self._cleanup())
        # tác vụ nền (dọn điện thoại mất kết nối, theo dõi video) chạy vô hạn: huỷ trước khi đóng loop
        tasks = asyncio.all_tasks(self.loop)
        for t in tasks:
            t.cancel()
        if tasks:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self.loop.close()

    async def _start(self):
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", self.app["port"]).start()

    async def _cleanup(self):
        if self.runner is not None:
            await self.runner.cleanup()

    def stop(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


def print_banner(args):
    print("=" * 56)
    print(" PhonePad đang chạy. Mở app PhonePad trên điện thoại (cùng Wi-Fi):")
    print(" app tự tìm máy tính. Không thấy thì nhập IP:")
    for ip in lan_ips():
        print("   %s" % ip)
    print(" Tay cầm Xbox ảo: %s" % ("SẴN SÀNG" if PAD_LIB else "chưa có (cần driver ViGEmBus)"))
    print("=" * 56)


def main():
    ap = argparse.ArgumentParser(description="PhonePad server")
    ap.add_argument("--port", type=int, default=8765, help="cổng HTTP (bố cục, trình sửa trong app)")
    ap.add_argument("--udp-port", type=int, default=8766, help="cổng UDP cho app Android")
    ap.add_argument("--console", action="store_true", help="không mở cửa sổ, chỉ in ra console")
    ap.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)  # do updater.relaunch truyền
    args = ap.parse_args()

    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    paths.ensure_layouts()

    if not args.console:
        import gui
        return gui.run(args)

    print_banner(args)
    print(" Nhấn Ctrl+C để tắt.")
    web.run_app(make_app(args.port, args.udp_port), host="0.0.0.0", port=args.port,
                print=None, access_log=None)


if __name__ == "__main__":
    sys.exit(main())
