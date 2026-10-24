"""PhonePad - phát hình màn hình máy tính sang điện thoại qua UDP (H.264 Annex-B).

Cần ffmpeg 7.1 trong tools/ffmpeg (chạy tools/fetch_ffmpeg.ps1).

KẾT QUẢ ĐO (24/10/2026, `python tools/video_probe.py --bench`)
Máy: laptop Optimus, màn 1920x1080 144 Hz do Intel UHD vẽ, RTX 3050 Laptop (driver 566.36),
16 luồng CPU, ffmpeg 7.1 (gyan). Thiết lập: 1280x720, 60 fps, CBR 10 Mbps, VBV 1 khung, không
B-frame, GOP 2 s, AUD. "Trễ" = từ lúc một cửa sổ đổi đen<->trắng tới lúc khung nén đầu tiên mang
màu mới ra khỏi stdout ffmpeg (biết bằng cách giải mã lại luồng; khung giải mã thứ k <-> khung nén
thứ k), 20 lần đổi màu/lần đo. Con số GỒM CẢ thời gian DWM vẽ (<= 7 ms ở 144 Hz) và nhịp chụp
60 fps của ddagrab (<= 16.7 ms), nên là cận trên của độ trễ nén. CPU = % tổng 16 luồng.
Phần lớn lần đo có Roblox đang chạy (GPU NVIDIA 92-98%) - đúng tình huống vừa chơi vừa phát.

  đường                                     fps      CPU ffmpeg   trễ trung vị (min-max), ms
  nvenc  (MẶC ĐỊNH) tải RAM -> swscale nv12  54-58    4-5%         23 (15-38), 26 (20-42)
         -> h264_nvenc p1/ull, -delay 0
  nvenc_ir  như trên + -intra-refresh 1      56-58    5-11%        17 (10-64), 39 (22-67) [*]
  nvenc_vpp VPP Intel thu nhỏ -> tải -> NVENC 47-56    1.4-1.7%     37 (19-82), 52, 60
  qsv    hwmap -> vpp_qsv -> h264_qsv        33-60    0.3-2%       35 (33-50), 38, 76 (31-149)
         low_power, async_depth 1            (nhịp đều nhất: lệch 5 ms; 1 lần tụt 33 fps khi GPU bận)
  mf     tải RAM -> swscale nv12 -> h264_mf   52-59    3-6%         95 (80-114), 132, 211
         (IDR 107-119 KB so với ~20 KB của các đường khác)
  [*] intra-refresh không có IDR định kỳ (chỉ IDR đầu), điện thoại vào giữa chừng / mất gói phải
      chờ request_idr() -> không làm mặc định; trễ không hơn nvenc thường một cách rõ rệt.
  Đã thử và loại:
  - chụp thẳng trên NVIDIA rồi NVENC không copy: ddagrab "Failed to enumerate DXGI output 0"
    (màn hình gắn vào Intel), nên NVENC luôn phải chép khung qua RAM.
  - tải RAM -> hwupload_cuda -> scale_cuda -> NVENC: chỉ ~20 fps.
  - swscale giữ bgra rồi cho NVENC tự đổi màu: CPU gấp ~2.5 lần (9-11%), trễ 16-34 ms.
  - vpp_qsv/scale_d3d11 ra nv12: lỗi "Could not create the texture (80070057)".
  - QSV không low_power: 78-138 ms; vpp_qsv async_depth mặc định (4): thêm ~40-60 ms.
  - ffmpeg >= 7.1.1 của gyan: NVENC API 13.x, driver 566 không mở được (cần >= 570/610).
  - "-fflags nobuffer" ở phía giải mã ffmpeg làm rơi khung (chỉ liên quan bộ đo).
Thứ tự thử: nvenc -> qsv -> mf. Đường nào chưa ra khung trong START_TIMEOUT giây thì sang đường
sau; màn hình khoá / UAC ("access denied") thì chờ rồi thử lại đúng đường đó.
Khởi động (và mỗi request_idr/restart) mất ~0.9-1.0 s tới khung đầu (mở CUDA + NVENC).

Cách dùng (phiên chính):
    vs = VideoStreamer(send_fn)            # send_fn(bytes, addr) gửi UDP, dùng chung socket 8766
    vs.start(("192.168.1.5", 8766))        # toàn màn hình, 1280x720 60 fps 10 Mbps
    vs.restart(crop=(0, 0, 960, 540))      # đổi vùng chụp / cỡ / fps / bitrate / addr
    vs.set_addr(("192.168.1.6", 8766))     # đổi nơi nhận, không khởi động lại ffmpeg
    vs.request_idr()                       # điện thoại mới vào hoặc mất gói: xin khung IDR sớm
    vs.stop()
    vs.running, vs.pipeline, vs.last_error, vs.frames_sent, vs.bytes_sent   # trạng thái

send_fn được gọi từ thread đọc ffmpeg (không phải thread asyncio). socket.sendto với UDP an toàn
khi gọi từ nhiều thread; nếu phiên chính dùng transport asyncio thì bọc bằng
loop.call_soon_threadsafe. Lỗi trong send_fn được nuốt (ghi vào last_error) để không làm chết thread.

Định dạng gói (little-endian, header 16 byte, cả gói tối đa 1200 byte):
    u8  type = 0x56 ('V')
    u8  flags         bit0 = khung có IDR (luôn kèm SPS/PPS ngay trước)
    u16 packet_index  0 .. packet_count-1
    u16 packet_count
    u16 reserved = 0
    u32 frame_number  tăng dần từ 0 mỗi lần start/restart vẫn tiếp tục đếm (không reset)
    u32 capture_time_us  32 bit thấp của time.perf_counter_ns()//1000 lúc nhận khung từ ffmpeg
    ... dữ liệu Annex-B của khung (bắt đầu bằng AUD 00 00 00 01 09 ...)
"""
import atexit
import collections
import ctypes
import os
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
import shutil

