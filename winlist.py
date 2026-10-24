"""Liệt kê cửa sổ ứng dụng và lấy vùng nội dung của cửa sổ (để chỉ phát hình một app).

Toạ độ là pixel thật của màn hình: tiến trình phải bật DPI awareness (gui.run làm việc này)
thì GetClientRect/ClientToScreen mới không bị Windows co giãn.
"""
import ctypes
import os
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi")

u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.IsWindow.argtypes = [wintypes.HWND]
u32.IsIconic.argtypes = [wintypes.HWND]
u32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
u32.GetWindow.restype = wintypes.HWND
u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
DWMWA_CLOAKED = 14
_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def title(hwnd):
    n = u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def process_name(hwnd):
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(h)


def _cloaked(hwnd):
    """Cửa sổ UWP chạy nền / ở desktop ảo khác: 'hiện' nhưng không thấy trên màn hình."""
    v = wintypes.DWORD()
    return dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0


def list_windows():
    """[(hwnd, title, process_name)] của các cửa sổ ứng dụng đang mở, theo thứ tự trên-xuống-dưới."""
    out = []
    me = os.getpid()

    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd) or u32.GetWindow(hwnd, GW_OWNER):
            return True
        if u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd):
            return True
        t = title(hwnd)
        if not t:
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            return True  # bỏ chính cửa sổ PhonePad
        out.append((hwnd, t, process_name(hwnd)))
        return True

    u32.EnumWindows(_ENUM(cb), 0)
    return out


def alive(hwnd):
    return bool(hwnd) and bool(u32.IsWindow(hwnd))


def minimized(hwnd):
    return bool(u32.IsIconic(hwnd))


def client_rect(hwnd):
    """Vùng nội dung (không có viền, thanh tiêu đề) theo toạ độ màn hình: (x, y, w, h)."""
    r = wintypes.RECT()
    if not u32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    p = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y, r.right - r.left, r.bottom - r.top


def find(process, title_hint=""):
    """Tìm lại cửa sổ đã chọn lần trước theo tên tiến trình (tiêu đề hay đổi, vd. tên map)."""
    best = None
    for hwnd, t, p in list_windows():
        if p.lower() != process.lower():
            continue
        if t == title_hint:
            return hwnd, t, p
        best = best or (hwnd, t, p)
    return best
