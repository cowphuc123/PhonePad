"""Thử module phát hình video.py trên chính máy này.

    python tools\\video_probe.py                    # phát 720p60 tới 127.0.0.1:18766, in số đo mỗi giây,
                                                    # ghi 5 giây đầu ra tools\\probe.h264
    python tools\\video_probe.py --pipeline qsv --seconds 15 --crop 0,0,960,540
    python tools\\video_probe.py --bench            # đo các đường nén (fps, độ trễ, CPU)
    python tools\\video_probe.py --bench --pipelines nvenc,nvenc_ir,nvenc_vpp,qsv,mf
        (--bench mở một ô đen/trắng nhấp nháy ở góc trên trái màn hình để đo độ trễ)

Xem file ghi được:  tools\\ffmpeg\\bin\\ffplay.exe tools\\probe.h264
"""
import argparse
import collections
import ctypes
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import video  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None


# ---------------------------------------------------------------------------
# Bộ nhận: ghép gói thành khung như điện thoại sẽ làm
# ---------------------------------------------------------------------------

class Receiver(threading.Thread):
    def __init__(self, port, record_path=None, record_seconds=5.0):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.2)
        self.port = port
        self.stop_flag = False
        self.lock = threading.Lock()
        self.pending = {}          # frame_number -> [packet_count, {index: payload}, flags, t_first]
        self.max_frame = -1
        self.record_path = record_path
        self.record_seconds = record_seconds
        self.record_file = None
        self.record_start = None
        self.recorded_frames = 0
        self.reset_stats()
        self.total_frames = 0
        self.total_idr = 0
        self.bad_packets = 0

    def reset_stats(self):
        with getattr(self, "lock", threading.Lock()):
            self.s_frames = 0
            self.s_bytes = 0
            self.s_pkts = 0
            self.s_expected = 0
            self.s_sizes = []
            self.s_lost_frames = 0
            self.s_t0 = time.perf_counter()

    def run(self):
        while not self.stop_flag:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._on_packet(data)
        if self.record_file:
            self.record_file.close()

    def _on_packet(self, data):
        if len(data) < 16:
            self.bad_packets += 1
            return
        typ, flags, idx, count, _, fn, _cap = video.HEADER.unpack_from(data)
        if typ != video.PKT_TYPE or idx >= count:
            self.bad_packets += 1
            return
        with self.lock:
            self.s_pkts += 1
            self.s_bytes += len(data)
            if fn <= self.max_frame - 30:
                return  # quá cũ
            ent = self.pending.get(fn)
            if ent is None:
                ent = self.pending[fn] = [count, {}, flags]
                self.s_expected += count
                if fn > self.max_frame + 1 and self.max_frame >= 0:
                    self.s_lost_frames += fn - self.max_frame - 1
                self.max_frame = max(self.max_frame, fn)
            ent[1][idx] = data[16:]
            if len(ent[1]) == count:
                del self.pending[fn]
                au = b"".join(ent[1][i] for i in range(count))
                self._on_frame(fn, au, bool(flags & video.FLAG_IDR))
            # dọn khung dở dang quá cũ
            for k in [k for k in self.pending if k < self.max_frame - 30]:
                del self.pending[k]

    def _on_frame(self, fn, au, idr):
        self.s_frames += 1
        self.total_frames += 1
        self.total_idr += idr
        self.s_sizes.append(len(au))
        if self.record_path and self.record_start is None and idr:
            self.record_file = open(self.record_path, "wb")
            self.record_start = time.perf_counter()
        if self.record_file:
            if time.perf_counter() - self.record_start <= self.record_seconds:
                self.record_file.write(au)
                self.recorded_frames += 1
            else:
                self.record_file.close()
                self.record_file = None
                print(f"[probe] đã ghi {self.recorded_frames} khung vào {self.record_path}")

    def snapshot(self):
        with self.lock:
            dt = time.perf_counter() - self.s_t0
            sizes = self.s_sizes
            lost = (1 - self.s_pkts / self.s_expected) * 100 if self.s_expected else 0.0
            res = dict(fps=self.s_frames / dt, mbps=self.s_bytes * 8 / dt / 1e6, loss=max(0.0, lost),
                       lost_frames=self.s_lost_frames,
                       avg_kb=(sum(sizes) / len(sizes) / 1024) if sizes else 0,
                       max_kb=(max(sizes) / 1024) if sizes else 0)
        self.reset_stats()
        return res

    def close(self):
        self.stop_flag = True
        self.sock.close()


