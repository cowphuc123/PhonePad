"""Đường dẫn dùng chung cho bản chạy từ mã nguồn và bản exe (PyInstaller một file).

RES_DIR  : file đi kèm chương trình (static/, layouts mặc định) - chỉ đọc.
DATA_DIR : nơi ghi dữ liệu (layouts.json, ffmpeg tải về).
           Bản exe chạy từ thư mục tạm nên phải ghi vào %LOCALAPPDATA%\\PhonePad;
           chạy từ mã nguồn thì ghi ngay trong thư mục dự án như trước.
"""
import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
SRC_DIR = Path(__file__).resolve().parent
RES_DIR = Path(getattr(sys, "_MEIPASS", SRC_DIR))

if FROZEN:
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PhonePad"
else:
    DATA_DIR = SRC_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATIC = RES_DIR / "static"
LAYOUTS = DATA_DIR / "layouts.json"
SETTINGS = DATA_DIR / "settings.json"  # lựa chọn trên cửa sổ PhonePad (vd. cửa sổ để phát)
FFMPEG_DIRS = [DATA_DIR / "ffmpeg", DATA_DIR / "tools" / "ffmpeg" / "bin"]
FFMPEG9_DIRS = [DATA_DIR / "ffmpeg9", DATA_DIR / "tools" / "ffmpeg9" / "bin"]  # chụp riêng cửa sổ


def ensure_layouts():
    """Lần đầu chạy exe: chép bố cục mẫu đi kèm ra DATA_DIR."""
    default = RES_DIR / "layouts.default.json"
    if not LAYOUTS.exists() and default.exists():
        shutil.copyfile(default, LAYOUTS)