import paths

PKT_TYPE = 0x56
HEADER = struct.Struct("<BBHHHII")
MAX_PACKET = 1200
MAX_PAYLOAD = MAX_PACKET - HEADER.size
FLAG_IDR = 0x01

# Thứ tự thử các đường nén; đường đầu là mặc định, các đường sau là dự phòng tự động.
DEFAULT_PIPELINES = ["nvenc", "qsv", "mf"]


def ffmpeg_path(kind="screen"):
    """ffmpeg.exe đã tải, None nếu chưa có.
    "screen": ffmpeg 7.1 (ddagrab + NVENC chạy với driver NVIDIA cũ), DATA_DIR/ffmpeg hoặc trong PATH.
    "window": ffmpeg 9 (gfxcapture = Windows.Graphics.Capture, chụp riêng một cửa sổ), DATA_DIR/ffmpeg9."""
    for d in (paths.FFMPEG_DIRS if kind == "screen" else paths.FFMPEG9_DIRS):
        exe = d / "ffmpeg.exe"
        if exe.exists():
            return str(exe)
    if kind == "screen":  # thiếu 7.1 thì ffmpeg trong PATH, rồi ffmpeg 9 (ddagrab vẫn có, NVENC cần driver mới)
        return shutil.which("ffmpeg") or ffmpeg_path("window")
    return None


def find_ffmpeg():
    return ffmpeg_path() or "ffmpeg"


# ---------------------------------------------------------------------------
# Dựng dòng lệnh ffmpeg cho từng đường nén
# ---------------------------------------------------------------------------

def _grab(crop, fps):
    """Bộ lọc ddagrab (Desktop Duplication, chụp thẳng trên GPU đang vẽ màn hình)."""
    s = f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=1"
    if crop:
        x, y, w, h = (int(v) for v in crop)
        w -= w % 2
        h -= h % 2
        s += f":offset_x={x}:offset_y={y}:video_size={w}x{h}"
    # pts của ddagrab là giờ chụp (µs) nên dao động; quy về 1/fps thì có lúc hai khung trùng pts và
    # muxer vứt khung sau ("non monotonically increasing dts") -> mất khung tham chiếu, hình vỡ.
    # Đánh lại pts tuần tự (thời gian thật đi trong header gói UDP, không cần pts).
    return s + f",setpts=N/{fps}/TB"


