"""Tải ffmpeg.exe cho tính năng xem hình máy tính (dùng trong cửa sổ PhonePad, không cần PowerShell).

Hai bản, đều của gyan (GitHub GyanD/codexffmpeg), chỉ giữ lại bin/ffmpeg.exe và LICENSE (GPL):
  "screen": 7.1 full_build -> DATA_DIR/ffmpeg. Phát cả màn hình (ddagrab). Bản mới nhất còn dùng
            NVENC API 12.2 (driver NVIDIA >= 551.76), giống tools/fetch_ffmpeg.ps1.
  "window": 9.0.2 essentials -> DATA_DIR/ffmpeg9. Chỉ phát một cửa sổ (gfxcapture, có từ ffmpeg 8);
            NVENC của bản này cần driver >= 610, driver cũ hơn thì tự dùng Quick Sync / Media Foundation.
"""
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import paths

KINDS = {
    "screen": ("7.1", "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-full_build.zip",
               "ffmpeg", 177),
    "window": ("9.0.2", "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
                        "ffmpeg-9.0.2-essentials_build.zip", "ffmpeg9", 115),
}
VERSION, URL = KINDS["screen"][:2]


def size_mb(kind):
    return KINDS[kind][3]


def fetch(progress=None, kind="screen"):
    """Tải và giải nén. progress(done_bytes, total_bytes) được gọi trong lúc tải.
    Trả về đường dẫn ffmpeg.exe; lỗi thì ném exception."""
    _, url, folder, _ = KINDS[kind]
    dest = paths.DATA_DIR / folder
    with tempfile.TemporaryDirectory(prefix="phonepad_ffmpeg_") as tmp:
        arc = Path(tmp) / "ffmpeg.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "PhonePad"})
        with urllib.request.urlopen(req, timeout=30) as r, open(arc, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        with zipfile.ZipFile(arc) as z:
            want = {}
            for name in z.namelist():
                tail = name.split("/", 1)[-1]
                if tail in ("bin/ffmpeg.exe", "LICENSE"):
                    want[tail.rsplit("/", 1)[-1]] = name
            if "ffmpeg.exe" not in want:
                raise RuntimeError("Gói tải về không có bin/ffmpeg.exe")
            part = Path(tmp) / "out"
            part.mkdir()
            for out_name, member in want.items():
                with z.open(member) as src, open(part / out_name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(part), str(dest))
    return str(dest / "ffmpeg.exe")
