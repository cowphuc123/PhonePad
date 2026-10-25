"""Cửa sổ trạng thái của PhonePad (thay cho trang mã QR trên trình duyệt trước đây).

Thread chính chạy Tk; máy chủ (aiohttp + UDP) chạy trong ServerThread. Mọi print() của chương
trình được chuyển vào ô nhật ký của cửa sổ (bản exe không có console).
"""
import ctypes
import json
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
import keeper
import paths
import server
import updater
import video_control
import winlist
from session import PAD_LIB
from version import VERSION

VIGEM_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"
GREEN, RED, GREY = "#1a7f37", "#c62828", "#666"
SAME_AS_DEFAULT = "Như ô Phát lên điện thoại"


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

        # mỗi điện thoại một dòng: số tay cầm (người chơi 1-4), có đang xem hình không, nguồn hình riêng
        tk.Label(root, text="Điện thoại:", font=("Segoe UI", 10, "bold")).grid(
            row=3, column=0, padx=14, sticky="nw")
        self.phone_box = tk.Frame(root)
        self.phone_box.grid(row=3, column=1, sticky="w", padx=(0, 14))
        self.phone_empty = tk.Label(self.phone_box, text="chưa có điện thoại nào", fg=GREY, font=("Segoe UI", 10))
        self.phone_empty.pack(anchor="w")
        self.phone_rows = {}     # addr -> (khung, nhãn, ô chọn nguồn)
        self.phone_order = ()
        self.cb_items = {}       # ô chọn nguồn -> các cửa sổ đang liệt kê trong ô đó

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
        self.ff_btn = ttk.Button(vf, command=self.download_ffmpeg)
        self.ff_bar = ttk.Progressbar(root, length=300, mode="determinate")

        # chọn phát cả màn hình hay chỉ một cửa sổ (kể cả khi cửa sổ đó bị che / không ở trên cùng)
        tk.Label(root, text="Phát lên điện thoại:", font=("Segoe UI", 10, "bold")).grid(row=6, column=0, **pad)
        sf = tk.Frame(root)
        sf.grid(row=6, column=1, sticky="we", padx=(0, 14))
        self.src = ttk.Combobox(sf, state="readonly", width=42, postcommand=self.refresh_windows)
        self.src.pack(side="left", fill="x", expand=True)
        self.src.bind("<<ComboboxSelected>>", self.on_source)
        # bấm "–" trên cửa sổ đó: giấu đi nhưng vẫn chạy (cửa sổ thu nhỏ thật thì không có hình) - keeper.py
        self.keep_var = tk.BooleanVar(value=self._setting("keep_minimized", True))
        ttk.Checkbutton(root, variable=self.keep_var, command=self.on_keep,
                        text="Bấm – trên cửa sổ đó thì giấu đi nhưng vẫn chạy cho điện thoại").grid(
            row=7, column=1, sticky="w")
        self.keeper = keeper.WindowKeeper(log=print)
        self.keeper.enabled = self.keep_var.get()
        self.src_lbl = tk.Label(root, font=("Segoe UI", 9), fg=GREY, justify="left", wraplength=300)
        self.src_lbl.grid(row=8, column=1, sticky="w")

        tk.Label(root, text="Nhật ký", font=("Segoe UI", 9), fg=GREY).grid(row=10, column=0, pady=(8, 0), **pad)
        self.log = ScrolledText(root, height=10, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.grid(row=11, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="nsew")
        bf = tk.Frame(root)
        bf.grid(row=12, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="we")
        ttk.Button(bf, text="Mở thư mục dữ liệu",
                   command=lambda: os.startfile(paths.DATA_DIR)).pack(side="left")
        ttk.Button(bf, text="Tắt PhonePad", command=self.quit).pack(side="right")
        self.upd_btn = ttk.Button(bf, command=self.start_update)  # hiện khi có bản mới
        self.ver_lbl = tk.Label(bf, text="v" + VERSION, fg=GREY, font=("Segoe UI", 9))
        self.ver_lbl.pack(side="right", padx=8)
        self.update_info = None
        self.updating = False
        root.columnconfigure(1, weight=1)
        root.rowconfigure(11, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        sys.stdout = _LogWriter(self.q, sys.__stdout__)
        sys.stderr = _LogWriter(self.q, sys.__stderr__)
        server.print_banner(args)
        print("Dữ liệu (bố cục, ffmpeg): %s" % paths.DATA_DIR)
        if getattr(args, "after_update", False):
            print("Đã cập nhật lên bản %s" % VERSION)
        self.load_source()
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

        vc = self.srv.app["video"]
        self.update_phones(vc)
        kind = self.ffmpeg_kind(vc)
        if vc is not None and kind is None:
            self.ff_btn.pack_forget()
            watching = vc.watching
            if len(watching) == 1:
                ip, pipe = watching[0]
                self.video_lbl.configure(text="đang phát tới %s (%s)" % (ip, pipe or "đang mở..."), fg=GREEN)
            elif watching:
                self.video_lbl.configure(text="đang phát tới %d điện thoại" % len(watching), fg=GREEN)
            else:
                self.video_lbl.configure(text="sẵn sàng (bấm 📺 trong app)", fg=GREEN)
        else:
            kind = kind or "screen"
            self.video_lbl.configure(text="cần ffmpeg " if not self.downloading else "đang tải ffmpeg... ",
                                     fg=RED if not self.downloading else GREY)
            if not self.downloading:
                self.ff_btn.configure(text="Tải ffmpeg (~%d MB)" % ffmpeg_fetch.size_mb(kind))
                self.ff_btn.pack(side="left")
        if vc is not None:
            keep = set()
            for t in vc.window_targets():
                vc.resolve(t)  # cập nhật trạng thái cửa sổ (tìm lại nếu game vừa mở lại)
                if t.get("state") in ("ok", "minimized"):
                    keep.add(t["hwnd"])
            self.keeper.targets = frozenset(keep)
        note, color = "", GREY
        if vc is not None and vc.target:
            state = vc.target.get("state", "")
            note = {
                "ok": "Chỉ cửa sổ này, kể cả khi bị che hay đang dùng app khác.",
                "minimized": "Cửa sổ đang thu nhỏ: điện thoại giữ hình cuối, mở cửa sổ lên là chạy tiếp.",
                "closed": "Cửa sổ đã đóng: chờ app mở lại (tự nhận ra).",
            }.get(state, "")
            color = RED if state == "closed" else GREY
        hidden = self.keeper.hidden
        if hidden:
            note = ("Đang giấu %s (đã bấm –): game vẫn chạy cho điện thoại. Bấm nút của nó trên "
                    "thanh tác vụ để hiện lại." % ("cửa sổ" if len(hidden) == 1 else "%d cửa sổ" % len(hidden)))
            color = GREY
        self.src_lbl.configure(text=note, fg=color)
        self.root.after(300, self.tick)

    def ffmpeg_kind(self, vc):
        """Bản ffmpeg còn thiếu cho các nguồn đang chọn ("screen"/"window"), None nếu đủ."""
        if vc is None:
            return "screen"
        ips = [addr[0] for addr in self.phone_rows] or [None]
        kinds = {"window" if (vc.source_for(ip) if ip else vc.target) else "screen" for ip in ips}
        for k in ("window", "screen"):
            if k in kinds and not vc.ffmpeg_ok(k):
                return k
        return None

    # --- danh sách điện thoại ---

    def update_phones(self, vc):
        udp = self.srv.app["udp"]
        try:
            peers = list(udp.peers.values()) if udp is not None else []
        except RuntimeError:  # dict đổi kích thước giữa chừng (thread máy chủ)
            return
        peers.sort(key=lambda p: (p.player or 9, p.addr))
        alive = {p.addr for p in peers}
        for addr in list(self.phone_rows):
            if addr not in alive:
                f, _, cb = self.phone_rows.pop(addr)
                self.cb_items.pop(str(cb), None)
                f.destroy()
        for p in peers:
            row = self.phone_rows.get(p.addr)
            if row is None:
                f = tk.Frame(self.phone_box)
                lbl = tk.Label(f, font=("Segoe UI", 10), width=24, anchor="w")
                lbl.pack(side="left")
                cb = ttk.Combobox(f, state="readonly", width=28)
                cb.configure(postcommand=lambda c=cb: self.fill_sources(c, per_phone=True))
                cb.bind("<<ComboboxSelected>>", lambda _e, ip=p.addr[0], c=cb: self.on_phone_source(ip, c))
                cb.pack(side="left", pady=1)
                cb.set(self.phone_source_label(vc, p.addr[0]))
                row = self.phone_rows[p.addr] = (f, lbl, cb)
            text = p.addr[0]
            if p.player:
                text += " · tay cầm %d" % p.player
            if vc is not None and vc.viewer(p.addr) is not None:
                text += " · 📺"
            row[1].configure(text=text)
        order = tuple(p.addr for p in peers)
        if order != self.phone_order:  # xếp lại theo số tay cầm
            self.phone_order = order
            for addr in order:
                self.phone_rows[addr][0].pack_forget()
            for addr in order:
                self.phone_rows[addr][0].pack(anchor="w")
            if order:
                self.phone_empty.pack_forget()
            else:
                self.phone_empty.pack(anchor="w")

    # --- chọn nguồn hình ---

    def _label(self, title, exe):
        t = title if len(title) <= 40 else title[:39] + "…"
        return "%s  —  %s" % (t, exe) if exe else t

    def refresh_windows(self):
        self.fill_sources(self.src)

    def fill_sources(self, cb, per_phone=False):
        items = winlist.list_windows()
        self.cb_items[str(cb)] = items
        head = [SAME_AS_DEFAULT, "Toàn màn hình"] if per_phone else ["Toàn màn hình"]
        cb.configure(values=head + [self._label(t, e) for _, t, e in items])

    def _picked(self, cb, skip):
        """Cửa sổ đã chọn trong ô cb ({"hwnd","title","exe"}); None nếu chọn một trong `skip` mục đầu."""
        i = cb.current() - skip
        items = self.cb_items.get(str(cb), [])
        if i < 0 or i >= len(items):
            return None
        hwnd, title, exe = items[i]
        return {"hwnd": hwnd, "title": title, "exe": exe}

    def on_source(self, _event=None):
        vc = self.srv.app["video"]
        target = self._picked(self.src, 1)
        if target is None:
            print("Phát: toàn màn hình")
        else:
            print('Phát: chỉ cửa sổ "%s" (%s)' % (target["title"], target["exe"]))
        if vc is not None:
            vc.target = target  # lần 'W' kế tiếp của điện thoại sẽ chuyển sang luồng theo nguồn mới
        self.save_source(target)

    def on_phone_source(self, ip, cb):
        """Điện thoại ip xem nguồn riêng (vd. Nucleus Co-op: mỗi người một cửa sổ game)."""
        vc = self.srv.app["video"]
        if vc is None:
            return
        i = cb.current()
        t = self._picked(cb, 2)
        if i <= 0:
            vc.sources.pop(ip, None)
            print("Điện thoại %s: phát như ô Phát lên điện thoại" % ip)
        elif i == 1:
            vc.sources[ip] = video_control.SCREEN
            print("Điện thoại %s: phát toàn màn hình" % ip)
        elif t is None:
            return
        else:
            vc.sources[ip] = t
            print('Điện thoại %s: chỉ cửa sổ "%s" (%s)' % (ip, t["title"], t["exe"]))
        self._save_setting("phone_sources", {
            k: v if v == video_control.SCREEN else {"exe": v["exe"], "title": v["title"]}
            for k, v in vc.sources.items()})

    def phone_source_label(self, vc, ip):
        s = vc.sources.get(ip) if vc is not None else None
        if s is None:
            return SAME_AS_DEFAULT
        if s == video_control.SCREEN:
            return "Toàn màn hình"
        return self._label(s["title"], s["exe"])

    def load_source(self):
        """Lần trước chọn cửa sổ nào thì tìm lại theo tên tiến trình (hwnd đổi mỗi lần mở app).
        Nhiều người chơi chọn nhiều cửa sổ của cùng một game: mỗi người một cửa sổ khác nhau."""
        vc = self.srv.app["video"]
        saved = self._setting("window")
        used = set()

        def find(saved):
            found = winlist.find(saved.get("exe", ""), saved.get("title", ""), exclude=used)
            if found:
                used.add(found[0])
            hwnd, title = (found[0], found[1]) if found else (0, saved.get("title", ""))
            return {"hwnd": hwnd, "title": title, "exe": saved.get("exe", "")}, found

        if not saved or vc is None:
            self.src.set("Toàn màn hình")
        else:
            vc.target, found = find(saved)
            self.src.set(self._label(vc.target["title"], vc.target["exe"]))
            print('Phát: chỉ cửa sổ "%s"%s' % (vc.target["title"], "" if found else " (chưa mở)"))
        if vc is None:
            return
        for ip, s in (self._setting("phone_sources") or {}).items():
            if s == video_control.SCREEN:
                vc.sources[ip] = s
            elif isinstance(s, dict):
                vc.sources[ip] = find(s)[0]

    def save_source(self, target):
        self._save_setting("window", {"exe": target["exe"], "title": target["title"]} if target else None)

    def on_keep(self):
        self.keeper.enabled = self.keep_var.get()
        self._save_setting("keep_minimized", self.keeper.enabled)

    def _setting(self, key, default=None):
        try:
            return json.loads(paths.SETTINGS.read_text(encoding="utf-8")).get(key, default)
        except (OSError, ValueError):
            return default

    def _save_setting(self, key, value):
        try:
            data = json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[key] = value
        try:
            paths.SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print("Không lưu được lựa chọn: %s" % e)

    # --- hành động ---

    def download_ffmpeg(self):
        if self.downloading:
            return
        self.downloading = True
        self.ff_btn.pack_forget()
        self.ff_bar.grid(row=9, column=1, sticky="w", pady=(2, 0))
        kind = self.ffmpeg_kind(self.srv.app["video"]) or "screen"
        version, url = ffmpeg_fetch.KINDS[kind][:2]
        print("Đang tải ffmpeg %s từ %s" % (version, url))

        def work():
            err = None
            try:
                exe = ffmpeg_fetch.fetch(lambda d, t: self.q.put(("progress", (d, t))), kind=kind)
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
        self.keeper.close()  # cửa sổ đang giấu phải hiện lại, không thì nó trong suốt mãi
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
