"""PhonePad - biến điện thoại thành tay cầm / bàn phím ảo cho máy tính qua mạng LAN.

Chạy:  python server.py            (mặc định cổng 8765)
       python server.py --port 9000 --no-browser
"""
import argparse
import asyncio
import json
import socket
import sys
import webbrowser
from pathlib import Path

from aiohttp import WSMsgType, web

from session import PAD_LIB, Session
from udp_server import start_udp
from video_control import VideoControl

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
LAYOUTS = BASE / "layouts.json"
APK = BASE / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"


async def get_apk(request):
    if not APK.exists():
        raise web.HTTPNotFound(text="Chưa build app Android (cd android && gradlew assembleDebug)")
    return web.FileResponse(APK, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="PhonePad.apk"',
    })


async def ws_handler(request):
    ws = web.WebSocketResponse(compress=False, max_msg_size=8192, heartbeat=10)
    await ws.prepare(request)
    sock = request.transport.get_extra_info("socket") if request.transport else None
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # tắt Nagle: gửi ngay, không gom gói
        except OSError:
            pass

    sess = Session(request.remote)
    clients = request.app["clients"]
    clients.add(sess)
    print("[+] Điện thoại kết nối: %s  (đang có %d)" % (sess.peer, len(clients)))
    await ws.send_str(json.dumps({"t": "hello", "pad": PAD_LIB}))
    reported_pad_error = False
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            for m in msg.data.split("\n"):
                if not m:
                    continue
                c = m[0]
                if c == "1":
                    sess.press(m[1:])
                elif c == "0":
                    sess.release(m[1:])
                elif c == "a":
                    try:
                        x, y = m[2:].split(",")
                        sess.analog(m[1], float(x), float(y))
                    except ValueError:
                        pass
                elif c == "p":
                    await ws.send_str("P" + m[1:])
                elif c == "r":
                    sess.release_all()
            if sess.pad_error and not reported_pad_error:
                reported_pad_error = True
                print("[!] %s" % sess.pad_error)
                await ws.send_str(json.dumps({"t": "padfail", "msg": sess.pad_error}))
    finally:
        sess.close()
        clients.discard(sess)
        print("[-] Điện thoại ngắt kết nối: %s  (còn %d)" % (sess.peer, len(clients)))
    return ws


def _html(name):
    async def handler(request):
        return web.FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})
    return handler


async def get_layouts(request):
    if LAYOUTS.exists():
        return web.Response(text=LAYOUTS.read_text(encoding="utf-8"), content_type="application/json")
    return web.json_response({})


async def put_layouts(request):
    raw = await request.text()
    if len(raw) > 2_000_000:
        raise web.HTTPRequestEntityTooLarge(max_size=2_000_000, actual_size=len(raw))
    try:
        data = json.loads(raw)
    except ValueError:
        raise web.HTTPBadRequest(text="JSON không hợp lệ")
    LAYOUTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
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
    app = web.Application()
    app["clients"] = set()
    app["port"] = port
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
        return web.json_response({
            "clients": [s.peer for s in app["clients"]]
                       + ([p.sess.peer for p in app["udp"].peers.values()] if app["udp"] else []),
            "urls": ["http://%s:%d" % (ip, port) for ip in lan_ips()],
            "pad": PAD_LIB,
        })

    app.router.add_get("/", _html("index.html"))
    app.router.add_get("/host", _html("host.html"))
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/layouts", get_layouts)
    app.router.add_post("/api/layouts", put_layouts)
    app.router.add_get("/api/status", status)
    app.router.add_get("/app.apk", get_apk)
    return app


def main():
    ap = argparse.ArgumentParser(description="PhonePad server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--udp-port", type=int, default=8766, help="cổng UDP cho app Android")
    ap.add_argument("--no-browser", action="store_true", help="không tự mở trang mã QR")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 56)
    print(" PhonePad đang chạy. Trên điện thoại (cùng Wi-Fi) mở:")
    for ip in lan_ips():
        print("   http://%s:%d" % (ip, args.port))
    print(" App Android: tự tìm máy tính (UDP cổng %d)" % args.udp_port)
    print(" Tay cầm Xbox ảo: %s" % ("SẴN SÀNG" if PAD_LIB else "chưa có (pip install vgamepad)"))
    print(" Nhấn Ctrl+C để tắt.")
    print("=" * 56)

    app = make_app(args.port, args.udp_port)
    if not args.no_browser:
        async def open_host(_app):
            asyncio.get_running_loop().call_later(
                0.5, webbrowser.open, "http://localhost:%d/host" % args.port)
        app.on_startup.append(open_host)
    web.run_app(app, host="0.0.0.0", port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
