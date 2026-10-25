"""Giữ cửa sổ đang phát lên điện thoại vẫn chạy khi người dùng bấm "–" (thu nhỏ) trên máy tính.

Windows ngừng vẽ cửa sổ đã thu nhỏ (không chụp được hình, nhiều game còn tự tạm dừng), và cũng không
vẽ phần cửa sổ nằm ngoài màn hình. Nên thay vì để nó thu nhỏ, ta "giấu": làm cửa sổ gần như trong
suốt (1/255) + chuột bấm xuyên qua, khôi phục nó (không giành focus) và đẩy xuống dưới cùng. Người dùng
thấy như đã thu nhỏ; Windows.Graphics.Capture vẫn chụp đủ nội dung (độ trong suốt không ảnh hưởng ảnh
chụp - đã thử với cửa sổ Direct3D và GDI). Bấm nút của nó trên thanh tác vụ / Alt+Tab tới nó thì trả
lại như cũ. Tay cầm ảo (XInput) vẫn điều khiển được vì không cần cửa sổ đang được chọn; phím bàn phím
gửi từ điện thoại thì luôn vào cửa sổ đang được chọn nên không điều khiển được cửa sổ đang giấu.
"""
import atexit
import ctypes
import threading
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.IsIconic.argtypes = [wintypes.HWND]
u32.IsWindow.argtypes = [wintypes.HWND]
u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetForegroundWindow.restype = wintypes.HWND
u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_uint]
u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
u32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
LWA_ALPHA = 0x2
SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = wintypes.HWND(1)
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


class WindowKeeper:
    """keeper.targets = các hwnd cần giữ (mỗi người chơi một cửa sổ). Chạy thread riêng, 20 lần/giây."""

    def __init__(self, log=print):
        self.log = log
        self.enabled = True
        self.targets = frozenset()
        self._hidden = {}   # hwnd -> (exstyle cũ, (key, alpha, flags) layered cũ hoặc None)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._run, name="WindowKeeper", daemon=True).start()
        atexit.register(self.close)

    @property
    def hidden(self):
        """Các cửa sổ đang được giấu (để cửa sổ PhonePad hiển thị)."""
        return list(self._hidden)

    def close(self):
        """Trả lại mọi cửa sổ đang giấu (gọi khi tắt PhonePad)."""
        self._stop.set()
        for hwnd in self.hidden:
            self.unhide(hwnd)

    def unhide(self, hwnd):
        with self._lock:
            h = self._hidden.pop(hwnd, None)
        if not h:
            return
        ex, layered = h
        if not u32.IsWindow(hwnd):
            return
        u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        if layered:
            u32.SetLayeredWindowAttributes(hwnd, *layered)
        u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)  # FRAMECHANGED
        self.log("[giữ cửa sổ] trả lại cửa sổ như cũ")

    def _hide(self, hwnd):
        ex = u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        layered = None
        if ex & WS_EX_LAYERED:  # cửa sổ vốn đã trong suốt kiểu layered: nhớ để trả lại đúng
            key, alpha, flags = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
            if u32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)):
                layered = (key.value, alpha.value, flags.value)
        # trong suốt TRƯỚC khi khôi phục để không thấy cửa sổ nháy lên
        u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        u32.SetLayeredWindowAttributes(hwnd, 0, 1, LWA_ALPHA)
        u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        u32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_FLAGS)
        with self._lock:
            self._hidden[hwnd] = (ex, layered)
        self.log("[giữ cửa sổ] cửa sổ bị thu nhỏ: giấu đi nhưng vẫn chạy cho điện thoại")

    def _run(self):
        while not self._stop.wait(0.05):
            try:
                targets = self.targets if self.enabled else frozenset()
                for hwnd in self.hidden:
                    if hwnd not in targets or not u32.IsWindow(hwnd):
                        self.unhide(hwnd)  # đổi cửa sổ / tắt tính năng / cửa sổ đã đóng
                fg = u32.GetForegroundWindow()
                for hwnd in targets:
                    if not hwnd or not u32.IsWindow(hwnd):
                        continue
                    if hwnd in self._hidden:
                        if fg == hwnd:
                            self.unhide(hwnd)  # người dùng bấm nút trên thanh tác vụ / Alt+Tab tới nó
                        elif u32.IsIconic(hwnd):
                            u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # bấm "–" lần nữa khi đang giấu
                            u32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_FLAGS)
                    elif u32.IsIconic(hwnd):
                        self._hide(hwnd)
            except Exception as e:  # không để lỗi làm chết thread
                self.log("[giữ cửa sổ] lỗi: %r" % e)