def _gfx(window, width, height, fps):
    """gfxcapture: chụp riêng một cửa sổ (kể cả khi bị cửa sổ khác che), thu nhỏ luôn trên GPU về
    width x height giữ tỉ lệ. Cửa sổ chỉ gửi khung khi nội dung đổi; thu nhỏ xuống taskbar thì
    Windows ngừng vẽ nó nên không có khung mới (điện thoại giữ hình cuối)."""
    s = (f"gfxcapture=hwnd={int(window['hwnd'])}:max_framerate={fps}:width={width}:height={height}"
         f":resize_mode=scale_aspect:capture_cursor=1")
    for side, v in zip(("left", "top", "right", "bottom"), window.get("crop") or (0, 0, 0, 0)):
        if v:
            s += f":crop_{side}={int(v)}"
    return s + f",setpts=N/{fps}/TB"


def _rate(bitrate_kbps, fps, bufsize_frames=1):
    # CBR, VBV ~1 khung: khung lớn bị nén mạnh hơn thay vì làm nghẽn đường truyền
    b = f"{int(bitrate_kbps)}k"
    buf = f"{max(1, int(bitrate_kbps * bufsize_frames / fps))}k"
    return ["-b:v", b, "-maxrate", b, "-minrate", b, "-bufsize", buf]


def _nvenc(gop, bitrate_kbps, fps, intra_refresh=False):
    a = ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-zerolatency", "1",
         "-delay", "0",  # mặc định nvenc giữ vài khung trong hàng đợi trước khi trả ra
         "-rc", "cbr", "-bf", "0", "-g", gop, "-forced-idr", "1",
         "-profile:v", "high", "-aud", "1"] + _rate(bitrate_kbps, fps)
    if intra_refresh:
        a += ["-intra-refresh", "1"]
    return a


INTEL_D3D = ["-init_hw_device", "d3d11va=dx:,vendor_id=0x8086", "-filter_hw_device", "dx"]
INTEL_QSV = ["-init_hw_device", "d3d11va=dx:,vendor_id=0x8086",
             "-init_hw_device", "qsv=qs@dx", "-filter_hw_device", "dx"]
# Thiếu thiết bị CUDA thì ffmpeg đưa luôn thiết bị d3d11 của Intel cho h264_nvenc
# -> "OpenEncodeSessionEx failed: no encode device"
NV_CUDA = ["-init_hw_device", "cuda=cu"]
# Gắn nhãn màu khớp với ma trận swscale dùng khi đổi RGB->YUV (Android coi luồng HD là BT.709)
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-color_range", "tv"]


def _sws_nv12(width, height):
    return (f"scale={width}:{height}:flags=fast_bilinear:out_color_matrix=bt709:out_range=tv,"
            f"format=nv12")


