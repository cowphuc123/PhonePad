"""Tự cập nhật bản exe từ GitHub Releases.

check()  : hỏi API GitHub bản mới nhất; trả về UpdateInfo nếu mới hơn version.VERSION.
apply()  : tải exe mới cạnh exe đang chạy, kiểm tra kích thước + SHA-256 (trường "digest" của
           GitHub), đổi tên exe đang chạy thành *.old (Windows cho đổi tên file exe đang chạy,
           không cho ghi đè), đặt exe mới vào đúng tên cũ. Người gọi tắt máy chủ rồi mở exe mới.
cleanup(): lần chạy sau xoá *.old.
PHONEPAD_UPDATE_URL (biến môi trường) thay địa chỉ API, để thử với release giả.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import paths
from version import VERSION

REPO = "cowphuc123/PhonePad"
API = os.environ.get("PHONEPAD_UPDATE_URL") or "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES = "https://github.com/%s/releases/latest" % REPO


@dataclass
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str  # "" nếu GitHub không ghi
    page: str


def _parse(v):
    return [int(x) for x in re.findall(r"\d+", v)]


def newer(a, b):
    """a mới hơn b? ("0.10" > "0.9")"""
    x, y = _parse(a), _parse(b)
    n = max(len(x), len(y))
    return x + [0] * (n - len(x)) > y + [0] * (n - len(y))


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "PhonePad/" + VERSION,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check():
    """Trả về UpdateInfo nếu có bản mới hơn, None nếu đang mới nhất. Lỗi mạng thì ném exception."""
    with _get(API, 10) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    if not newer(tag, VERSION):
        return None
    for a in rel.get("assets", []):
        if re.match(r"PhonePad-PC-.*\.exe$", a.get("name", "")):
            digest = a.get("digest") or ""
            return UpdateInfo(tag.lstrip("v"), a["browser_download_url"], int(a["size"]),
                              digest[7:] if digest.startswith("sha256:") else "",
                              rel.get("html_url") or RELEASES)
    return None


def can_self_update():
    return paths.FROZEN


def apply(info, progress=None):
    """Tải và thay exe. Trả về đường dẫn exe (mới) để mở lại. Lỗi thì exe cũ vẫn nguyên."""
    exe = Path(sys.executable)
    new = exe.with_name(exe.stem + ".new.exe")
    old = exe.with_name(exe.name + ".old")
    h = hashlib.sha256()
    done = 0
    try:
        with _get(info.url, 30) as r, open(new, "wb") as f:
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, info.size)
        if done != info.size:
            raise RuntimeError("Tải thiếu (%d/%d byte)" % (done, info.size))
        if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
            raise RuntimeError("File tải về bị hỏng (SHA-256 không khớp)")
        if old.exists():
            old.unlink()
        exe.rename(old)
        try:
            new.rename(exe)
        except OSError:
            old.rename(exe)  # trả lại exe cũ
            raise
    finally:
        if new.exists():
            try:
                new.unlink()
            except OSError:
                pass
    return exe


def relaunch(exe):
    """Mở exe mới, tách khỏi tiến trình hiện tại (tiến trình này sắp thoát)."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # Không đặt biến này thì bản mới tưởng mình là tiến trình con của bản cũ và chạy luôn bằng
    # thư mục giải nén tạm (_MEI...) của bản cũ, tức là vẫn chạy mã cũ (PyInstaller >= 6.9).
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([str(exe), "--after-update"], creationflags=flags, close_fds=True,
                     cwd=str(Path(exe).parent), env=env)


def cleanup():
    if paths.FROZEN:
        old = Path(sys.executable + ".old")
        try:
            if old.exists():
                old.unlink()
        except OSError:
            pass  # bản cũ còn đang thoát; lần sau xoá
