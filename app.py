"""YT Downloader - a simple GUI around yt-dlp.

Supports single videos, playlists and whole channels.
ffmpeg/ffprobe are picked up automatically if they sit next to the program.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import yt_dlp

APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

COOKIES_FILE = os.path.join(os.path.expanduser("~"), ".ytdl-gui", "cookies.txt")
BROWSERS = ["chrome", "edge", "firefox", "brave", "opera"]
BROWSER_EXES = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe", "opera": "opera.exe"}

QUALITIES = {
    "Best video (MP4)": {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
    },
    "Up to 1080p (MP4)": {
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
    },
    "Up to 720p (MP4)": {
        "format": "bv*[height<=720]+ba/b[height<=720]",
        "merge_output_format": "mp4",
    },
    "Up to 480p (MP4)": {
        "format": "bv*[height<=480]+ba/b[height<=480]",
        "merge_output_format": "mp4",
    },
    "Audio only (MP3)": {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    },
}


class Cancelled(Exception):
    pass


class QueueLogger:
    def __init__(self, q):
        self.q = q
        self.errors = 0

    def debug(self, msg):
        if not msg.startswith("[debug]"):
            self.q.put(("log", msg))

    def info(self, msg):
        self.q.put(("log", msg))

    def warning(self, msg):
        self.q.put(("log", "WARNING: " + msg))

    def error(self, msg):
        self.errors += 1
        self.q.put(("log", "ERROR: " + msg.replace("ERROR: ", "")))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YT Downloader (yt-dlp)")
        self.geometry("760x640")
        self.minsize(640, 520)

        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None

        self.outdir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self.quality = tk.StringVar(value=list(QUALITIES)[0])
        self.subfolders = tk.BooleanVar(value=True)
        self.use_archive = tk.BooleanVar(value=True)
        self.browser = tk.StringVar(value="chrome")
        self.use_cookies = tk.BooleanVar(value=os.path.exists(COOKIES_FILE))

        self._build_ui()
        self.after(100, self._poll)

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="Links (video / playlist / channel) - one per line:",
        ).pack(anchor="w", **pad)
        self.urls = scrolledtext.ScrolledText(main, height=6, wrap="none")
        self.urls.pack(fill="x", **pad)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Paste from clipboard", command=self._paste).pack(side="left")
        ttk.Button(row, text="Clear", command=self._clear_urls).pack(side="left", padx=6)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Save to:").pack(side="left")
        ttk.Entry(row, textvariable=self.outdir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left")

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Quality:").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.quality,
            values=list(QUALITIES),
            state="readonly",
            width=24,
        ).pack(side="left", padx=6)

        ttk.Checkbutton(
            main,
            text="Create sub-folders (playlist / channel name)",
            variable=self.subfolders,
        ).pack(anchor="w", **pad)
        ttk.Checkbutton(
            main,
            text="Skip files that were already downloaded (archive)",
            variable=self.use_archive,
        ).pack(anchor="w", **pad)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        self.btn_cookies = ttk.Button(
            row, text="Export cookies from browser", command=self._export_cookies
        )
        self.btn_cookies.pack(side="left")
        ttk.Combobox(
            row, textvariable=self.browser, values=BROWSERS, state="readonly", width=9
        ).pack(side="left", padx=6)
        ttk.Checkbutton(
            row, text="Use exported cookies when downloading", variable=self.use_cookies
        ).pack(side="left", padx=6)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Load cookies.txt file...", command=self._load_cookies_file).pack(side="left")
        ttk.Label(row, text="(fallback if browser export fails)").pack(side="left", padx=6)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        self.btn_start = ttk.Button(row, text="Download", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(main, maximum=100)
        self.progress.pack(fill="x", **pad)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status).pack(anchor="w", **pad)

        self.log = scrolledtext.ScrolledText(main, height=12, state="disabled")
        self.log.pack(fill="both", expand=True, **pad)

        self._log("Tip: for a channel, paste e.g. https://www.youtube.com/@name/videos")

    def _paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.status.set("Clipboard is empty or has no text")
            return
        links = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not links:
            self.status.set("Clipboard is empty or has no text")
            return
        existing = self.urls.get("1.0", "end").strip()
        if existing:
            self.urls.insert("end", "\n")
        self.urls.insert("end", "\n".join(links) + "\n")
        self.urls.see("end")
        self.status.set(f"Pasted {len(links)} link(s)")

    def _clear_urls(self):
        self.urls.delete("1.0", "end")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- actions ----------
    def _start(self):
        urls = [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("Missing link", "Please paste at least one link.")
            return
        outdir = self.outdir.get().strip()
        if not outdir:
            messagebox.showwarning("Missing folder", "Please choose a destination folder.")
            return
        os.makedirs(outdir, exist_ok=True)

        self.cancel.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress["value"] = 0
        self.worker = threading.Thread(
            target=self._download,
            args=(
                urls,
                outdir,
                self.quality.get(),
                self.subfolders.get(),
                self.use_archive.get(),
                self.use_cookies.get(),
            ),
            daemon=True,
        )
        self.worker.start()

    def _cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")

    def _download(self, urls, outdir, quality, subfolders, archive, use_cookies):
        def hook(d):
            if self.cancel.is_set():
                raise Cancelled()
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                pct = (d.get("downloaded_bytes", 0) / total * 100) if total else 0
                info = d.get("info_dict") or {}
                idx = info.get("playlist_index")
                count = info.get("n_entries")
                prefix = f"[{idx}/{count}] " if idx and count else ""
                text = "{}{}  {}  ETA {}".format(
                    prefix,
                    (d.get("_percent_str") or "").strip(),
                    (d.get("_speed_str") or "").strip(),
                    (d.get("_eta_str") or "").strip(),
                )
                self.q.put(("progress", pct, text))
            elif d["status"] == "finished":
                self.q.put(("progress", 100, "Processing..."))

        logger = QueueLogger(self.q)
        if subfolders:
            template = "%(playlist_title|Single videos)s/%(title)s [%(id)s].%(ext)s"
        else:
            template = "%(title)s [%(id)s].%(ext)s"

        opts = {
            "outtmpl": template,
            "paths": {"home": outdir},
            "progress_hooks": [hook],
            "logger": logger,
            "ignoreerrors": True,
            "windowsfilenames": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "noplaylist": False,
        }
        opts.update(QUALITIES[quality])
        if archive:
            opts["download_archive"] = os.path.join(outdir, "downloaded.txt")

        if use_cookies and os.path.exists(COOKIES_FILE):
            opts["cookiefile"] = COOKIES_FILE

        ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        if os.path.exists(os.path.join(APP_DIR, ffmpeg_name)):
            opts["ffmpeg_location"] = APP_DIR

        # YouTube needs a JavaScript runtime (deno) to solve its challenges
        deno_path = os.path.join(APP_DIR, "deno.exe" if os.name == "nt" else "deno")
        if os.path.exists(deno_path):
            opts["js_runtimes"] = {"deno": {"path": deno_path}}
        opts["remote_components"] = ["ejs:github"]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download(urls)
            if logger.errors:
                self.q.put(("done", f"Finished with {logger.errors} error(s) - see log"))
            else:
                self.q.put(("done", "Finished"))
        except Cancelled:
            self.q.put(("done", "Cancelled"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("log", f"ERROR: {e}"))
            self.q.put(("done", "Stopped because of an error"))

    def _export_cookies(self):
        browser = self.browser.get()
        close_first = False
        if os.name == "nt" and browser in BROWSER_EXES:
            close_first = messagebox.askyesno(
                "Close browser?",
                f"{browser} must be fully closed to read its cookies.\n\n"
                "Close it now automatically? (Tabs are usually restored when you reopen it.)",
            )
        self.btn_cookies.configure(state="disabled")
        self._log(f"Exporting cookies from {browser}...")
        threading.Thread(
            target=self._export_cookies_worker, args=(browser, close_first), daemon=True
        ).start()

    def _export_cookies_worker(self, browser, close_first):
        try:
            if close_first:
                self.q.put(("log", f"Closing {browser}..."))
                subprocess.run(
                    ["taskkill", "/F", "/IM", BROWSER_EXES[browser]],
                    capture_output=True,
                    creationflags=0x08000000,  # no console window
                )
                time.sleep(2)
            os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
            opts = {"cookiesfrombrowser": (browser,), "quiet": True, "logger": QueueLogger(self.q)}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.cookiejar.save(COOKIES_FILE, ignore_discard=True, ignore_expires=True)
                count = len(ydl.cookiejar)
            self.q.put(("cookies_ok", count))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            if "copy" in low or "decrypt" in low or "dpapi" in low:
                msg += (
                    "\nHint: recent Chrome versions block cookie access. Try Firefox or Edge in the"
                    " list, or export cookies.txt with a browser extension and use"
                    " 'Load cookies.txt file...'."
                )
            self.q.put(("cookies_fail", msg))

    def _load_cookies_file(self):
        path = filedialog.askopenfilename(
            title="Choose cookies.txt (Netscape format)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
            shutil.copyfile(path, COOKIES_FILE)
        except OSError as e:
            self._log(f"ERROR loading cookies file: {e}")
            return
        self.use_cookies.set(True)
        self._log(f"Loaded cookies file: {path}")
        self.status.set("Cookies loaded")

    # ---------- queue polling ----------
    def _poll(self):
        try:
            while True:
                kind, *data = self.q.get_nowait()
                if kind == "log":
                    self._log(data[0])
                elif kind == "progress":
                    self.progress["value"] = data[0]
                    self.status.set(data[1])
                elif kind == "cookies_ok":
                    self.btn_cookies.configure(state="normal")
                    self.use_cookies.set(True)
                    self._log(f"Exported {data[0]} cookies to {COOKIES_FILE}")
                    self.status.set("Cookies exported")
                elif kind == "cookies_fail":
                    self.btn_cookies.configure(state="normal")
                    self._log(f"ERROR exporting cookies: {data[0]}")
                    self.status.set("Cookie export failed")
                elif kind == "done":
                    self.status.set(data[0])
                    self._log(data[0])
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