def run_probe(args):
    port = args.port
    recv = Receiver(port, record_path=args.out, record_seconds=5.0)
    recv.start()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    vs = video.VideoStreamer(lambda b, a: tx.sendto(b, a),
                             pipelines=[args.pipeline] if args.pipeline else None)
    crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None
    vs.start(("127.0.0.1", port), crop=crop, width=args.width, height=args.height,
             fps=args.fps, bitrate_kbps=args.kbps)
    cpu = psutil.Process() if psutil else None
    if cpu:
        cpu.cpu_percent()
    ff_ps = None
    try:
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            time.sleep(1.0)
            s = recv.snapshot()
            ncpu = psutil.cpu_count() if psutil else 1
            ff_cpu = float("nan")
            if psutil and vs.proc:
                try:
                    if ff_ps is None or ff_ps.pid != vs.proc.pid:  # ffmpeg vừa khởi động lại
                        ff_ps = psutil.Process(vs.proc.pid)
                        ff_ps.cpu_percent()
                    else:
                        ff_cpu = ff_ps.cpu_percent() / ncpu
                except Exception:
                    ff_ps = None
            py_cpu = cpu.cpu_percent() / ncpu if cpu else float("nan")
            print(f"[{vs.pipeline or '...'}] {s['fps']:5.1f} fps  {s['mbps']:5.2f} Mbps  "
                  f"mất {s['loss']:4.2f}% gói ({s['lost_frames']} khung)  "
                  f"khung tb {s['avg_kb']:5.1f} KB, lớn nhất {s['max_kb']:5.1f} KB  "
                  f"CPU ffmpeg {ff_cpu:4.1f}% python {py_cpu:4.1f}%")
    except KeyboardInterrupt:
        pass
    finally:
        vs.stop()
        time.sleep(0.3)
        recv.close()
        recv.join(1)
    print(f"[probe] tổng {recv.total_frames} khung, {recv.total_idr} IDR, gói hỏng {recv.bad_packets}")
    if vs.last_error:
        print(f"[probe] lỗi cuối: {vs.last_error}")
    return 0 if recv.total_frames else 1


# ---------------------------------------------------------------------------
# Đo các đường nén: fps, độ trễ (đổi màu cửa sổ -> khung ra stdout), CPU
# ---------------------------------------------------------------------------

