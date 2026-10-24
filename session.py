"""Trạng thái một điện thoại đang kết nối (dùng chung cho WebSocket và UDP)."""
import winput

try:
    import vgamepad as vg
    PAD_LIB = True
    PAD_BUTTONS = {
        "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A, "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
        "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X, "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
        "LB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "Back": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK, "Start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
        "Guide": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
        "LS": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB, "RS": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
        "DUp": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP, "DDown": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
        "DLeft": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT, "DRight": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    }
except Exception:  # vgamepad chưa cài hoặc thiếu driver ViGEmBus
    vg = None
    PAD_LIB = False
    PAD_BUTTONS = {}


class Session:
    """Trạng thái của một điện thoại đang kết nối. Mỗi điện thoại có tay cầm ảo riêng."""

    def __init__(self, peer):
        self.peer = peer
        self.held = {}  # hành động -> số nút đang giữ nó (2 nút cùng gán W thì chỉ nhả khi cả 2 nhả)
        self.pad = None
        self.pad_error = None

    def get_pad(self):
        if self.pad is None and self.pad_error is None:
            if not PAD_LIB:
                self.pad_error = "Chưa cài vgamepad / ViGEmBus trên máy tính"
            else:
                try:
                    self.pad = vg.VX360Gamepad()
                except Exception as e:
                    self.pad_error = "Không tạo được tay cầm ảo: %s" % e
        return self.pad

    def press(self, act):
        n = self.held.get(act, 0)
        self.held[act] = n + 1
        if n == 0:
            self._apply(act, True)

    def release(self, act):
        n = self.held.get(act, 0)
        if n <= 0:
            return
        if n == 1:
            del self.held[act]
            self._apply(act, False)
        else:
            self.held[act] = n - 1

    def release_all(self):
        for act in list(self.held):
            self._apply(act, False)
        self.held.clear()
        if self.pad is not None:
            self.pad.reset()
            self.pad.update()

    def _apply(self, act, down):
        kind, _, name = act.partition(":")
        if kind == "k":
            winput.key(name, down)
        elif kind == "m":
            winput.mouse_button(name, down)
        elif kind == "g":
            pad = self.get_pad()
            btn = PAD_BUTTONS.get(name)
            if pad is not None and btn is not None:
                (pad.press_button if down else pad.release_button)(button=btn)
                pad.update()
        elif kind == "t":
            pad = self.get_pad()
            if pad is not None:
                v = 1.0 if down else 0.0
                if name == "LT":
                    pad.left_trigger_float(value_float=v)
                else:
                    pad.right_trigger_float(value_float=v)
                pad.update()

    def analog(self, side, x, y):
        pad = self.get_pad()
        if pad is None:
            return
        x = max(-1.0, min(1.0, x))
        y = max(-1.0, min(1.0, -y))  # điện thoại: y hướng xuống; Xbox: y hướng lên
        if side == "L":
            pad.left_joystick_float(x_value_float=x, y_value_float=y)
        else:
            pad.right_joystick_float(x_value_float=x, y_value_float=y)
        pad.update()

    def close(self):
        self.release_all()
        self.pad = None