def build_cmd(pipeline, crop=None, width=1280, height=720, fps=60, bitrate_kbps=10000,
              ffmpeg=None, window=None):
    """window = {"hwnd": ..., "crop": (trái, trên, phải, dưới)} -> chỉ phát cửa sổ đó (cần ffmpeg 9);
    các bước sau nguồn hình giống hệt (thu nhỏ về đúng cỡ đã có sẵn thì swscale/VPP chỉ đổi màu)."""
    ff = ffmpeg or find_ffmpeg()
    gop = str(int(fps * 2))
    grab = _gfx(window, width, height, fps) if window else _grab(crop, fps)
    head = [ff, "-hide_banner", "-loglevel", "error", "-nostats"]
    # Màn hình laptop Optimus do Intel vẽ: ddagrab phải chạy trên GPU Intel (chạy trên NVIDIA báo
    # "Failed to enumerate DXGI output 0"), nên NVENC luôn cần một lần chép khung qua RAM.
    if pipeline in ("nvenc", "nvenc_ir"):
        # Intel chụp -> tải 1080p bgra về RAM -> thu nhỏ + đổi nv12 bằng CPU (một bước swscale) -> NVENC
        # (thu nhỏ giữ bgra tốn CPU gấp ~2.5 lần; đẩy lên CUDA rồi scale_cuda chỉ được ~20 fps)
        vf = f"{grab},hwdownload,format=bgra,{_sws_nv12(width, height)}"
        enc = _nvenc(gop, bitrate_kbps, fps, intra_refresh=pipeline == "nvenc_ir") + BT709
        dev = INTEL_D3D + NV_CUDA
    elif pipeline == "nvenc_vpp":
        # Intel chụp -> thu nhỏ bằng VPP của Intel -> tải 720p bgra về RAM -> NVENC
        vf = (f"{grab},hwmap=derive_device=qsv,format=qsv,vpp_qsv=w={width}:h={height}:async_depth=1,"
              f"hwdownload,format=bgra")
        enc = _nvenc(gop, bitrate_kbps, fps)
        dev = INTEL_QSV + NV_CUDA
    elif pipeline == "qsv":
        # Intel chụp -> map sang QSV (không copy) -> thu nhỏ bằng VPP -> Quick Sync (vào bgra)
        # (ép VPP ra nv12 thì lỗi "Could not create the texture (80070057)" trên máy này)
        vf = f"{grab},hwmap=derive_device=qsv,format=qsv,vpp_qsv=w={width}:h={height}:async_depth=1"
        enc = ["-c:v", "h264_qsv", "-preset", "veryfast", "-low_power", "1", "-async_depth", "1", "-look_ahead", "0",
               "-low_delay_brc", "1", "-bf", "0", "-g", gop, "-idr_interval", "0",
               "-forced_idr", "1", "-profile:v", "high", "-aud", "1"] + _rate(bitrate_kbps, fps)
        dev = INTEL_QSV
    elif pipeline == "mf":
        # Media Foundation (MFT phần cứng do Windows chọn), vào nv12 trong RAM; MF không có AUD nên chèn bằng bsf
        vf = f"{grab},hwdownload,format=bgra,{_sws_nv12(width, height)}"
        enc = BT709 + ["-c:v", "h264_mf", "-hw_encoding", "1", "-scenario", "display_remoting",
               "-rate_control", "cbr", "-bf", "0", "-g", gop,
               "-bsf:v", "h264_metadata=aud=insert"] + _rate(bitrate_kbps, fps)
        dev = INTEL_D3D
    else:
        raise ValueError(f"Không biết đường nén {pipeline!r}")
    # -r: setpts làm mất frame rate của luồng, QSV/MF cần biết (pts đã đều 1/fps nên không thêm/bớt khung)
    out = ["-an", "-sn", "-r", str(int(fps)),
           "-flush_packets", "1", "-avioflags", "direct", "-f", "h264", "pipe:1"]
    return head + dev + ["-filter_complex", vf] + enc + out


# ---------------------------------------------------------------------------
# Windows: Job Object để ffmpeg chết theo Python dù Python bị giết thô bạo
# ---------------------------------------------------------------------------

_job = None
if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.PeekNamedPipe.restype = wintypes.BOOL
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                    ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _get_job():
        global _job
        if _job is None:
            h = _k32.CreateJobObjectW(None, None)
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))
            _job = h
        return _job

    def _attach_to_job(proc):
        try:
            _k32.AssignProcessToJobObject(_get_job(), int(proc._handle))
        except Exception:
            pass

    def _pipe_pending(fd):
        """Số byte còn chờ trong pipe (0 = ffmpeg đã ghi xong lần ghi gần nhất)."""
        import msvcrt
        avail = wintypes.DWORD(0)
        if not _k32.PeekNamedPipe(msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(avail), None):
            return 0
        return avail.value
