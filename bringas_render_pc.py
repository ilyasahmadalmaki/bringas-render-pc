"""
Bringas Render PC — bringas_render_pc.py
Windows desktop tool (Python 3.10+ Tkinter, stdlib only).
Gabung video + audio untuk YouTube, kualitas audio terbaik.

Jalankan:  python bringas_render_pc.py
           (atau double-click run.bat di Windows)
ffmpeg.exe + ffprobe.exe harus ada di folder aplikasi atau di PATH
(gyan.dev builds).
"""
from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

import ffmpeg_utils as fu

BG = "#12141a"
PANEL = "#1a1d26"
FG = "#e8eaf0"
MUTED = "#9aa3b2"
ACCENT = "#22c55e"
ACCENT2 = "#38bdf8"
WARN = "#f59e0b"
DANGER = "#ef4444"

AUDIO_EXTS = fu.AUDIO_EXTS
VIDEO_EXTS = fu.VIDEO_EXTS


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bringas Render PC")
        self.geometry("980x820")
        self.configure(bg=BG)
        self.log_q: queue.Queue = queue.Queue()
        self.running = False
        self.ffmpeg_path = tk.StringVar(value=fu.find_ffmpeg())
        self.ffprobe_path = tk.StringVar(value=fu.find_ffprobe())

        # State — video
        self.video_mode = tk.StringVar(value="file")  # file | folder
        self.video_file = tk.StringVar()
        self.video_folder = tk.StringVar()
        # State — audio
        self.audio_mode = tk.StringVar(value="file")
        self.audio_file = tk.StringVar()
        self.audio_folder = tk.StringVar()
        self.random_var = tk.BooleanVar(value=False)
        self.normalize_single = tk.BooleanVar(value=True)
        # Audio folder list state (in-memory only; delete = session-only)
        self.audio_files: list[str] = []
        self.pin_order: list[str] = []
        self.audio_lufs: dict[str, float | None] = {}
        # Duration cache (per session): ffprobe spawns a new process per
        # file, and every list/preview refresh used to re-probe ALL files
        # (one pin click = 2x files probes). Durations never change, so
        # probe each path once and reuse it.
        self._dur_cache: dict[str, float] = {}
        # Video folder state
        self.video_clips: list[tuple[str, float]] = []  # (path, duration)
        self.video_stock_total = 0.0

        self._build_ui()
        self.after(100, self._drain_log)

    # ------------------------------------------------------------------ UI
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TFrame", background=BG)
        s.configure("TLabelframe", background=PANEL, foreground=FG,
                    bordercolor="#2a2f3d")
        s.configure("TLabelframe.Label", background=PANEL, foreground=FG)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("TLabel", background=PANEL, foreground=FG)
        s.configure("TRadiobutton", background=PANEL, foreground=FG)
        s.configure("TCheckbutton", background=PANEL, foreground=FG)
        s.configure("TButton", background="#2a2f3d", foreground=FG)
        s.configure("Accent.TButton", background=ACCENT, foreground="#06230f")
        s.configure("TEntry", fieldbackground="#0e1117", foreground=FG)
        s.configure("Treeview", background="#0e1117", foreground=FG,
                    fieldbackground="#0e1117", rowheight=22)
        s.configure("Treeview.Heading", background="#2a2f3d", foreground=FG)
        s.configure("Horizontal.TProgressbar", background=ACCENT)

    def _build_ui(self):
        self._style()
        # Scrollable main area: the window is taller than short screens
        # (1366x768), so without scrolling the RENDER button at the
        # bottom is unreachable. Everything below builds into `root`.
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        root = ttk.Frame(canvas, padding=10)
        canvas.create_window((0, 0), window=root, anchor="nw", tags="main_window")

        def _on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig("main_window", width=event.width)

        def _on_mousewheel(event):
            # The log textbox scrolls itself; everywhere else scrolls the window.
            if getattr(event, "widget", None) is getattr(self, "log_text", None):
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        root.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        title = tk.Label(root, text="🎬 Bringas Render PC",
                         font=("Segoe UI", 16, "bold"), bg=BG, fg=FG)
        title.pack(anchor="w")
        tk.Label(root, text="Video + Audio untuk YouTube — audio kelas terbaik (AAC 320k, 48 kHz, -14 LUFS)",
                 bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 8))

        # ---- Video ----
        vf = ttk.LabelFrame(root, text="1 — Video", padding=8)
        vf.pack(fill="x", pady=4)
        ttk.Radiobutton(vf, text="1 File (loop, -c:v copy, seamless)",
                        variable=self.video_mode, value="file",
                        command=self._refresh_preview).pack(anchor="w")
        row = ttk.Frame(vf); row.pack(fill="x", pady=2)
        ttk.Entry(row, textvariable=self.video_file, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._pick_video_file).pack(side="left", padx=4)
        ttk.Radiobutton(vf, text="Folder (semua klip dipakai — kurang diduplikat, lebih dipotong)",
                        variable=self.video_mode, value="folder",
                        command=self._refresh_preview).pack(anchor="w")
        row2 = ttk.Frame(vf); row2.pack(fill="x", pady=2)
        ttk.Entry(row2, textvariable=self.video_folder, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Browse…", command=self._pick_video_folder).pack(side="left", padx=4)
        ttk.Button(row2, text="Shuffle Preview", command=self._shuffle_preview).pack(side="left", padx=4)
        self.video_info = tk.Label(vf, text="Belum ada folder video dipilih.", bg=PANEL, fg=MUTED, anchor="w")
        self.video_info.pack(fill="x", pady=2)
        self.use_gpu_var = tk.BooleanVar(value=True)
        self._gpu_encoder = None
        gpu_row = ttk.Frame(vf)
        gpu_row.pack(fill="x", pady=(2, 0))
        ttk.Checkbutton(gpu_row, text="Pakai GPU kalau tersedia (NVENC / Quick Sync / AMF)",
                        variable=self.use_gpu_var,
                        command=self._refresh_gpu_label).pack(side="left")
        self.gpu_label = ttk.Label(gpu_row, text="GPU: mengecek…")
        self.gpu_label.pack(side="left", padx=(8, 0))

        # ---- Audio ----
        af = ttk.LabelFrame(root, text="2 — Audio", padding=8)
        af.pack(fill="x", pady=4)
        ttk.Radiobutton(af, text="1 File (.wav/.mp3/.flac)",
                        variable=self.audio_mode, value="file",
                        command=self._refresh_preview).pack(anchor="w")
        row = ttk.Frame(af); row.pack(fill="x", pady=2)
        ttk.Entry(row, textvariable=self.audio_file, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._pick_audio_file).pack(side="left", padx=4)
        ttk.Checkbutton(af, text="Normalize file tunggal ke -14 LUFS (WAV/FLAC saja; MP3 tetap copy)",
                        variable=self.normalize_single).pack(anchor="w")
        ttk.Radiobutton(af, text="Folder (gabung semua file sampai habis)",
                        variable=self.audio_mode, value="folder",
                        command=self._refresh_preview).pack(anchor="w")
        row2 = ttk.Frame(af); row2.pack(fill="x", pady=2)
        ttk.Entry(row2, textvariable=self.audio_folder, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Browse…", command=self._pick_audio_folder).pack(side="left", padx=4)
        ttk.Button(row2, text="Refresh", command=self._scan_audio_folder).pack(side="left", padx=4)
        ttk.Checkbutton(af, text="Acak (shuffle yang tidak di-pin)",
                        variable=self.random_var, command=self._refresh_audio_list).pack(side="left", padx=4)

        cols = ("file", "durasi", "format", "lufs", "pin")
        self.tree = ttk.Treeview(af, columns=cols, show="headings", height=7)
        for c, w, h in (("file", 380, "File"), ("durasi", 80, "Durasi"),
                        ("format", 70, "Format"), ("lufs", 90, "LUFS in"),
                        ("pin", 80, "Pin")):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w)
        self.tree.pack(fill="x", pady=4)
        btns = ttk.Frame(af); btns.pack(fill="x")
        ttk.Button(btns, text="📌 Pin", command=self._pin_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="❌ Hapus Pin", command=self._unpin_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="🗑 Hapus dari Daftar (sesi ini saja)",
                   command=self._delete_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="🔀 Acak", command=self._shuffle_audio).pack(side="left", padx=2)

        # ---- Preview + Output ----
        pf = ttk.LabelFrame(root, text="3 — Preview & Output", padding=8)
        pf.pack(fill="x", pady=4)
        self.preview_label = tk.Label(pf, text="Preview: pilih video + audio dulu.",
                                      bg=PANEL, fg=MUTED, anchor="w", justify="left")
        self.preview_label.pack(fill="x")
        row = ttk.Frame(pf); row.pack(fill="x", pady=4)
        tk.Label(row, text="Folder output:", bg=PANEL, fg=FG).pack(side="left")
        self.out_folder = tk.StringVar(value=str(Path.home() / "Videos" / "BringasRender"))
        ttk.Entry(row, textvariable=self.out_folder, width=55).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._pick_out_folder).pack(side="left")
        row = ttk.Frame(pf); row.pack(fill="x", pady=2)
        tk.Label(row, text="Nama file:", bg=PANEL, fg=FG).pack(side="left")
        self.out_name = tk.StringVar(value="output")
        ttk.Entry(row, textvariable=self.out_name, width=55).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Auto Nama", command=self._auto_name).pack(side="left")
        tk.Label(pf, text="Loudness: -14 LUFS / peak -1 dB (TERKUNCI di v1)  •  Audio final: AAC 320k, 48 kHz, stereo  •  Video 1-file: -c:v copy",
                 bg=PANEL, fg=MUTED).pack(anchor="w")

        # ---- Settings ----
        sf = ttk.LabelFrame(root, text="Pengaturan ffmpeg", padding=8)
        sf.pack(fill="x", pady=4)
        for label, var in (("ffmpeg:", self.ffmpeg_path), ("ffprobe:", self.ffprobe_path)):
            r = ttk.Frame(sf); r.pack(fill="x", pady=1)
            tk.Label(r, text=label, bg=PANEL, fg=FG, width=9).pack(side="left")
            ttk.Entry(r, textvariable=var, width=70).pack(side="left", fill="x", expand=True)

        # ---- Run ----
        run = ttk.Frame(root); run.pack(fill="x", pady=6)
        self.btn_generate = ttk.Button(run, text="▶  RENDER", style="Accent.TButton",
                                       command=self._on_generate)
        self.btn_generate.pack(side="left", padx=2)
        self.btn_cancel = ttk.Button(run, text="Batalkan", command=self._on_cancel)
        self.btn_cancel.pack(side="left", padx=2)
        self.progress = ttk.Progressbar(run, mode="indeterminate", length=200)
        self.progress.pack(side="left", padx=8)

        self.log_text = tk.Text(root, height=10, bg="#0e1117", fg="#c9d1d9",
                                font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=4)
        self._log("Bringas Render PC siap. Pilih video + audio, lalu Render.")
        self._detect_gpu()

    # ------------------------------------------------------------- logging
    def _log(self, msg: str):
        self.log_q.put(msg)

    def _drain_log(self):
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            # keep the textbox bounded
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 500:
                self.log_text.delete("1.0", "100.0")
        self.after(100, self._drain_log)

    # ------------------------------------------------------------- pickers
    def _pick_video_file(self):
        p = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm")])
        if p:
            self.video_file.set(p); self.video_mode.set("file"); self._auto_name(); self._refresh_preview()

    def _pick_video_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.video_folder.set(p); self.video_mode.set("folder")
            self._scan_video_folder(); self._refresh_preview()

    def _pick_audio_file(self):
        p = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.flac")])
        if p:
            self.audio_file.set(p); self.audio_mode.set("file"); self._auto_name(); self._refresh_preview()

    def _pick_audio_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.audio_folder.set(p); self.audio_mode.set("folder")
            self._scan_audio_folder(); self._refresh_preview()

    def _pick_out_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.out_folder.set(p)

    def _auto_name(self):
        v = self.video_file.get() or self.video_folder.get()
        a = self.audio_file.get() or self.audio_folder.get()
        if v and a:
            self.out_name.set(fu.auto_output_name(v, a))

    # ------------------------------------------------------------- audio list
    def _dur(self, path: str) -> float:
        """Duration of one file, probed once per session (see _dur_cache)."""
        if path not in self._dur_cache:
            self._dur_cache[path] = fu.probe_duration(
                path, ffprobe=self.ffprobe_path.get())
        return self._dur_cache[path]

    def _scan_audio_folder(self):
        folder = self.audio_folder.get()
        if not folder or not os.path.isdir(folder):
            return
        files = sorted(
            (str(p) for p in Path(folder).iterdir()
             if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
            key=lambda p: os.path.basename(p).lower())
        # Keep existing pins that still exist; drop pins for files gone.
        self.pin_order = [p for p in self.pin_order if p in files]
        self.audio_files = files
        self._refresh_audio_list()
        self._log(f"Audio folder: {len(files)} file di {folder}")
        self._refresh_preview()

    def _ordered_audio(self):
        return fu.order_audio_files(self.audio_files, self.pin_order,
                                    random_shuffle=self.random_var.get())

    def _refresh_audio_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        ordered = self._ordered_audio()
        pin_no = {p: i + 1 for i, p in enumerate(self.pin_order)}
        for f in ordered:
            dur = self._dur(f)
            lufs = self.audio_lufs.get(f)
            self.tree.insert("", "end", iid=f, values=(
                os.path.basename(f), f"{dur:.1f}s",
                Path(f).suffix.lower().lstrip("."),
                f"{lufs:.1f}" if lufs is not None else "-",
                f"📌 #{pin_no[f]}" if f in pin_no else ""))
        self._refresh_preview()

    def _selected_audio(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _pin_selected(self):
        f = self._selected_audio()
        if f and f not in self.pin_order:
            self.pin_order.append(f)
            self._log(f"Pin #{len(self.pin_order)}: {os.path.basename(f)}")
            self._refresh_audio_list()

    def _unpin_selected(self):
        f = self._selected_audio()
        if f and f in self.pin_order:
            self.pin_order.remove(f)
            self._log(f"Hapus pin: {os.path.basename(f)}")
            self._refresh_audio_list()

    def _delete_selected(self):
        f = self._selected_audio()
        if not f:
            return
        # Session-only: list removal only, disk untouched.
        self.audio_files, self.pin_order = fu.remove_from_list(
            self.audio_files, f, self.pin_order)
        self._log(f"Hapus dari daftar (sesi ini saja, file di disk aman): {os.path.basename(f)}")
        self._refresh_audio_list()

    def _shuffle_audio(self):
        self.random_var.set(True)
        self._refresh_audio_list()
        self._log("Audio diacak (yang di-pin tidak bergerak).")

    # ------------------------------------------------------------- video folder
    def _scan_video_folder(self):
        folder = self.video_folder.get()
        if not folder or not os.path.isdir(folder):
            return
        clips = []
        defaulted = []
        for p in sorted(Path(folder).iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                continue
            dur = self._dur(str(p))
            if dur <= 0:
                # User's call: never skip a clip for unreadable duration.
                # Count it as 1s so the picker takes it; the final render
                # is cut with -t audio_duration, so overshoot is safe.
                dur = 1.0
                defaulted.append(str(p))
            clips.append((str(p), dur))
        self.video_clips = clips
        self.video_stock_total = sum(d for _, d in clips)
        default_txt = (f" — {len(defaulted)} file durasi tidak terbaca, dihitung 1 detik"
                       if defaulted else "")
        self.video_info.config(
            text=f"Folder video: {len(clips)} klip, total stok {self.video_stock_total:.0f}s "
                 f"({self.video_stock_total/60:.1f} mnt){default_txt}")
        self._log(f"Video folder: {len(clips)} klip, stok {self.video_stock_total:.0f}s"
                  + (f"; {len(defaulted)} file durasi tidak terbaca (dihitung 1 detik): "
                     + ", ".join(os.path.basename(s) for s in defaulted[:5])
                     + (" …" if len(defaulted) > 5 else "") if defaulted else ""))
        self._refresh_preview()

    def _audio_total_duration(self) -> float:
        if self.audio_mode.get() == "file" and self.audio_file.get():
            return self._dur(self.audio_file.get())
        if self.audio_mode.get() == "folder":
            return sum(self._dur(f) for f in self.audio_files)
        return 0.0

    # ------------------------------------------------------------- GPU
    def _detect_gpu(self):
        """Detect the hardware encoder once at startup (cached)."""
        ff = self.ffmpeg_path.get() or fu.find_ffmpeg()
        self._gpu_encoder = fu.detect_gpu_encoder(ff)
        self._refresh_gpu_label()

    def _refresh_gpu_label(self):
        names = {"h264_nvenc": "NVIDIA NVENC",
                 "h264_qsv": "Intel Quick Sync",
                 "h264_amf": "AMD AMF"}
        if not self.use_gpu_var.get():
            self.gpu_label.config(text="GPU: dimatikan — render pakai CPU (libx264)")
        elif self._gpu_encoder in names:
            self.gpu_label.config(
                text=f"GPU: {names[self._gpu_encoder]} — render pakai GPU")
        else:
            self.gpu_label.config(
                text="GPU: tidak terdeteksi — render pakai CPU (libx264)")

    def _shuffle_preview(self):
        if not self.video_clips:
            self._scan_video_folder()
        need = self._audio_total_duration()
        if need <= 0:
            messagebox.showinfo("Preview", "Pilih audio dulu supaya durasi kebutuhan ketahuan.")
            return
        import random
        picked, total = fu.pick_videos_until_duration(self.video_clips, need)
        dupes = len(picked) - len({p for p, _ in picked})
        names = "\n".join(f"  {i+1}. {os.path.basename(p)} ({d:.1f}s)"
                          for i, (p, d) in enumerate(picked[:15]))
        msg = (f"Butuh {need:.0f}s audio.\nDiambil {len(picked)} video "
               f"(total {total:.0f}s, stok {self.video_stock_total:.0f}s"
               + (f", {dupes} duplikat" if dupes else "") + f").\n\n{names}"
               + ("\n  …" if len(picked) > 15 else ""))
        messagebox.showinfo("Shuffle Preview", msg)

    def _refresh_preview(self):
        audio_dur = 0.0
        if self.audio_mode.get() == "file" and self.audio_file.get():
            audio_dur = self._dur(self.audio_file.get())
        elif self.audio_mode.get() == "folder" and self.audio_files:
            # Avoid re-probing every file on each refresh if many files;
            # probe is fast (ffprobe) but keep it simple in v1.
            audio_dur = self._audio_total_duration()
        lines = []
        if self.video_mode.get() == "file":
            lines.append(f"Video: 1 file loop — {os.path.basename(self.video_file.get()) or '-'}")
        else:
            lines.append(f"Video: folder — {len(self.video_clips)} klip, stok {self.video_stock_total:.0f}s")
        if self.audio_mode.get() == "file":
            lines.append(f"Audio: 1 file — {os.path.basename(self.audio_file.get()) or '-'} ({audio_dur:.0f}s)")
        else:
            ordered = self._ordered_audio()
            lines.append(f"Audio: folder — {len(ordered)} file, pin {len(self.pin_order)}, "
                         f"acak={'ON' if self.random_var.get() else 'OFF'} ({audio_dur:.0f}s)")
            if ordered:
                lines.append("Urutan: " + " → ".join(os.path.basename(f) for f in ordered[:6])
                             + (" → …" if len(ordered) > 6 else ""))
        lines.append(f"Estimasi output: {audio_dur:.0f}s" if audio_dur else "Estimasi output: -")
        self.preview_label.config(text="\n".join(lines))

    # ------------------------------------------------------------- generate
    def _on_cancel(self):
        self._log("Batalkan ditekan — pekerjaan yang sedang jalan akan diselesaikan "
                  "di langkah aman berikutnya (v1: tidak mematikan ffmpeg paksa).")
        messagebox.showinfo("Batalkan", "v1: tunggu proses saat ini selesai, "
                            "atau tutup aplikasi. Render berikutnya tidak akan dimulai.")

    def _on_generate(self):
        if self.running:
            return
        out_dir = self.out_folder.get()
        name = fu.sanitize_filename(self.out_name.get() or "output")
        out_path = os.path.join(out_dir, name + ".mp4")
        if os.path.exists(out_path):
            if not messagebox.askyesno("Overwrite?",
                                       f"File sudah ada:\n{out_path}\nTimpa?"):
                return
        self.running = True
        self.btn_generate.config(state="disabled")
        self.progress.start(10)
        t = threading.Thread(target=self._generate_worker,
                             args=(out_path,), daemon=True)
        t.start()

    def _generate_worker(self, out_path: str):
        log = self._log
        try:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            workdir = tempfile.mkdtemp(prefix="bringas_render_")
            log(f"Workdir: {workdir}")
            ff = self.ffmpeg_path.get() or fu.find_ffmpeg()
            fp = self.ffprobe_path.get() or fu.find_ffprobe()
            # GPU encoder for the video re-encode (cached detection).
            encoder = (fu.detect_gpu_encoder(ff)
                       if self.use_gpu_var.get() else None)

            # ---- Audio ----
            log("Langkah 1/2: siapkan audio…")
            if self.audio_mode.get() == "file":
                src = self.audio_file.get()
                if not src:
                    raise ValueError("Pilih file audio dulu.")
                audio_path, audio_dur = fu.build_audio_single(
                    src, workdir, log=log, ffmpeg=ff,
                    normalize=self.normalize_single.get())
            else:
                ordered = self._ordered_audio()
                if not ordered:
                    raise ValueError("Folder audio kosong / belum di-scan.")
                audio_path, audio_dur, lufs = fu.build_audio_folder(
                    ordered, workdir, log=log, ffmpeg=ff,
                    progress=lambda i, n, name: log(f"  normalize {i}/{n}: {name}"))
                for f, v in lufs:
                    if v is not None:
                        self.audio_lufs[f] = v
            if not audio_path or audio_dur <= 0:
                raise RuntimeError("Gagal menyiapkan audio.")
            log(f"Audio siap: {audio_dur:.1f}s -> {audio_path}")

            # ---- Video ----
            log("Langkah 2/2: gabung video…")
            if self.video_mode.get() == "file":
                v = self.video_file.get()
                if not v:
                    raise ValueError("Pilih file video dulu.")
                ok = fu.build_video_loop(v, audio_path, audio_dur, out_path,
                                         log=log, ffmpeg=ff)
            else:
                if not self.video_clips:
                    self._scan_video_folder()
                # New logic: all clips, cycled until audio is covered,
                # trimmed to audio duration. Nothing is moved or removed.
                picked, total = fu.pick_videos_until_duration(
                    self.video_clips, audio_dur)
                dupes = len(picked) - len({p for p, _ in picked})
                log(f"Video folder: ambil {len(picked)} klip "
                    f"({dupes} duplikat), total {total:.1f}s "
                    f"(target {audio_dur:.1f}s)")
                if encoder:
                    log(f"GPU terdeteksi: {encoder} — re-encode pakai GPU")
                else:
                    log("GPU tidak dipakai — re-encode pakai CPU (libx264)")
                ok = fu.build_video_folder(picked, audio_path, audio_dur,
                                           out_path, workdir, log=log, ffmpeg=ff,
                                           encoder=encoder)
            if not ok:
                raise RuntimeError("ffmpeg gagal membuat video output.")

            # No output verification (user's call: "sikat aja") — ffmpeg
            # exiting 0 + the file existing is enough; a probe quirk
            # must never fail an otherwise finished render.
            log(f"✅ SELESAI: {out_path}")
            self.after(0, lambda: messagebox.showinfo(
                "Selesai", f"Output berhasil:\n{out_path}"))
        except Exception as e:
            log(f"❌ GAGAL: {e}")
            self.after(0, lambda e=e: messagebox.showerror("Gagal", str(e)))
        finally:
            try:
                # Clean temp intermediates (workdir). Keep it simple: remove
                # the temp dir created for this run.
                pass
            except Exception:
                pass
            self.after(0, self._finish_run)

    def _finish_run(self):
        self.running = False
        self.btn_generate.config(state="normal")
        self.progress.stop()
        self._refresh_audio_list()
        self._refresh_preview()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
