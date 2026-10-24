"""Tải ffmpeg.exe cho tính năng xem hình máy tính (dùng trong cửa sổ PhonePad, không cần PowerShell).

Ghim bản gyan 7.1 "full_build" giống tools/fetch_ffmpeg.ps1: bản mới nhất còn dùng NVENC API 12.2
(driver NVIDIA >= 551.76), có ddagrab, h264_nvenc, h264_qsv, h264_mf.
Chỉ giữ lại bin/ffmpeg.exe và LICENSE (GPL) trong DATA_DIR/ffmpeg.
"""
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import paths

VERSION = "7.1"
URL = (f"https://github.com/GyanD/codexffmpeg/releases/download/{VERSION}/"
       f"ffmpeg-{VERSION}-full_build.zip")


def fetch(progress=None, url=URL):
    """Tải và giải nén. progress(done_bytes, total_bytes) được gọi trong lúc tải.
    Trả về đường dẫn ffmpeg.exe; lỗi thì ném exception."""
    dest = paths.DATA_DIR / "ffmpeg"
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