else:
    def _attach_to_job(proc):
        pass

    def _pipe_pending(fd):
        import select
        return 1 if select.select([fd], [], [], 0)[0] else 0


# ---------------------------------------------------------------------------
# Tách khung từ luồng Annex-B
# ---------------------------------------------------------------------------

def _nal_starts(buf, start=0):
    """Vị trí (start_code_pos, nal_header_pos) của mọi NAL trong buf."""
    out = []
    i = buf.find(b"\x00\x00\x01", start)
    while i >= 0:
        sc = i - 1 if i > 0 and buf[i - 1] == 0 else i
        out.append((sc, i + 3))
        i = buf.find(b"\x00\x00\x01", i + 3)
    return out


class AnnexBFramer:
    """Ghép byte từ ffmpeg thành từng khung (access unit), tách theo AUD.

    Khung cuối trong bộ đệm được coi là xong khi pipe trống: ffmpeg chạy với
    -avioflags direct -flush_packets 1 nên mỗi khung là đúng một lần ghi, không phải chờ AUD
    của khung sau (đỡ trễ 1 khung). Thêm SPS/PPS trước IDR nếu bộ nén không tự gắn.
    """

    def __init__(self):
        self.buf = bytearray()
        self.sps = None
        self.pps = None

    def feed(self, data, pipe_empty):
        """Trả về danh sách (au_bytes, is_idr)."""
        self.buf += data
        buf = self.buf
        auds = [sc for sc, h in _nal_starts(buf) if h < len(buf) and (buf[h] & 0x1F) == 9]
        frames = []
        if not auds:
            return frames
        if auds[0] > 0:  # rác trước AUD đầu (thường không có)
            auds.insert(0, 0)
        bounds = auds + ([len(buf)] if pipe_empty else [])
        for a, b in zip(bounds, bounds[1:]):
            frames.append(self._finish(bytes(buf[a:b])))
        del buf[:bounds[-1]]
        return [f for f in frames if f is not None]

    def _finish(self, au):
        nals = _nal_starts(au)
        types = set()
        has_sps = has_pps = False
        filler = []
        for k, (sc, h) in enumerate(nals):
            if h >= len(au):
                continue
            t = au[h] & 0x1F
            types.add(t)
            end = nals[k + 1][0] if k + 1 < len(nals) else len(au)
            if t == 12:
                filler.append((sc, end))
            elif t == 7:
                self.sps, has_sps = au[sc:end], True
            elif t == 8:
                self.pps, has_pps = au[sc:end], True
        if not (types & {1, 5}):
            return None  # không có slice -> không phải khung hình
        if filler:
            # NAL filler (CBR độn cho đủ bitrate) chỉ tốn băng thông, bộ giải mã bỏ qua
            for sc, end in reversed(filler):
                au = au[:sc] + au[end:]
            nals = _nal_starts(au)
        idr = 5 in types
        if idr and not (has_sps and has_pps) and self.sps and self.pps:
            # chèn SPS/PPS ngay sau AUD
            aud_end = nals[1][0] if len(nals) > 1 else len(au)
            au = au[:aud_end] + self.sps + self.pps + au[aud_end:]
        return au, idr


def packetize(au, frame_number, capture_us, idr):
    flags = FLAG_IDR if idr else 0
    count = (len(au) + MAX_PAYLOAD - 1) // MAX_PAYLOAD
    for i in range(count):
        yield HEADER.pack(PKT_TYPE, flags, i, count, 0, frame_number & 0xFFFFFFFF,
                          capture_us & 0xFFFFFFFF) + au[i * MAX_PAYLOAD:(i + 1) * MAX_PAYLOAD]


# ---------------------------------------------------------------------------
# VideoStreamer
# ---------------------------------------------------------------------------

