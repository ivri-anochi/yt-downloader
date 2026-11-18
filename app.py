"""מוריד יוטיוב - ממשק גרפי בעברית ל-yt-dlp.

תומך בסרטונים בודדים, פלייליסטים וערוצים שלמים.
ffmpeg, ffprobe ו-deno נמצאים אוטומטית אם הם בתיקייה של התוכנה.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk

import yt_dlp

APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

COOKIES_FILE = os.path.join(os.path.expanduser("~"), ".ytdl-gui", "cookies.txt")
BROWSERS = ["chrome", "edge", "firefox", "brave", "opera"]
BROWSER_EXES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
}

QUALITIES = {
    "הכי טובה (MP4)": {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
    },
    "עד 1080p (MP4)": {
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
    },
    "עד 720p (MP4)": {
        "format": "bv*[height<=720]+ba/b[height<=720]",
        "merge_output_format": "mp4",
    },
    "עד 480p (MP4)": {
        "format": "bv*[height<=480]+ba/b[height<=480]",
        "merge_output_format": "mp4",
    },
    "אודיו בלבד (MP3)": {
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


def rtl_check(parent, text, var):
    """תיבת סימון עם הריבוע בצד ימין והטקסט משמאלו."""
    f = ttk.Frame(parent)
    ttk.Checkbutton(f, variable=var).pack(side="right")
    lbl = ttk.Label(f, text=text, cursor="hand2")
    lbl.pack(side="right")
    lbl.bind("<Button-1>", lambda _e: var.set(not var.get()))
    return f


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("מוריד יוטיוב (yt-dlp)")
        self.geometry("820x780")
        self.minsize(700, 640)

        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None

        self.outdir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self.quality = tk.StringVar(value=list(QUALITIES)[0])
        self.subfolders = tk.BooleanVar(value=True)
        self.use_archive = tk.BooleanVar(value=True)
        self.browser = tk.StringVar(value="chrome")
        self.use_cookies = tk.BooleanVar(value=os.path.exists(COOKIES_FILE))

        self._setup_style()
        self._build_ui()
        self.after(100, self._poll)

    # ---------- עיצוב ----------
    def _setup_style(self):
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        tkfont.nametofont("TkDefaultFont").configure(family="Segoe UI", size=10)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Sub.TLabel", foreground="#666666")
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(22, 7))

    # ---------- ממשק ----------
    def _build_ui(self):
        main = ttk.Frame(self, padding=14)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="מוריד יוטיוב", style="Title.TLabel").pack(anchor="e")
        ttk.Label(
            main,
            text="סרטונים, פלייליסטים וערוצים שלמים - מבוסס yt-dlp",
            style="Sub.TLabel",
        ).pack(anchor="e", pady=(0, 10))

        # --- קישורים ---
        box = ttk.LabelFrame(main, text="קישורים (שורה לכל קישור)", labelanchor="ne", padding=8)
        box.pack(fill="x", pady=(0, 8))
        self.urls = scrolledtext.ScrolledText(box, height=5, wrap="none", font=("Consolas", 10))
        self.urls.pack(fill="x")
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="הדבק מהלוח", command=self._paste).pack(side="right")
        ttk.Button(btns, text="נקה", command=self._clear_urls).pack(side="right", padx=6)

        # --- הגדרות ---
        opts = ttk.LabelFrame(main, text="הגדרות", labelanchor="ne", padding=8)
        opts.pack(fill="x", pady=(0, 8))
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="תיקיית שמירה:").grid(row=0, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Entry(opts, textvariable=self.outdir).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(opts, text="עיון...", command=self._browse).grid(row=0, column=0, padx=(0, 6), pady=4)

        ttk.Label(opts, text="איכות:").grid(row=1, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Combobox(
            opts,
            textvariable=self.quality,
            values=list(QUALITIES),
            state="readonly",
            width=22,
            justify="right",
        ).grid(row=1, column=1, sticky="e", pady=4)

        rtl_check(opts, "צור תיקיות משנה (שם הפלייליסט / הערוץ)", self.subfolders).grid(
            row=2, column=1, columnspan=2, sticky="e", pady=2
        )
        rtl_check(opts, "דלג על קבצים שכבר הורדו (ארכיון)", self.use_archive).grid(
            row=3, column=1, columnspan=2, sticky="e", pady=2
        )

        # --- עוגיות ---
        ck = ttk.LabelFrame(main, text="עוגיות (Cookies) - לסרטונים מוגבלים", labelanchor="ne", padding=8)
        ck.pack(fill="x", pady=(0, 8))

        r1 = ttk.Frame(ck)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="דפדפן:").pack(side="right")
        ttk.Combobox(r1, textvariable=self.browser, values=BROWSERS, state="readonly", width=9).pack(
            side="right", padx=6
        )
        self.btn_cookies = ttk.Button(r1, text="ייצוא עוגיות מהדפדפן", command=self._export_cookies)
        self.btn_cookies.pack(side="right", padx=6)

        r2 = ttk.Frame(ck)
        r2.pack(fill="x", pady=2)
        ttk.Button(r2, text="טעינת קובץ cookies.txt...", command=self._load_cookies_file).pack(side="right")
        ttk.Label(r2, text="(אם הייצוא מהדפדפן נכשל)", style="Sub.TLabel").pack(side="right", padx=8)

        rtl_check(ck, "השתמש בעוגיות בעת ההורדה", self.use_cookies).pack(anchor="e", pady=2)

        # --- הפעלה ---
        act = ttk.Frame(main)
        act.pack(fill="x", pady=(4, 6))
        self.btn_start = ttk.Button(act, text="הורד", style="Accent.TButton", command=self._start)
        self.btn_start.pack(side="right")
        self.btn_cancel = ttk.Button(act, text="ביטול", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="right", padx=8)
        ttk.Button(act, text="פתח תיקיית יעד", command=self._open_folder).pack(side="right")

        self.progress = ttk.Progressbar(main, maximum=100)
        self.progress.pack(fill="x", pady=(2, 4))
        self.status = tk.StringVar(value="מוכן")
        ttk.Label(main, textvariable=self.status, anchor="e", justify="right").pack(fill="x")

        # --- יומן ---
        lf = ttk.LabelFrame(main, text="יומן", labelanchor="ne", padding=6)
        lf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = scrolledtext.ScrolledText(lf, height=10, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        self._log("טיפ: לערוץ שלם הדביקו כתובת כמו https://www.youtube.com/@name/videos")

    # ---------- פעולות עזר ----------
    def _paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.status.set("הלוח ריק או שאין בו טקסט")
            return
        links = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not links:
            self.status.set("הלוח ריק או שאין בו טקסט")
            return
        if self.urls.get("1.0", "end").strip():
            self.urls.insert("end", "\n")
        self.urls.insert("end", "\n".join(links) + "\n")
        self.urls.see("end")
        self.status.set(f"הודבקו {len(links)} קישורים")

    def _clear_urls(self):
        self.urls.delete("1.0", "end")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    def _open_folder(self):
        path = self.outdir.get().strip()
        if not os.path.isdir(path):
            self.status.set("התיקייה עדיין לא קיימת")
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- הורדה ----------
    def _start(self):
        urls = [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("חסר קישור", "נא להדביק לפחות קישור אחד.")
            return
        outdir = self.outdir.get().strip()
        if not outdir:
            messagebox.showwarning("חסרה תיקייה", "נא לבחור תיקיית יעד.")
            return
        os.makedirs(outdir, exist_ok=True)

        self.cancel.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress["value"] = 0
        self.status.set("מתחיל...")
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
        self.status.set("מבטל...")

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
                text = "{}{}   {}   נותרו {}".format(
                    prefix,
                    (d.get("_percent_str") or "").strip(),
                    (d.get("_speed_str") or "").strip(),
                    (d.get("_eta_str") or "").strip(),
                )
                self.q.put(("progress", pct, text))
            elif d["status"] == "finished":
                self.q.put(("progress", 100, "מעבד את הקובץ..."))

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

        # יוטיוב דורש סביבת JavaScript (deno) לפענוח האתגרים שלה
        deno_path = os.path.join(APP_DIR, "deno.exe" if os.name == "nt" else "deno")
        if os.path.exists(deno_path):
            opts["js_runtimes"] = {"deno": {"path": deno_path}}
        opts["remote_components"] = ["ejs:github"]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download(urls)
            if logger.errors:
                self.q.put(("done", f"הסתיים עם {logger.errors} שגיאות - ראו ביומן"))
            else:
                self.q.put(("done", "הסתיים בהצלחה"))
        except Cancelled:
            self.q.put(("done", "בוטל"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("log", f"ERROR: {e}"))
            self.q.put(("done", "נעצר עקב שגיאה"))

    # ---------- עוגיות ----------
    def _export_cookies(self):
        browser = self.browser.get()
        close_first = False
        if os.name == "nt" and browser in BROWSER_EXES:
            close_first = messagebox.askyesno(
                "לסגור את הדפדפן?",
                f"כדי לקרוא את העוגיות צריך לסגור את {browser} לחלוטין.\n\n"
                "לסגור אותו עכשיו אוטומטית? (בדרך כלל הלשוניות חוזרות בפתיחה מחדש)",
            )
        self.btn_cookies.configure(state="disabled")
        self._log(f"מייצא עוגיות מ-{browser}...")
        threading.Thread(
            target=self._export_cookies_worker, args=(browser, close_first), daemon=True
        ).start()

    def _export_cookies_worker(self, browser, close_first):
        try:
            if close_first:
                self.q.put(("log", f"סוגר את {browser}..."))
                subprocess.run(
                    ["taskkill", "/F", "/IM", BROWSER_EXES[browser]],
                    capture_output=True,
                    creationflags=0x08000000,  # בלי חלון קונסולה
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
                    "\nרמז: גרסאות חדשות של כרום חוסמות גישה לעוגיות. נסו Firefox או Edge ברשימה,"
                    " או ייצאו cookies.txt בעזרת תוסף דפדפן וטענו אותו בכפתור 'טעינת קובץ cookies.txt'."
                )
            self.q.put(("cookies_fail", msg))

    def _load_cookies_file(self):
        path = filedialog.askopenfilename(
            title="בחרו קובץ cookies.txt (פורמט Netscape)",
            filetypes=[("קבצי טקסט", "*.txt"), ("כל הקבצים", "*.*")],
        )
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
            shutil.copyfile(path, COOKIES_FILE)
        except OSError as e:
            self._log(f"ERROR: טעינת קובץ העוגיות נכשלה: {e}")
            return
        self.use_cookies.set(True)
        self._log(f"נטען קובץ עוגיות: {path}")
        self.status.set("העוגיות נטענו")

    # ---------- קריאת התור ----------
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
                    self._log(f"יוצאו {data[0]} עוגיות אל {COOKIES_FILE}")
                    self.status.set("העוגיות יוצאו בהצלחה")
                elif kind == "cookies_fail":
                    self.btn_cookies.configure(state="normal")
                    self._log(f"ERROR: ייצוא העוגיות נכשל: {data[0]}")
                    self.status.set("ייצוא העוגיות נכשל")
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