class FlipWindow:
    """Cửa sổ luôn nổi, đổi đen/trắng; ghi lại perf_counter_ns lúc đổi."""

    def __init__(self, x=100, y=100, w=640, h=480):
        import tkinter as tk
        self.rect = (x, y, w, h)
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.white = False
        self.flips = []  # (t_ns, white)
        self.root.update()

    def flip(self):
        self.white = not self.white
        self.canvas.configure(bg="white" if self.white else "black")
        self.root.update()
        self.flips.append((time.perf_counter_ns(), self.white))

    def pump(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.root.update()
            time.sleep(0.005)

    def close(self):
        self.root.destroy()


RAW_W, RAW_H = 64, 36  # ảnh thu nhỏ để dò màu cửa sổ đo


class RawSampler(threading.Thread):
    """Đọc khung bgra 64x36 từ stdout một tiến trình ffmpeg; ghi (thời điểm, có trắng không)."""

    def __init__(self, proc, win_rect):
        super().__init__(daemon=True)
        self.proc = proc
        sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        x, y, w, h = win_rect
        px, py = int((x + w / 2) / sw * RAW_W), int((y + h / 2) / sh * RAW_H)
        self.off = (py * RAW_W + px) * 4
        self.samples = []  # (t_ns, white)

    def run(self):
        size = RAW_W * RAW_H * 4
        buf = bytearray()
        while True:
            d = self.proc.stdout.read(size - len(buf))
            if not d:
                break
            buf += d
            if len(buf) == size:
                self.samples.append((time.perf_counter_ns(), buf[self.off + 1] > 128))
                buf = bytearray()


def _latencies(flips, frames):
    """flips: [(t_ns, white)]; frames: [(t_ns, white)] theo thứ tự khung.
    Trả về ms từ lúc đổi màu tới khung đầu tiên mang màu mới."""
    out = []
    for t_flip, white in flips:
        for t, w in frames:
            if t > t_flip and w == white:
                if t - t_flip < 500e6:
                    out.append((t - t_flip) / 1e6)
                break
    return out


def _do_flips(win, n):
    win.flips.clear()
    for k in range(n):
        win.flip()
        win.pump(0.25 + 0.013 * (k % 5))  # lệch pha để không trùng nhịp chụp


class CheckDecoder:
    """Giải mã lại luồng nén để biết khung nén thứ k đã mang màu mới chưa.
    Khung giải mã thứ k <-> khung nén thứ k (không B-frame, không bỏ khung), nên độ trễ của chính
    bộ giải mã không tính vào kết quả: lấy thời điểm khung nén k ra khỏi stdout ffmpeg nén."""

    def __init__(self, ffmpeg, win_rect):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
               "-probesize", "32", "-analyzeduration", "0",  # -fflags nobuffer làm rơi khung
               "-flags", "low_delay", "-threads", "1", "-f", "h264", "-i", "pipe:0",
               "-vf", f"scale={RAW_W}:{RAW_H}:flags=area,format=bgra", "-fps_mode", "passthrough",
               "-f", "rawvideo", "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.rs = RawSampler(self.proc, win_rect)
        self.rs.start()
        self.sent_times = []  # thời điểm khung nén thứ k ra khỏi ffmpeg nén
        self.q = collections.deque()
        self.evt = threading.Event()
        self.closed = False
        threading.Thread(target=self._writer, daemon=True).start()

    def feed(self, au, t_ns, idr):
        if not self.sent_times and not idr:
            return  # bắt đầu từ IDR
        self.sent_times.append(t_ns)
        self.q.append(au)
        self.evt.set()

    def _writer(self):
        while not self.closed or self.q:
            self.evt.wait(0.1)
            self.evt.clear()
            while self.q:
                try:
                    self.proc.stdin.write(self.q.popleft())
                except Exception:
                    return

    def close(self):
        time.sleep(0.3)
        self.closed = True
        time.sleep(0.2)
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.rs.join(3)
        self.proc.kill()

    def frames(self):
        """[(t_ns ra khỏi bộ nén, white)] cho các khung giải mã được."""
        n = min(len(self.sent_times), len(self.rs.samples))
        return [(self.sent_times[k], self.rs.samples[k][1]) for k in range(n)]


def bench_pipeline(win, name, args, seconds=8.0, flips_n=20):
    frames = []
    vs = video.VideoStreamer(lambda b, a: None, pipelines=[name], log=lambda m: None)
    vs.START_TIMEOUT = 6.0
    dec = CheckDecoder(video.find_ffmpeg(), win.rect)

    def on_frame(au, n, cap, idr):
        t = time.perf_counter_ns()
        frames.append((t, len(au), idr))
        dec.feed(au, t, idr)

    vs.on_frame = on_frame
    vs.start(("127.0.0.1", 9), width=args.width, height=args.height, fps=args.fps,
             bitrate_kbps=args.kbps)
    t0 = time.perf_counter()
    while not frames and time.perf_counter() - t0 < 7:
        win.pump(0.1)
    if not frames:
        vs.stop()
        dec.close()
        return dict(name=name, ok=False, err=vs.last_error)
    win.pump(1.0)
    ff = psutil.Process(vs.proc.pid) if psutil and vs.proc else None
    me = psutil.Process() if psutil else None
    if ff:
        ff.cpu_percent(), me.cpu_percent()
    n0, t_s = len(frames), time.perf_counter()
    win.pump(seconds)  # đo fps/CPU
    n1, t_e = len(frames), time.perf_counter()
    ncpu = psutil.cpu_count() if psutil else 1
    ff_cpu = ff.cpu_percent() / ncpu if ff else float("nan")
    py_cpu = me.cpu_percent() / ncpu if me else float("nan")
    _do_flips(win, flips_n)
    vs.stop()
    dec.close()
    lat = _latencies(win.flips, dec.frames())
    sizes = [s for _, s, _ in frames[n0:n1]]
    gaps = [(b[0] - a[0]) / 1e6 for a, b in zip(frames[n0:n1], frames[n0 + 1:n1])]
    return dict(name=name, ok=True, fps=(n1 - n0) / (t_e - t_s), ff_cpu=ff_cpu, py_cpu=py_cpu,
                lat=lat, avg_kb=statistics.mean(sizes) / 1024 if sizes else 0,
                idr_kb=max((s for _, s, i in frames if i), default=0) / 1024,
                jitter=statistics.pstdev(gaps) if gaps else 0, err=vs.last_error,
                decoded=f"{len(dec.rs.samples)}/{len(dec.sent_times)}")


def _fmt_lat(lat):
    if not lat:
        return "không đo được"
    return (f"trung vị {statistics.median(lat):5.1f} ms, min {min(lat):5.1f}, "
            f"max {max(lat):5.1f} (n={len(lat)})")


def run_bench(args):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    names = args.pipelines.split(",")
    win = FlipWindow()
    try:
        for name in names:
            r = bench_pipeline(win, name, args)
            if not r["ok"]:
                print(f"{name:10s} KHÔNG CHẠY: {r['err']}")
                continue
            print(f"{name:10s} {r['fps']:5.1f} fps (lệch nhịp {r['jitter']:4.1f} ms)  "
                  f"CPU ffmpeg {r['ff_cpu']:4.1f}% python {r['py_cpu']:4.1f}%  "
                  f"khung tb {r['avg_kb']:5.1f} KB, IDR {r['idr_kb']:5.1f} KB  "
                  f"giải mã đối chiếu {r['decoded']}  đổi màn hình->stdout: {_fmt_lat(r['lat'])}")
    finally:
        win.close()


def main():
    if not sys.stdout.isatty():  # in qua pipe với bảng mã cp125x thì tiếng Việt làm print() lỗi
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", action="store_true", help="đo các đường nén")
    ap.add_argument("--pipelines", default="nvenc,qsv,mf", help="danh sách đường nén khi --bench")
    ap.add_argument("--pipeline", help="ép một đường nén khi chạy probe")
    ap.add_argument("--port", type=int, default=18766)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--crop", help="x,y,w,h")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--kbps", type=int, default=10000)
    ap.add_argument("--out", default=str(ROOT / "tools" / "probe.h264"))
    args = ap.parse_args()
    if args.bench:
        return run_bench(args)
    return run_probe(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
