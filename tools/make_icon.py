"""Vẽ phonepad.ico (biểu tượng exe/cửa sổ) giống icon app Android: tay cầm xanh trên nền tối.

    python tools\\make_icon.py      -> phonepad.ico ở thư mục gốc dự án
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024 / 108  # vẽ to theo toạ độ 108x108 của ic_launcher.xml rồi thu nhỏ cho mịn


def p(*xy):
    return [v * S for v in xy]


img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle(p(4, 4, 104, 104), radius=22 * S, fill="#111318")
blue = "#3D8BFF"
d.rounded_rectangle(p(12, 38, 96, 70), radius=14 * S, fill=blue)          # thân
d.ellipse(p(10, 50, 34, 84), fill=blue)                                    # tay cầm trái
d.ellipse(p(74, 50, 98, 84), fill=blue)                                    # tay cầm phải
d.rectangle(p(34, 50, 40, 68), fill="white")                               # dấu cộng
d.rectangle(p(28, 56, 46, 62), fill="white")
d.ellipse(p(70, 48, 78, 56), fill="white")                                 # 2 nút
d.ellipse(p(78, 56, 86, 64), fill="white")

img.save(ROOT / "phonepad.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(ROOT / "phonepad.ico")
