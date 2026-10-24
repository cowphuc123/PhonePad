"""Cửa sổ trạng thái của PhonePad (thay cho trang mã QR trên trình duyệt trước đây).

Thread chính chạy Tk; máy chủ (aiohttp + UDP) chạy trong ServerThread. Mọi print() của chương
trình được chuyển vào ô nhật ký của cửa sổ (bản exe không có console).
"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import ffmpeg_fetch
import paths
import server
import updater
from session import PAD_LIB
from version import VERSION

VIGEM_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"
GREEN, RED, GREY = "#1a7f37", "#c62828", "#666"


class _LogWriter:
    """Thay sys.stdout/stderr: đẩy chữ vào hàng đợi cho cửa sổ, vẫn in ra console nếu có."""

    def __init__(self, q, orig):
        self.q = q
        self.orig = orig

    def write(self, s):
        if s:
            self.q.put(("log", s))
            if self.orig is not None:
                try:
                    self.orig.write(s)
                except Exception:
                    pass
        return len(s)

    def flush(self):
        pass


class StatusWindow:
    def __init__(self, root, srv, args):
        self.root = root
        self.srv = srv
        self.args = args
        self.q = queue.Queue()
        self.downloading = False

        root.title("PhonePad")
        root.minsize(460, 420)
        try:
            root.iconbitmap(default=str(paths.RES_DIR / "phonepad.ico"))
        except tk.TclError:
            pass
        pad = {"padx": 14, "sticky": "w"}

        tk.Label(root, text="PhonePad đang chạy", font=("Segoe UI", 15, "bold"),
                 fg=GREEN).grid(row=0, column=0, columnspan=2, pady=(12, 2), **pad)
        tk.Label(root, justify="left", wraplength=430, font=("Segoe UI", 10),
                 text="Mở app PhonePad trên điện thoại (cùng Wi-Fi với máy này) - app tự tìm thấy "
                      "máy tính. Không thấy thì nhập IP:").grid(row=1, column=0, columnspan=2, **pad)
        ips = tk.Entry(root, font=("Consolas", 13, "bold"), relief="flat", bd=0,
                       readonlybackground=root.cget("bg"))
        ips.insert(0, "   ".join(server.lan_ips()))
        ips.configure(state="readonly")
        ips.grid(row=2, column=0, columnspan=2, padx=14, pady=(2, 8), sticky="we")

        tk.Label(root, text="Điện thoại:", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, **pad)
        self.phones = tk.Label(root, font=("Segoe UI", 10), justify="left")
        self.phones.grid(row=3, column=1, sticky="w")

        tk.Label(root, text="Tay cầm Xbox ảo:", font=("Segoe UI", 10, "bold")).grid(row=4, column=0, **pad)
        pf = tk.Frame(root)
        pf.grid(row=4, column=1, sticky="w")
        if PAD_LIB:
            tk.Label(pf, text="sẵn sàng", fg=GREEN, font=("Segoe UI", 10)).pack(side="left")
        else:
            tk.Label(pf, text="chưa có driver ViGEmBus ", fg=RED, font=("Segoe UI", 10)).pack(side="left")
            ttk.Button(pf, text="Cài driver", command=self.install_vigem).pack(side="left")

        tk.Label(root, text="Hình máy tính:", font=("Segoe UI", 10, "bold")).grid(row=5, column=0, **pad)
        vf = tk.Frame(root)
        vf.grid(row=5, column=1, sticky="w")
        self.video_lbl = tk.Label(vf, font=("Segoe UI", 10))
        self.video_lbl.pack(side="left")
        self.ff_btn = ttk.Button(vf, text="Tải ffmpeg (~170 MB)", command=self.download_ffmpeg)
        self.ff_bar = ttk.Progressbar(root, length=300, mode="determinate")

        tk.Label(root, text="Nhật ký", font=("Segoe UI", 9), fg=GREY).grid(row=7, column=0, pady=(8, 0), **pad)
        self.log = ScrolledText(root, height=10, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.grid(row=8, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="nsew")
        bf = tk.Frame(root)
        bf.grid(row=9, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="we")
        ttk.Button(bf, text="Mở thư mục dữ liệu",
                   command=lambda: os.startfile(paths.DATA_DIR)).pack(side="left")
        ttk.Button(bf, text="Tắt PhonePad", command=self.quit).pack(side="right")
        self.upd_btn = ttk.Button(bf, command=self.start_update)  # hiện khi có bản mới
        self.ver_lbl = tk.Label(bf, text="v" + VERSION, fg=GREY, font=("Segoe UI", 9))
        self.ver_lbl.pack(side="right", padx=8)
        self.update_info = None
        self.updating = False
        root.columnconfigure(1, weight=1)
        root.rowconfigure(8, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        sys.stdout = _LogWriter(self.q, sys.__stdout__)
        sys.stderr = _LogWriter(self.q, sys.__stderr__)
        server.print_banner(args)
        print("Dữ liệu (bố cục, ffmpeg): %s" % paths.DATA_DIR)
        if getattr(args, "after_update", False):
            print("Đã cập nhật lên bản %s" % VERSION)
        self.tick()
        root.after(2000, self.check_update)

    # --- cập nhật định kỳ ---

    def tick(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", val)
                    if float(self.log.index("end")) > 600:
                        self.log.delete("1.0", "200.0")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "progress":
                    done, total = val
                    self.ff_bar.configure(maximum=max(total, 1), value=done)
                elif kind == "update_found":
                    self.update_info = val
                    self.upd_btn.configure(text="⬆ Cập nhật lên v%s" % val.version)
                    self.upd_btn.pack(side="right")
                elif kind == "update_progress":
                    done, total = val
                    self.upd_btn.configure(text="Đang tải %d%%" % (done * 100 // max(total, 1)))
                elif kind == "update_failed":
                    self.updating = False
                    self.upd_btn.configure(text="⬆ Cập nhật lên v%s" % self.update_info.version)
                    messagebox.showerror("PhonePad", "Cập nhật không được:\n%s\n\nCó thể tải tay tại:\n%s"
                                         % (val, self.update_info.page))
                elif kind == "update_ready":
                    print("Mở bản mới...")
                    self.quit(relaunch=val)
                    return
                elif kind == "ffmpeg_done":
                    self.downloading = False
                    self.ff_bar.grid_forget()
                    if val:
                        messagebox.showerror("PhonePad", "Tải ffmpeg không được:\n%s" % val)
        except queue.Empty:
            pass

        names = server.phones(self.srv.app)
        self.phones.configure(text="\n".join(names) if names else "chưa có điện thoại nào",
                              fg="black" if names else GREY)
        vc = self.srv.app["video"]
        if vc is not None and vc.ffmpeg_ok():
            self.ff_btn.pack_forget()
            ip, pipe = vc.watching
            if ip:
                self.video_lbl.configure(text="đang phát tới %s (%s)" % (ip, pipe or "đang mở..."), fg=GREEN)
            else:
                self.video_lbl.configure(text="sẵn sàng (bấm 📺 trong app)", fg=GREEN)
        else:
            self.video_lbl.configure(text="cần ffmpeg " if not self.downloading else "đang tải ffmpeg... ",
                                     fg=RED if not self.downloading else GREY)
            if not self.downloading:
                self.ff_btn.pack(side="left")
        self.root.after(300, self.tick)

    # --- hành động ---

    def download_ffmpeg(self):
        if self.downloading:
            return
        self.downloading = True
        self.ff_btn.pack_forget()
        self.ff_bar.grid(row=6, column=1, sticky="w", pady=(2, 0))
        print("Đang tải ffmpeg %s từ %s" % (ffmpeg_fetch.VERSION, ffmpeg_fetch.URL))

        def work():
            err = None
            try:
                exe = ffmpeg_fetch.fetch(lambda d, t: self.q.put(("progress", (d, t))))
                print("Đã tải ffmpeg: %s" % exe)
            except Exception as e:
                err = str(e)
                print("Tải ffmpeg lỗi: %s" % e)
            self.q.put(("ffmpeg_done", err))

        threading.Thread(target=work, daemon=True).start()

    def install_vigem(self):
        """Chạy bộ cài ViGEmBus đi kèm gói vgamepad (Windows tự hỏi quyền Administrator)."""
        msi = vigem_msi()
        if msi is None:
            webbrowser.open(VIGEM_URL)
            return
        try:
            os.startfile(msi)
        except OSError as e:
            messagebox.showerror("PhonePad", "Không chạy được bộ cài driver:\n%s" % e)
            return
        messagebox.showinfo("PhonePad", "Cài xong driver ViGEmBus thì tắt PhonePad và mở lại "
                                        "để dùng tay cầm Xbox ảo.")

    def check_update(self):
        """Hỏi GitHub có bản mới không (lúc mở và mỗi 6 giờ); lỗi mạng thì im lặng."""
        def work():
            try:
                info = updater.check()
            except Exception as e:
                print("Không kiểm tra được bản cập nhật: %s" % e)
                return
            if info is not None:
                print("Có bản mới: v%s" % info.version)
                self.q.put(("update_found", info))
        if self.update_info is None:
            threading.Thread(target=work, daemon=True).start()
        self.root.after(6 * 3600 * 1000, self.check_update)

    def start_update(self):
        info = self.update_info
        if info is None or self.updating:
            return
        if not updater.can_self_update():  # chạy từ mã nguồn: chỉ mở trang tải
            webbrowser.open(info.page)
            return
        if not messagebox.askyesno("PhonePad", "Tải bản v%s (%.1f MB), PhonePad sẽ tự tắt và mở lại.\n"
                                   "Nhớ cập nhật cả app trên điện thoại.\n\nCập nhật ngay?"
                                   % (info.version, info.size / 1e6)):
            return
        self.updating = True
        print("Đang tải bản v%s: %s" % (info.version, info.url))

        def work():
            try:
                exe = updater.apply(info, lambda d, t: self.q.put(("update_progress", (d, t))))
            except Exception as e:
                print("Cập nhật lỗi: %s" % e)
                self.q.put(("update_failed", str(e)))
                return
            self.q.put(("update_ready", exe))

        threading.Thread(target=work, daemon=True).start()

    def quit(self, relaunch=None):
        print("Đang tắt...")
        self.root.update()
        self.srv.stop()  # nhả cổng 8765/8766 trước khi bản mới mở
        if relaunch is not None:
            try:
                updater.relaunch(relaunch)
            except OSError as e:
                messagebox.showerror("PhonePad", "Đã cập nhật nhưng không tự mở lại được (%s).\n"
                                                 "Hãy mở lại PhonePad." % e)
        self.root.destroy()


def vigem_msi():
    import importlib.util
    import platform
    arch = "x64" if platform.machine().endswith("64") else "x86"
    rel = ("vgamepad", "win", "vigem", "install", arch, "ViGEmBusSetup_%s.msi" % arch)
    bases = [paths.RES_DIR]
    try:
        spec = importlib.util.find_spec("vgamepad")
        if spec and spec.submodule_search_locations:
            bases.append(Path(list(spec.submodule_search_locations)[0]).parent)
    except Exception:
        pass
    for b in bases:
        msi = b.joinpath(*rel)
        if msi.exists():
            return str(msi)
    return None


def run(args):
    try:  # chữ nét trên màn hình phóng to; ddagrab cũng cần toạ độ pixel thật
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    updater.cleanup()
    srv = server.ServerThread(args.port, args.udp_port)
    err = srv.start()
    # vừa tự cập nhật: bản cũ có thể chưa thoát hẳn, chờ nó nhả cổng
    for _ in range(20 if getattr(args, "after_update", False) else 0):
        if not err:
            break
        time.sleep(0.5)
        srv = server.ServerThread(args.port, args.udp_port)
        err = srv.start()
    root = tk.Tk()
    if err:
        root.withdraw()
        messagebox.showerror("PhonePad", err)
        root.destroy()
        return 1
    StatusWindow(root, srv, args)
    root.mainloop()
    return 0
