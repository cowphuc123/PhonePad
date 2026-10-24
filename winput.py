"""Gửi phím / chuột giả lập trên Windows bằng SendInput (dùng scan code để game nhận được)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

# Tên phím theo chuẩn KeyboardEvent.code -> (scan code, extended?)
SCAN = {
    "Escape": (0x01, False), "Minus": (0x0C, False), "Equal": (0x0D, False), "Backspace": (0x0E, False),
    "Tab": (0x0F, False), "BracketLeft": (0x1A, False), "BracketRight": (0x1B, False), "Enter": (0x1C, False),
    "ControlLeft": (0x1D, False), "Semicolon": (0x27, False), "Quote": (0x28, False), "Backquote": (0x29, False),
    "ShiftLeft": (0x2A, False), "Backslash": (0x2B, False), "Comma": (0x33, False), "Period": (0x34, False),
    "Slash": (0x35, False), "ShiftRight": (0x36, False), "NumpadMultiply": (0x37, False), "AltLeft": (0x38, False),
    "Space": (0x39, False), "CapsLock": (0x3A, False), "NumLock": (0x45, False), "ScrollLock": (0x46, False),
    "Numpad7": (0x47, False), "Numpad8": (0x48, False), "Numpad9": (0x49, False), "NumpadSubtract": (0x4A, False),
    "Numpad4": (0x4B, False), "Numpad5": (0x4C, False), "Numpad6": (0x4D, False), "NumpadAdd": (0x4E, False),
    "Numpad1": (0x4F, False), "Numpad2": (0x50, False), "Numpad3": (0x51, False), "Numpad0": (0x52, False),
    "NumpadDecimal": (0x53, False), "F11": (0x57, False), "F12": (0x58, False),
    "ControlRight": (0x1D, True), "AltRight": (0x38, True), "NumpadEnter": (0x1C, True),
    "NumpadDivide": (0x35, True), "ArrowUp": (0x48, True), "ArrowDown": (0x50, True),
    "ArrowLeft": (0x4B, True), "ArrowRight": (0x4D, True), "Insert": (0x52, True), "Delete": (0x53, True),
    "Home": (0x47, True), "End": (0x4F, True), "PageUp": (0x49, True), "PageDown": (0x51, True),
    "MetaLeft": (0x5B, True),
}
for _ch, _sc in zip("QWERTYUIOP", range(0x10, 0x1A)):
    SCAN["Key" + _ch] = (_sc, False)
for _ch, _sc in zip("ASDFGHJKL", range(0x1E, 0x27)):
    SCAN["Key" + _ch] = (_sc, False)
for _ch, _sc in zip("ZXCVBNM", range(0x2C, 0x33)):
    SCAN["Key" + _ch] = (_sc, False)
for _i, _d in enumerate("1234567890"):
    SCAN["Digit" + _d] = (0x02 + _i, False)
for _i in range(10):
    SCAN["F%d" % (_i + 1)] = (0x3B + _i, False)

# Nút chuột: tên -> (cờ nhấn, cờ nhả)
MOUSE = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key(code, down):
    entry = SCAN.get(code)
    if entry is None:
        return False
    sc, ext = entry
    flags = KEYEVENTF_SCANCODE
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(0, sc, flags, 0, 0)
    _send(inp)
    return True


def mouse_button(name, down):
    flags = MOUSE.get(name)
    if flags is None:
        return False
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(0, 0, 0, flags[0] if down else flags[1], 0, 0)
    _send(inp)
    return True