class VideoStreamer:
    """Chạy ffmpeg chụp màn hình + nén H.264, cắt gói và gọi send_fn(bytes, addr) cho từng gói."""

    START_TIMEOUT = 5.0  # giây chờ khung đầu tiên trước khi chuyển sang đường dự phòng

    def __init__(self, send_fn, pipelines=None, ffmpeg=None, log=print):
        self.send_fn = send_fn
        self.pipelines = list(pipelines or DEFAULT_PIPELINES)
        self.ffmpeg = ffmpeg      # None = tìm lúc chạy (ffmpeg có thể vừa được tải về)
        self.log = log
        self.on_frame = None      # tuỳ chọn: hàm(au, frame_number, capture_us, idr) để đo/gỡ lỗi
        self.pipeline = None      # đường nén đang chạy
        self._good = {}           # chế độ ("screen"/"window") -> đường nén đã chạy được
        self.last_error = None
        self.frame_number = 0
        self.frames_sent = 0
        self.bytes_sent = 0
        self.params = {}
        self.addr = None
        self._lock = threading.Lock()       # start/stop
        self._proc_lock = threading.Lock()  # self._proc (thread chạy ffmpeg cũng dùng)
        self._proc = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._last_restart = 0.0
        atexit.register(self.stop)

    # --- API công khai ---

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def proc(self):
        return self._proc

    def start(self, addr, crop=None, width=1280, height=720, fps=60, bitrate_kbps=10000, window=None):
        """Bắt đầu phát tới addr=(ip, port). crop=(x, y, w, h) theo pixel màn hình hoặc None.
        window={"hwnd": ..., "crop": (trái, trên, phải, dưới)}: chỉ phát cửa sổ đó (ffmpeg 9)."""
        with self._lock:
            self._stop_locked()
            self.addr = addr
            self.params = dict(crop=crop, width=width, height=height, fps=fps,
                               bitrate_kbps=bitrate_kbps, window=window)
            self._stop_evt = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop_evt,),
                                            name="VideoStreamer", daemon=True)
            self._last_restart = time.monotonic()
            self._thread.start()

    def restart(self, **changes):
        """Đổi tham số (addr, crop, width, height, fps, bitrate_kbps) và khởi động lại ffmpeg."""
        if not self.params:
            raise RuntimeError("Chưa start()")
        params = dict(self.params)
        addr = changes.pop("addr", self.addr)
        bad = set(changes) - {"crop", "width", "height", "fps", "bitrate_kbps", "window"}
        if bad:
            raise TypeError(f"Tham số không hợp lệ: {', '.join(sorted(bad))}")
        params.update(changes)
        self.start(addr, **params)

    def set_addr(self, addr):
        """Đổi địa chỉ nhận mà không khởi động lại ffmpeg."""
        self.addr = addr

    def request_idr(self, min_interval=1.0):
        """Xin khung IDR ngay (ffmpeg không cho xin giữa chừng nên phải khởi động lại).
        Bỏ qua nếu vừa khởi động lại trong min_interval giây. Trả về True nếu đã khởi động lại."""
        if not self.running or time.monotonic() - self._last_restart < min_interval:
            return False
        self.restart()
        return True

    def stop(self):
        with self._lock:
            self._stop_locked()

    # --- bên trong ---

    def _stop_locked(self):
        self._stop_evt.set()
        with self._proc_lock:
            proc = self._proc
        self._kill(proc)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread = None
        self._proc = None

    def _log(self, msg):
        try:  # log hỏng (vd. console cp1258 không in được tiếng Việt) không được làm chết thread
            self.log(msg)
        except Exception:
            pass

    def _kill(self, proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(b"q")  # ffmpeg thoát êm khi nhận 'q'
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _spawn(self, pipeline):
        ff = self.ffmpeg or ffmpeg_path("window" if self.params.get("window") else "screen") or "ffmpeg"
        cmd = build_cmd(pipeline, ffmpeg=ff, **self.params)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
        _attach_to_job(proc)
        try:  # ưu tiên cao để khung không bị trễ khi game ngốn CPU
            import psutil
            psutil.Process(proc.pid).nice(psutil.HIGH_PRIORITY_CLASS)
        except Exception:
            pass
        return proc

    def _run(self, stop_evt):
        order = list(self.pipelines)
        mode = "window" if self.params.get("window") else "screen"
        good = self._good.get(mode)  # đường đã chạy được lần trước (riêng từng chế độ) lên đầu
        if good in order:
            order.remove(good)
            order.insert(0, good)
        idx = 0
        last_logged = None
        while not stop_evt.is_set():
            pipeline = order[idx]
            proc = self._spawn(pipeline)
            with self._proc_lock:
                if stop_evt.is_set():
                    self._kill(proc)
                    return
                self._proc = proc
            err_tail = collections.deque(maxlen=20)
            err_thread = threading.Thread(target=self._drain_stderr, args=(proc, err_tail), daemon=True)
            err_thread.start()
            got = self._pump(proc, stop_evt, pipeline)
            self._kill(proc)
            err_thread.join(timeout=1)
            if stop_evt.is_set():
                return
            err = "\n".join(err_tail).strip()
            self.last_error = f"{pipeline}: {err[-500:] or 'ffmpeg dừng'}"
            if self.last_error != last_logged:  # màn hình khoá lâu thì không in lặp lại
                self._log(f"[video] {self.last_error}")
                last_logged = self.last_error
            if got:
                idx = 0  # đang chạy thì đứt (đổi độ phân giải...): thử lại từ đường ưu tiên
            elif "access denied" in err.lower() or "access lost" in err.lower():
                # màn hình khoá / UAC / Ctrl+Alt+Del: không phải lỗi của đường nén, chờ rồi thử lại
                stop_evt.wait(1.0)
            else:
                idx += 1  # chưa ra khung nào: sang đường dự phòng
                if idx >= len(order):
                    idx = 0
                    stop_evt.wait(2.0)  # mọi đường đều hỏng: nghỉ rồi thử lại từ đầu
            stop_evt.wait(0.3)

    @staticmethod
    def _drain_stderr(proc, tail):
        # phải đọc liên tục: pipe stderr đầy thì ffmpeg đứng (h264_mf in cảnh báo dts mỗi khung)
        try:
            for line in proc.stderr:
                tail.append(line.decode("utf-8", "replace").rstrip())
        except Exception:
            pass

    def _pump(self, proc, stop_evt, pipeline):
        """Đọc stdout ffmpeg, gửi gói. Trả về True nếu đã gửi được ít nhất một khung."""
        fd = proc.stdout.fileno()
        framer = AnnexBFramer()
        got = False
        t0 = time.monotonic()
        watchdog = threading.Timer(self.START_TIMEOUT, lambda: got or self._kill_hard(proc))
        watchdog.daemon = True
        watchdog.start()
        try:
            while not stop_evt.is_set():
                try:
                    data = os.read(fd, 1 << 21)
                except OSError:
                    break
                if not data:
                    break
                now_us = time.perf_counter_ns() // 1000
                frames = framer.feed(data, _pipe_pending(fd) == 0)
                for au, idr in frames:
                    if not got:
                        got = True
                        self.pipeline = pipeline
                        self._good["window" if self.params.get("window") else "screen"] = pipeline
                        self.last_error = None
                        self._log(f"[video] đang phát bằng {pipeline} "
                                 f"(khung đầu sau {time.monotonic() - t0:.2f} s)")
                    self._send_frame(au, idr, now_us)
        finally:
            watchdog.cancel()
        return got

    @staticmethod
    def _kill_hard(proc):
        try:
            proc.kill()
        except Exception:
            pass

    def _send_frame(self, au, idr, capture_us):
        n = self.frame_number
        self.frame_number += 1
        addr = self.addr
        for pkt in packetize(au, n, capture_us, idr):
            try:
                self.send_fn(pkt, addr)
            except Exception as e:  # mạng lỗi tạm thời không được làm chết thread
                self.last_error = f"send: {e}"
        self.frames_sent += 1
        self.bytes_sent += len(au)
        cb = self.on_frame
        if cb:
            cb(au, n, capture_us, idr)
