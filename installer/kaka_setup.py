# -*- coding: utf-8 -*-
"""Kaka Setup - the windowed installer for Kaka by Abhinaba Chanda.

Kaka-Setup.exe is a friendly window around the same installer that lives in
Kaka-Setup-V1.1.bat: the window asks the questions (folder, repair/uninstall,
name, city, options ...) and then runs the bat's own PowerShell part with those
answers, showing its live output, a progress bar and the current step. So the
exe and the bat always install Kaka exactly the same way.

Copyright (C) 2026 Abhinaba Chanda
https://github.com/abhinaba-chanda/Kaka
"""
from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

APP_TITLE = "Kaka Setup"
AUTHOR = "Abhinaba Chanda"
REPO_URL = "https://github.com/abhinaba-chanda/Kaka"
PROFILE_URL = "https://github.com/abhinaba-chanda"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
IS_WIN = sys.platform == "win32"
DEFAULT_FOLDER = "C:\\Kaka" if IS_WIN else os.path.join(str(Path.home()), "Kaka")
NEED_GB = 3
KAKA_ITEMS = ['app', 'Kaka-src', 'docs', '.venv', 'Kaka', 'Data', 'Tools', 'Kaka-Setup.bat', 'README.md', 'LICENSE', '.gitignore']
CREATE_NO_WINDOW = 0x08000000

# the bat's "Choose options" step: same keys, labels and defaults
OPTIONS = [
    ("desktop", True, 'Desktop shortcut "Kaka"'),
    ("startup", False, "Start Kaka when Windows starts"),
    ("browsers", True, "Browser control (Playwright Chromium + Firefox, ~400 MB)"),
    ("ffmpeg", True, "Screen recording: ffmpeg (~190 MB download, {ff})"),
    ("face", True, "Face greet + chor alarm (face models, ~10 MB)"),
    ("gestures", True, "Hand gestures (MediaPipe + hand model, ~45 MB)"),
    ("discord", True, "Discord phone remote + alarm alerts (discord.py, ~3 MB)"),
    ("wake", True, 'Wake word "Kaka" (Vosk + English model, ~40 MB)'),
]

STEP_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s+(.*\S)")
APP_RE = re.compile(r"^\s*\[ OK \] Kaka app:\s+(.*\S)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# ---------------------------------------------------------------- resources
def res_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def res(*parts) -> Path:
    base = res_dir()
    p = base.joinpath(*parts)
    if not p.exists() and parts and parts[0] == "engine":      # running from source
        q = base.joinpath("build", *parts[1:])
        if q.exists():
            return q
    return p


def bundled_python():
    """The Python 3.13 installer packed into the exe (used before downloading)."""
    for d in (res("engine"), res_dir() / "build"):
        try:
            for f in sorted(d.glob("python-3.13.*-amd64.exe")):
                m = re.match(r"python-(3\.13\.\d+)-amd64\.exe$", f.name)
                if m:
                    return f, m.group(1)
        except Exception:
            pass
    return None, None


def log_file() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "kaka-setup-log.txt"


# ---------------------------------------------------------------- the same checks the bat makes
def clean_name(n) -> str:
    s = str(n or "")
    s = re.sub(r"[\x00-\x1f\x7f\"'`{}\[\]<>\\|]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40].strip()


def user_env(name: str) -> str:
    if not IS_WIN:
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0] or "")
    except Exception:
        return ""


def fixed_drives():
    out = []
    if not IS_WIN:
        return out
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = chr(65 + i) + ":\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3 and os.path.isdir(root):
                    out.append(root)
    except Exception:
        pass
    return out


def pjoin(a: str, b: str) -> str:
    if not IS_WIN:
        b = b.replace("\\", "/")
    return os.path.join(a, b)


def has_kaka(folder) -> bool:
    return bool(folder) and os.path.isfile(pjoin(folder, "app\\main.py"))


def known_installs():
    cands = [os.environ.get("KAKA_ROOT") or user_env("KAKA_ROOT")]
    cands += [os.path.join(d, "Kaka") for d in fixed_drives()]
    cands.append(os.path.join(os.environ.get("USERPROFILE") or str(Path.home()), "Kaka"))
    seen, out = set(), []
    for c in cands:
        if c and has_kaka(c) and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def normalize_folder(raw: str, suggest: str):
    """The bat's folder rules, so the window shows the folder the installer will
    really use. Returns (folder, older_kaka_folder_or_None)."""
    f = (raw or "").strip().strip('"') or suggest
    try:
        f = os.path.abspath(f)
    except Exception:
        return None, None
    f = f.rstrip("\\/") or f
    if len(f) <= 2:
        f = f + ("\\Kaka" if IS_WIN else "/Kaka")
    elif os.path.basename(f).lower() == "app" and os.path.isfile(os.path.join(f, "main.py")):
        f = os.path.dirname(f)
    old_app = None
    if os.path.exists(f) and not has_kaka(f):
        try:
            for d in sorted(os.listdir(f)):
                full = os.path.join(f, d)
                if os.path.isdir(full) and has_kaka(full):
                    old_app = full
                    break
        except Exception:
            pass
    if os.path.exists(f) and not old_app and not has_kaka(f):
        try:
            items = [x for x in os.listdir(f) if x not in KAKA_ITEMS]
        except Exception:
            items = []
        if items:
            f = os.path.join(f, "Kaka")
    return f, old_app


def read_settings(folder: str) -> dict:
    try:
        with open(pjoin(folder, "app\\config\\api_keys.json"), "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def free_gb(path: str) -> float:
    try:
        p = path
        while p and not os.path.exists(p):
            up = os.path.dirname(p)
            if up == p:
                break
            p = up
        return round(shutil.disk_usage(p).free / (1024 ** 3), 1)
    except Exception:
        return -1


def writable(folder: str) -> bool:
    """Same test as the bat: make the folder, write and delete a small file.
    A folder made only for this test is removed again (the install makes it)."""
    made = []
    try:
        p = os.path.abspath(folder)
        while p and not os.path.exists(p):
            made.append(p)
            up = os.path.dirname(p)
            if up == p:
                break
            p = up
        os.makedirs(folder, exist_ok=True)
        t = os.path.join(folder, ".kaka-write-test-%d" % os.getpid())
        with open(t, "w") as fh:
            fh.write("ok")
        os.remove(t)
        return True
    except Exception:
        return False
    finally:
        for d in made:
            try:
                os.rmdir(d)
            except Exception:
                pass


def startup_lnk() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Kaka.lnk")


def probe_pc() -> dict:
    """Webcam and GPU, read the same way the bat reads them (for the option defaults)."""
    out = {"cams": [], "gpus": [], "ok": False}
    if not IS_WIN:
        return out
    ps = ("$c=@(); $g=@(); "
          "try { $c=@(Get-CimInstance Win32_PnPEntity -ErrorAction Stop | Where-Object { ($_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image') -and $_.Status -eq 'OK' } | ForEach-Object { $_.Name }) } catch { }; "
          "try { $g=@(Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object { $_.Name }) } catch { }; "
          "@{cams=$c; gpus=$g} | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=40, creationflags=CREATE_NO_WINDOW)
        d = json.loads(r.stdout.strip() or "{}")
        lst = lambda v: [] if v is None else (v if isinstance(v, list) else [v])
        out = {"cams": lst(d.get("cams")), "gpus": lst(d.get("gpus")), "ok": True}
    except Exception:
        pass
    return out


def save_gemini_key(app_dir: str, key: str):
    """Put the Gemini key into app\\config\\api_keys.json ("gemini_api_key"),
    keeping every other setting. Refuses to write a file it cannot read."""
    cfg = os.path.join(app_dir, "config", "api_keys.json")
    data = {}
    if os.path.exists(cfg):
        try:
            with open(cfg, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return False, "the settings file is not a JSON object, so it was not changed"
        except Exception as e:
            return False, "the settings file could not be read (%s), so it was not changed" % e
    data["gemini_api_key"] = key
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    tmp = cfg + ".tmp-setup"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, cfg)
    return True, cfg


def append_to_log(line: str):
    try:
        with open(log_file(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- the engine (the bat's PowerShell part)
class Engine:
    """Runs kaka_setup_engine.ps1 (the bat's PowerShell part, made by
    build_engine.py) with the window's answers and streams every line back."""

    def __init__(self, folder_raw, mode, answers, options_csv, name, city, on_line, on_ask, on_launch, on_done,
                 powershell=None):
        self.folder_raw, self.mode, self.answers = folder_raw, mode, answers
        self.options_csv, self.name, self.city = options_csv, name, city
        self.on_line, self.on_ask, self.on_launch, self.on_done = on_line, on_ask, on_launch, on_done
        self.powershell = powershell
        self.proc = None
        self.cancelled = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def answer(self, text: str):
        try:
            self.proc.stdin.write((str(text).replace("\r", " ").replace("\n", " ") + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except Exception:
            pass

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                self.proc.kill()

    def _env(self):
        env = dict(os.environ)
        env["KAKU_BAT"] = str(res("engine", "Kaka-Setup.bat"))   # exact copy of Kaka-Setup-V1.1.bat
        env["KAKU_ARGS"] = ""
        env["KAKA_GUI"] = "1"
        env["KAKA_GUI_PS"] = str(res("engine", "kaka_setup_engine.ps1"))
        env["KAKA_GUI_EXE"] = sys.executable if getattr(sys, "frozen", False) else ""
        env["KAKA_GUI_ANSWERS"] = json.dumps(self.answers, ensure_ascii=False)
        env["KAKA_SETUP_FOLDER"] = self.folder_raw
        env["KAKA_SETUP_MODE"] = self.mode
        for k in ("KAKA_SETUP_UNATTENDED", "KAKA_SETUP_OPTIONS", "KAKA_SETUP_NAME", "KAKA_SETUP_CITY"):
            env.pop(k, None)
        if self.options_csv:
            env["KAKA_SETUP_OPTIONS"] = self.options_csv
        if self.name:
            env["KAKA_SETUP_NAME"] = self.name
        if self.city:
            env["KAKA_SETUP_CITY"] = self.city
        py, ver = bundled_python()
        if py:
            env["KAKA_GUI_PY_EXE"] = str(py)
            env["KAKA_GUI_PY_VER"] = ver
        return env

    def _run(self):
        script = "$s=[IO.File]::ReadAllText($env:KAKA_GUI_PS); & ([ScriptBlock]::Create($s)); exit $LASTEXITCODE"
        if IS_WIN:
            cmd = [self.powershell or "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        else:
            cmd = [self.powershell or "pwsh", "-NoProfile", "-Command", script]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=self._env(), creationflags=(CREATE_NO_WINDOW if IS_WIN else 0))
        except Exception as e:
            self.on_line("    [ X  ] Windows PowerShell could not be started: %s" % e, "line")
            self.on_line("    Fix:   use Windows 10 or 11 (PowerShell is built in), then run Kaka-Setup.exe again.", "line")
            self.on_done(9)
            return
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        buf = ""
        out = self.proc.stdout
        while True:
            chunk = out.read1(65536)
            if not chunk:
                break
            buf += dec.decode(chunk)
            while True:
                m = re.search(r"\r\n|\n|\r", buf)
                if not m:
                    break
                if m.group(0) == "\r" and m.end() == len(buf):
                    break                      # a lone \r at the end may be the first half of \r\n
                seg, sep, buf = buf[:m.start()], m.group(0), buf[m.end():]
                self._segment(seg, "cr" if sep == "\r" else "line")
        buf += dec.decode(b"", final=True)
        for seg in re.split(r"\r\n|\n|\r", buf):
            if seg:
                self._segment(seg, "line")
        rc = self.proc.wait()
        self.on_done(-1 if self.cancelled else rc)

    def _segment(self, seg: str, kind: str):
        seg = ANSI_RE.sub("", seg)
        if seg.startswith("@@KAKA-ASK@@"):
            try:
                d = json.loads(seg[len("@@KAKA-ASK@@"):])
            except Exception:
                d = {"q": seg, "d": ""}
            self.on_ask(d.get("q", ""), d.get("d", ""))
        elif seg.startswith("@@KAKA-LAUNCH@@"):
            try:
                self.on_launch(json.loads(seg[len("@@KAKA-LAUNCH@@"):]))
            except Exception:
                pass
        else:
            self.on_line(seg, kind)


def launch_kaka(info: dict) -> bool:
    """Open Kaka the way the bat does: Kaka\\Kaka.exe if present, else pythonw + launcher."""
    try:
        target, wd = info.get("target") or "", info.get("workdir") or None
        flags = (0x00000008 | 0x00000200) if IS_WIN else 0
        if target and target == info.get("exe") and os.path.isfile(target):
            subprocess.Popen([target], cwd=wd, creationflags=flags, close_fds=True)
        else:
            subprocess.Popen([info.get("pyw"), info.get("launcher")], cwd=wd, creationflags=flags, close_fds=True)
        return True
    except Exception:
        return False


def answers_for(mode, name=None, city=None, delete_data=False):
    if mode == "uninstall":
        return {"Remove Kaka from": "Y", "Your Data folder": "DELETE" if delete_data else ""}
    a = {"Install Kaka here?": "Y", "Type a number to switch it on/off": ""}
    if name:
        a["Aapka naam kya hai?"] = name
    if city:
        a["Aapka city kaunsa hai?"] = city
    return a


# ---------------------------------------------------------------- silent mode (tests, power users)
def run_silent(a) -> int:
    out_fh = open(a.log_out, "w", encoding="utf-8") if a.log_out else None

    def emit(t):
        if out_fh:
            out_fh.write(t + "\n")
            out_fh.flush()
        try:
            print(t, flush=True)
        except Exception:
            pass

    known = known_installs()
    suggest = known[0] if known else DEFAULT_FOLDER
    raw = a.dir or suggest
    eff, _old = normalize_folder(raw, suggest)
    mode = (a.mode or "").lower() or ("repair" if has_kaka(eff) else "install")
    state = {"rc": None, "launch": None, "app": None}
    done = threading.Event()
    eng = None

    def on_line(t, kind):
        if kind == "line":
            emit(t)
            m = APP_RE.match(t)
            if m:
                state["app"] = m.group(1)

    def on_ask(q, d):
        emit("    (silent) %s [%s] -> default" % (q, d))
        eng.answer("")

    def on_launch(info):
        state["launch"] = info

    def on_done(rc):
        state["rc"] = rc
        done.set()

    eng = Engine(raw, mode, answers_for(mode, a.name, a.city, a.delete_data), a.options, a.name, a.city,
                 on_line, on_ask, on_launch, on_done, powershell=a.powershell)
    eng.start()
    done.wait()
    rc = state["rc"]
    if rc == 0 and mode != "uninstall" and a.key:
        ok, msg = save_gemini_key(state["app"] or pjoin(eff, "app"), a.key.strip())
        line = ("    [ OK ] Your Gemini key was saved in " + msg) if ok else ("    [ !  ] Gemini key not saved: " + msg)
        emit(line)
        append_to_log(line)
        if not ok:
            rc = 10
    if rc == 0 and state["launch"] and not a.no_launch:
        launch_kaka(state["launch"])
    emit("KAKA-SETUP-EXIT %s" % rc)
    if out_fh:
        out_fh.close()
    return int(rc if rc is not None else 1)


# ---------------------------------------------------------------- the window
CYAN = "#22d3ee"
QSS = """
QWidget { background: #070b10; color: #dbe7f0; }
QLabel { background: transparent; }
#header { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0c1720, stop:1 #091017); border-bottom: 1px solid #124050; }
#sidebar { background: #0a0f15; border-right: 1px solid #13202a; }
#footer { background: #0a0f15; border-top: 1px solid #13202a; }
#title { font-size: 21pt; font-weight: 700; color: #ffffff; }
#by { font-size: 11pt; color: #8b98a5; padding-bottom: 3px; }
#tagline { font-size: 9pt; color: #7d8a96; }
#pageTitle { font-size: 16pt; font-weight: 600; color: #ffffff; }
#pageSub { color: #9fb0bf; }
#muted { color: #7d8a96; }
#small { color: #627180; font-size: 8.5pt; }
#info { background: #0b1a22; border: 1px solid #154555; border-radius: 8px; padding: 9px 12px; color: #bfe9f5; }
#warn { background: #211809; border: 1px solid #5a4214; border-radius: 8px; padding: 9px 12px; color: #ffd38a; }
#bad { background: #220d0f; border: 1px solid #6b2027; border-radius: 8px; padding: 9px 12px; color: #fecaca; }
#card { background: #0c131a; border: 1px solid #172530; border-radius: 10px; }
#choice { background: #0c131a; border: 1px solid #172530; border-radius: 10px; }
QLineEdit { background: #0d151d; border: 1px solid #22323f; border-radius: 7px; padding: 8px 10px; font-size: 11pt; color: #ffffff; selection-background-color: #0e7490; }
QLineEdit:focus { border: 1px solid #22d3ee; }
QPushButton { background: #121c25; border: 1px solid #263644; border-radius: 7px; padding: 7px 18px; color: #dbe7f0; }
QPushButton:hover { background: #172431; border-color: #34617a; }
QPushButton:disabled { color: #4d5a66; border-color: #1a2530; background: #0d141b; }
QPushButton#primary { background: #0891b2; border: 1px solid #22d3ee; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #06a3c7; }
QPushButton#primary:disabled { background: #0d2a33; border-color: #164451; color: #5f8590; }
QPushButton#danger { background: #8b1e1e; border: 1px solid #ef4444; color: #ffffff; font-weight: 600; }
QPushButton#gh { background: #161b22; border: 1px solid #30363d; border-radius: 9px; padding: 8px 15px; font-weight: 600; color: #e6edf3; }
QPushButton#gh:hover { background: #1f2630; border-color: #8b949e; }
QRadioButton, QCheckBox { spacing: 10px; font-size: 10.5pt; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #3a5568; border-radius: 4px; background: #0d151d; }
QCheckBox::indicator:checked { background: #22d3ee; border-color: #22d3ee; image: url(CHECK_SVG); }
QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid #3a5568; border-radius: 9px; background: #0d151d; }
QRadioButton::indicator:checked { background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #22d3ee, stop:0.5 #22d3ee, stop:0.6 #0d151d, stop:1 #0d151d); border-color: #22d3ee; }
QProgressBar { background: #0d151d; border: 1px solid #1d2c38; border-radius: 7px; min-height: 16px; max-height: 16px; text-align: center; color: #e6f7fb; font-size: 8.5pt; }
QProgressBar::chunk { border-radius: 6px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0891b2, stop:1 #22d3ee); }
QPlainTextEdit { background: #04070a; border: 1px solid #172530; border-radius: 8px; color: #b9c6d1; font-family: Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', monospace; font-size: 9pt; padding: 6px; }
QScrollBar:vertical { background: #070b10; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #1f3140; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: #070b10; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #1f3140; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { background: #0c131a; color: #dbe7f0; border: 1px solid #22d3ee; }
"""


def run_gui(a) -> int:
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AbhinabaChanda.KakaSetup")
        except Exception:
            pass
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, Signal, QObject, QTimer
    from PySide6.QtSvg import QSvgRenderer

    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QtWidgets.QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_TITLE)
    qapp.setOrganizationName(AUTHOR)
    qapp.setWindowIcon(QtGui.QIcon(str(res("assets", "kaka.ico"))))
    qapp.setFont(QtGui.QFont("Segoe UI", 10))
    qapp.setStyleSheet(QSS.replace("CHECK_SVG", res("assets", "check.svg").as_posix()))
    MB = QtWidgets.QMessageBox

    def svg_icon(path, size):
        r = QSvgRenderer(str(path))
        pm = QtGui.QPixmap(size * 3, size * 3)
        pm.fill(Qt.transparent)
        p = QtGui.QPainter(pm)
        r.render(p)
        p.end()
        pm.setDevicePixelRatio(3)
        return QtGui.QIcon(pm)

    def logo(size):
        pm = QtGui.QPixmap(str(res("assets", "kaka-logo.png")))
        pm = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(2)
        l = QtWidgets.QLabel()
        l.setPixmap(pm)
        l.setFixedSize(size + 4, size + 4)
        return l

    def label(text, name=None, wrap=True, rich=False):
        l = QtWidgets.QLabel(text)
        if name:
            l.setObjectName(name)
        l.setWordWrap(wrap)
        if rich:
            l.setTextFormat(Qt.RichText)
            l.setOpenExternalLinks(True)
            l.setTextInteractionFlags(Qt.TextBrowserInteraction)
        return l

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    class Bridge(QObject):
        line = Signal(str, str)
        ask = Signal(str, str)
        launch = Signal(dict)
        done = Signal(int)
        probed = Signal(dict)

    class Page(QtWidgets.QWidget):
        def __init__(self, key, title="", sub=""):
            super().__init__()
            self.key = key
            self.v = QtWidgets.QVBoxLayout(self)
            self.v.setContentsMargins(34, 24, 34, 16)
            self.v.setSpacing(11)
            self.title_label = label(title, "pageTitle")
            self.sub_label = label(sub, "pageSub")
            if title:
                self.v.addWidget(self.title_label)
            if sub:
                self.v.addWidget(self.sub_label)

    def choice_box(rb, desc):
        box = QtWidgets.QFrame()
        box.setObjectName("choice")
        bl = QtWidgets.QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(2)
        rb.setStyleSheet("font-size:11pt; font-weight:600;")
        bl.addWidget(rb)
        d = label(desc, "muted")
        d.setContentsMargins(28, 0, 0, 0)
        bl.addWidget(d)
        return box

    class Wizard(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(APP_TITLE)
            self.resize(920, 650)
            self.setMinimumSize(860, 600)
            self.br = Bridge()
            self.br.line.connect(self.on_line)
            self.br.ask.connect(self.on_ask)
            self.br.launch.connect(self.on_launch)
            self.br.done.connect(self.on_done)
            self.br.probed.connect(self.on_probed)
            self.engine = None
            self.running = False
            self.rc = None
            self.launch_info = None
            self.app_dir_seen = None
            self.step = (0, 8, "")
            self.sub_frac = 0.0
            self.last_cr = False
            self.bad_lines, self.warn_lines, self.summary = [], [], []
            self.known = known_installs()
            self.suggest = self.known[0] if self.known else DEFAULT_FOLDER
            self.mode = None
            self.shots = None
            self.history = []
            self.eff_folder = self.raw_folder = ""
            self.cur_name = self.cur_city = self.cur_key = ""
            self.name_touched = self.city_touched = False
            if a.uninstall:
                self.mode = "uninstall"

            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(self.build_header())
            mid = QtWidgets.QHBoxLayout()
            mid.setSpacing(0)
            mid.addWidget(self.build_sidebar())
            self.stack = QtWidgets.QStackedWidget()
            mid.addWidget(self.stack, 1)
            outer.addLayout(mid, 1)
            outer.addWidget(self.build_footer())
            self.pages = {}
            for p in (self.page_welcome(), self.page_mode(), self.page_folder(), self.page_name(), self.page_city(),
                      self.page_key(), self.page_options(), self.page_uninstall(), self.page_run(), self.page_finish()):
                self.pages[p.key] = p
                self.stack.addWidget(p)
            if a.folder_preset:
                self.ed_folder.setText(a.folder_preset)
            elif a.dir:
                self.ed_folder.setText(a.dir)
            self.go("welcome", record=False)
            threading.Thread(target=lambda: self.br.probed.emit(probe_pc()), daemon=True).start()

        # ---------------- frame
        def build_header(self):
            h = QtWidgets.QFrame()
            h.setObjectName("header")
            h.setFixedHeight(88)
            lay = QtWidgets.QHBoxLayout(h)
            lay.setContentsMargins(22, 12, 22, 12)
            lay.setSpacing(14)
            lay.addWidget(logo(50))
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(1)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(label("Kaka Setup", "title", wrap=False), 0, Qt.AlignBottom)
            row.addWidget(label("by Abhinaba Chanda", "by", wrap=False), 0, Qt.AlignBottom)
            row.addStretch(1)
            col.addLayout(row)
            col.addWidget(label("Your personal AI assistant for Windows. Nothing is sent, deleted or installed without asking.", "tagline"))
            lay.addLayout(col, 1)
            gh = QtWidgets.QPushButton("  GitHub")
            gh.setObjectName("gh")
            gh.setIcon(svg_icon(res("assets", "github-mark.svg"), 18))
            gh.setIconSize(QtCore.QSize(18, 18))
            gh.setCursor(Qt.PointingHandCursor)
            gh.setToolTip(PROFILE_URL)
            gh.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(PROFILE_URL)))
            lay.addWidget(gh, 0, Qt.AlignVCenter)
            return h

        def build_sidebar(self):
            s = QtWidgets.QFrame()
            s.setObjectName("sidebar")
            s.setFixedWidth(196)
            self.side = QtWidgets.QVBoxLayout(s)
            self.side.setContentsMargins(18, 22, 10, 16)
            self.side.setSpacing(2)
            return s

        def build_footer(self):
            f = QtWidgets.QFrame()
            f.setObjectName("footer")
            f.setFixedHeight(60)
            lay = QtWidgets.QHBoxLayout(f)
            lay.setContentsMargins(22, 10, 22, 10)
            lay.addWidget(label("Copyright \u00a9 2026 Abhinaba Chanda", "small", wrap=False))
            lay.addStretch(1)
            self.btn_back = QtWidgets.QPushButton("Back")
            self.btn_next = QtWidgets.QPushButton("Next")
            self.btn_cancel = QtWidgets.QPushButton("Cancel")
            for b in (self.btn_back, self.btn_next, self.btn_cancel):
                b.setMinimumWidth(110)
                b.setCursor(Qt.PointingHandCursor)
                lay.addWidget(b)
            self.btn_back.clicked.connect(self.back)
            self.btn_next.clicked.connect(self.next)
            self.btn_cancel.clicked.connect(self.cancel)
            return f

        def flow(self):
            if self.mode == "uninstall":
                return ["welcome", "mode", "folder", "uninstall", "run", "finish"]
            f = ["welcome"]
            if self.known or self.mode == "repair":
                f.append("mode")
            return f + ["folder", "name", "city", "key", "options", "run", "finish"]

        def refresh_sidebar(self):
            while self.side.count():
                it = self.side.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            names = {"welcome": "Welcome", "mode": "Repair or uninstall", "folder": "Kaka folder" if self.mode in ("repair", "uninstall") else "Install folder",
                     "name": "Your name", "city": "Your city", "key": "API key", "options": "Options",
                     "uninstall": "Uninstall", "run": {"uninstall": "Removing", "repair": "Repair / update"}.get(self.mode, "Install"),
                     "finish": "Finish"}
            keys = self.flow()
            cur = self.stack.currentWidget().key
            ci = keys.index(cur) if cur in keys else 0
            for i, k in enumerate(keys):
                if i < ci:
                    txt, css = "\u2713   " + names[k], "color:#4fd1a5;"
                elif i == ci:
                    txt, css = "\u25B8   " + names[k], "color:%s; font-weight:600; background:#0d1d26; border-radius:6px;" % CYAN
                else:
                    txt, css = "\u25CB   " + names[k], "color:#5d6b77;"
                l = QtWidgets.QLabel(txt)
                l.setStyleSheet(css + "font-size:10pt; padding:7px 8px;")
                self.side.addWidget(l)
            self.side.addStretch(1)
            self.side.addWidget(label("Kaka v1.1", "small"))

        # ---------------- pages
        def page_welcome(self):
            p = Page("welcome")
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(24)
            row.addWidget(logo(136), 0, Qt.AlignTop)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(8)
            t = label("Kaka by Abhinaba Chanda", "pageTitle")
            t.setStyleSheet("font-size:20pt;")
            col.addWidget(t)
            col.addWidget(label("Your personal desktop AI assistant for Windows", "pageSub"))
            col.addSpacing(4)
            col.addWidget(label("Setup asks where to install, checks your PC, installs Python 3.13 if needed, downloads Kaka, "
                                "its packages and tools, and makes shortcuts. Run it again later to repair, update or uninstall."))
            col.addWidget(label("Kaka aapka naam aur city poochega, phir baaki sab khud karega \u2014 har step yahin live dikhega."))
            col.addStretch(1)
            row.addLayout(col, 1)
            p.v.addLayout(row)
            self.welcome_info = label("", "info")
            p.v.addWidget(self.welcome_info)
            p.v.addStretch(1)
            p.v.addWidget(label("Needs Windows 10/11 64-bit, internet and about %d GB free space. "
                                "Nothing is changed on your PC until the install step." % NEED_GB, "muted"))
            return p

        def page_mode(self):
            p = Page("mode", "Kaka is already installed")
            self.mode_info = label("", "info")
            p.v.addWidget(self.mode_info)
            self.rb_repair = QtWidgets.QRadioButton("1   Repair / update")
            self.rb_uninst = QtWidgets.QRadioButton("2   Uninstall")
            self.rb_exit = QtWidgets.QRadioButton("3   Exit without changes")
            grp = QtWidgets.QButtonGroup(p)
            for rb, d in ((self.rb_repair, "New Kaka files + packages; keeps your Data and API keys (and your name and city)."),
                          (self.rb_uninst, "Removes Kaka. Your Data folder is kept unless you choose to delete it. Python stays installed."),
                          (self.rb_exit, "Nothing is changed.")):
                grp.addButton(rb)
                p.v.addWidget(choice_box(rb, d))
                rb.toggled.connect(lambda _on: self.update_buttons())
            self.rb_repair.setChecked(True)
            p.v.addStretch(1)
            return p

        def page_folder(self):
            p = Page("folder", "Choose where to install Kaka",
                     "Pick a folder on any drive (or make a new one). Kaka keeps its app, Data and Tools in it.")
            row = QtWidgets.QHBoxLayout()
            self.ed_folder = QtWidgets.QLineEdit()
            self.ed_folder.textChanged.connect(self.update_folder_preview)
            br = QtWidgets.QPushButton("Browse\u2026")
            br.clicked.connect(self.browse)
            row.addWidget(self.ed_folder, 1)
            row.addWidget(br)
            p.v.addLayout(row)
            self.folder_default = label("", "muted")
            p.v.addWidget(self.folder_default)
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            cl = QtWidgets.QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(3)
            cl.addWidget(label("Kaka will be installed in:"))
            self.folder_eff = label("")
            self.folder_eff.setStyleSheet("color:#4ade80; font-weight:600; font-size:11pt;")
            cl.addWidget(self.folder_eff)
            for t in ("app\\       Kaka itself (+ Kaka-src launcher, .venv Python)",
                      "Data\\      your notes, reminders, recordings, face data",
                      "Tools\\     ffmpeg (screen recording)"):
                l = QtWidgets.QLabel("  " + t)
                l.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace; color:#7d8a96; font-size:9.5pt;")
                cl.addWidget(l)
            p.v.addWidget(card)
            self.folder_note = label("", "warn")
            p.v.addWidget(self.folder_note)
            self.folder_free = label("", "muted")
            p.v.addWidget(self.folder_free)
            p.v.addStretch(1)
            return p

        def page_name(self):
            p = Page("name", "Your name")
            p.v.addWidget(label("Aapka naam kya hai? (Kaka aapko isi naam se bulayega)"))
            self.ed_name = QtWidgets.QLineEdit()
            self.ed_name.setMaxLength(60)
            self.ed_name.returnPressed.connect(self.next)
            p.v.addWidget(self.ed_name)
            self.name_info = label("", "info")
            p.v.addWidget(self.name_info)
            self.name_warn = label("Naam khaali nahi chalega, phir se likho.", "warn")
            self.name_warn.hide()
            p.v.addWidget(self.name_warn)
            p.v.addWidget(label("Change it later in Kaka: gear > Customise > YOUR NAME.", "muted"))
            p.v.addStretch(1)
            return p

        def page_city(self):
            p = Page("city", "Your city")
            p.v.addWidget(label("Aapka city kaunsa hai? (weather isi city ka batayega)"))
            self.ed_city = QtWidgets.QLineEdit()
            self.ed_city.setMaxLength(60)
            self.ed_city.returnPressed.connect(self.next)
            p.v.addWidget(self.ed_city)
            self.city_info = label("", "info")
            p.v.addWidget(self.city_info)
            p.v.addWidget(label("Weather and the morning brief use this city. Change it later: gear > Customise > YOUR CITY, "
                                "or say \u201cKaka, mera city Delhi kar do\u201d.", "muted"))
            p.v.addStretch(1)
            return p

        def page_key(self):
            p = Page("key", "Gemini API key (optional)")
            p.v.addWidget(label('Kaka talks with a free Gemini key. Get yours here: <a style="color:%s" href="%s">%s</a><br>'
                                "(sign in with Google \u2192 Create API key \u2192 copy it)" % (CYAN, GEMINI_KEY_URL, GEMINI_KEY_URL),
                                rich=True))
            row = QtWidgets.QHBoxLayout()
            self.ed_key = QtWidgets.QLineEdit()
            self.ed_key.setEchoMode(QtWidgets.QLineEdit.Password)
            self.ed_key.setPlaceholderText("Paste your Gemini API key here, or leave it empty")
            self.btn_eye = QtWidgets.QPushButton("Show")
            self.btn_eye.setCheckable(True)
            self.btn_eye.toggled.connect(self.toggle_key)
            row.addWidget(self.ed_key, 1)
            row.addWidget(self.btn_eye)
            p.v.addLayout(row)
            self.key_info = label("", "info")
            p.v.addWidget(self.key_info)
            p.v.addWidget(label("Empty is fine: Kaka asks for it the first time it opens. More keys any time: gear icon > API KEYS. "
                                "The key stays on this PC (app\\config\\api_keys.json).", "muted"))
            p.v.addStretch(1)
            return p

        def toggle_key(self, on):
            self.ed_key.setEchoMode(QtWidgets.QLineEdit.Normal if on else QtWidgets.QLineEdit.Password)
            self.btn_eye.setText("Hide" if on else "Show")

        def page_options(self):
            p = Page("options", "Choose options", "Untick anything you don't want. You can add it later with Setup > Repair.")
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            grid = QtWidgets.QVBoxLayout(card)
            grid.setContentsMargins(18, 12, 18, 12)
            grid.setSpacing(9)
            self.opt_boxes = {}
            for i, (k, d, text) in enumerate(OPTIONS):
                cb = QtWidgets.QCheckBox("%d   %s" % (i + 1, text.format(ff="CPU recording")))
                cb.setChecked(d)
                self.opt_boxes[k] = cb
                grid.addWidget(cb)
            p.v.addWidget(card)
            self.opt_note = label("", "muted")
            p.v.addWidget(self.opt_note)
            p.v.addStretch(1)
            return p

        def page_uninstall(self):
            p = Page("uninstall", "Uninstall Kaka")
            self.un_info = label("", "warn")
            p.v.addWidget(self.un_info)
            self.cb_deldata = QtWidgets.QCheckBox("Also delete my Data folder (notes, recordings, face data)")
            p.v.addWidget(self.cb_deldata)
            p.v.addWidget(label("Your Data folder is KEPT unless you tick this. Your API key file is moved to Data\\Backup "
                                "(a new install offers to bring it back). Python 3.13 is left installed (other programs may use it).",
                                "muted"))
            p.v.addStretch(1)
            return p

        def page_run(self):
            p = Page("run", "Installing Kaka")
            self.run_step = label("Starting\u2026")
            self.run_step.setStyleSheet("color:%s; font-weight:600; font-size:11pt;" % CYAN)
            p.v.addWidget(self.run_step)
            self.prog = QtWidgets.QProgressBar()
            self.prog.setRange(0, 1000)
            self.prog.setValue(0)
            p.v.addWidget(self.prog)
            self.run_detail = label(" ", "muted", wrap=False)
            self.run_detail.setMinimumWidth(10)
            self.run_detail.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            p.v.addWidget(self.run_detail)
            self.logview = QtWidgets.QPlainTextEdit()
            self.logview.setReadOnly(True)
            self.logview.setMaximumBlockCount(30000)
            self.logview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            p.v.addWidget(self.logview, 1)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(label("Log file: %s" % log_file(), "small", wrap=False))
            row.addStretch(1)
            ol = QtWidgets.QPushButton("Open log")
            ol.clicked.connect(self.open_log)
            row.addWidget(ol)
            p.v.addLayout(row)
            return p

        def page_finish(self):
            p = Page("finish")
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(20)
            row.addWidget(logo(96), 0, Qt.AlignTop)
            col = QtWidgets.QVBoxLayout()
            self.fin_title = label("", "pageTitle")
            self.fin_title.setStyleSheet("font-size:19pt;")
            col.addWidget(self.fin_title)
            self.fin_text = label("", rich=True)
            col.addWidget(self.fin_text)
            col.addStretch(1)
            row.addLayout(col, 1)
            p.v.addLayout(row)
            self.fin_card = label("", "card", rich=True)
            self.fin_card.setStyleSheet("padding:12px 16px;")
            p.v.addWidget(self.fin_card)
            p.v.addStretch(1)
            self.cb_launch = QtWidgets.QCheckBox("Launch Kaka")
            self.cb_launch.setChecked(True)
            self.cb_launch.setStyleSheet("font-size:11pt; font-weight:600;")
            row2 = QtWidgets.QHBoxLayout()
            row2.addWidget(self.cb_launch)
            row2.addStretch(1)
            ol = QtWidgets.QPushButton("Open log")
            ol.clicked.connect(self.open_log)
            row2.addWidget(ol)
            p.v.addLayout(row2)
            return p

        # ---------------- navigation
        def go(self, key, record=True):
            cur = self.stack.currentWidget()
            if record and cur is not None and cur.key != key:
                self.history.append(cur.key)
            getattr(self, "enter_" + key, lambda: None)()
            self.stack.setCurrentWidget(self.pages[key])
            self.refresh_sidebar()
            self.update_buttons()
            if self.shots is not None:
                self.shots.entered(key)

        def update_buttons(self):
            if self.stack.currentWidget() is None:
                return
            k = self.stack.currentWidget().key
            self.btn_back.setEnabled(bool(self.history) and k not in ("run", "finish"))
            self.btn_next.setEnabled(k != "run")
            self.btn_cancel.setVisible(k != "finish")
            self.btn_cancel.setEnabled(not (k == "run" and not self.running))
            txt, obj = "Next", "primary"
            if k == "options":
                txt = "Repair / update" if self.mode == "repair" else "Install"
            elif k == "uninstall":
                txt, obj = "Uninstall", "danger"
            elif k == "finish":
                txt = "Finish"
            elif k == "mode" and self.rb_exit.isChecked():
                txt = "Exit"
            self.btn_next.setText(txt)
            self.btn_next.setObjectName(obj)
            self.btn_next.style().unpolish(self.btn_next)
            self.btn_next.style().polish(self.btn_next)

        def back(self):
            if self.history and not self.running:
                k = self.history.pop()
                if k == "welcome" and not a.uninstall:
                    self.mode = None
                getattr(self, "enter_" + k, lambda: None)()
                self.stack.setCurrentWidget(self.pages[k])
                self.refresh_sidebar()
                self.update_buttons()

        def next(self):
            k = self.stack.currentWidget().key
            fn = getattr(self, "leave_" + k, None)
            if fn is not None and fn() is False:
                return
            if k == "finish":
                self.close()
                return
            fl = self.flow()
            i = fl.index(k) if k in fl else 0
            if i + 1 < len(fl):
                self.go(fl[i + 1])

        def cancel(self):
            if self.running:
                r = MB.question(self, APP_TITLE, "Setup abhi chal raha hai. Beech mein band karein?\n\n"
                                "Jo ho chuka wo rahega; baad mein Setup > Repair chala lena.", MB.Yes | MB.No, MB.No)
                if r == MB.Yes and self.engine:
                    self.engine.cancel()
                return
            if self.shots is not None or self.rc is not None:
                self.close()
                return
            if MB.question(self, APP_TITLE, "Setup band karein? Abhi tak kuch bhi change nahi hua hai.",
                           MB.Yes | MB.No, MB.No) == MB.Yes:
                self.close()

        def closeEvent(self, ev):
            if self.running:
                ev.ignore()
                self.cancel()
                return
            ev.accept()

        # ---------------- enter / leave
        def enter_welcome(self):
            if self.known:
                self.welcome_info.setText("Kaka is already installed here: %s\nNext: Repair / update or Uninstall." % ", ".join(self.known))
            else:
                self.welcome_info.setText("Ready for a fresh install. Default folder: %s" % DEFAULT_FOLDER)

        def enter_mode(self):
            eff, _ = normalize_folder(self.ed_folder.text() or self.suggest, self.suggest)
            self.mode_info.setText("Kaka is already installed in this folder:\n%s" % eff)
            if self.mode == "uninstall":
                self.rb_uninst.setChecked(True)

        def leave_mode(self):
            if self.rb_exit.isChecked():
                self.rc = 0
                self.close()
                return False
            self.mode = "uninstall" if self.rb_uninst.isChecked() else "repair"
            return True

        def enter_folder(self):
            if not self.ed_folder.text().strip():
                self.ed_folder.setText(self.suggest)
            t = {"uninstall": "Which Kaka should be removed?", "repair": "Kaka folder to repair / update"}.get(self.mode, "Choose where to install Kaka")
            self.pages["folder"].title_label.setText(t)
            self.folder_default.setText("Default: %s   (Kaka uses about %d GB there)" % (self.suggest, NEED_GB))
            self.update_folder_preview()

        def update_folder_preview(self):
            raw = self.ed_folder.text()
            eff, old = normalize_folder(raw, self.suggest)
            if not eff:
                self.folder_eff.setText("That folder name is not valid.")
                return
            self.folder_eff.setText(eff)
            notes = []
            typed = os.path.abspath(raw.strip().strip('"') or self.suggest).rstrip("\\/")
            if has_kaka(eff):
                notes.append("Kaka is already installed in this folder.")
            elif old:
                notes.append("An older Kaka is in %s. The new one uses the same Data folder." % old)
            elif os.path.normcase(eff) != os.path.normcase(typed) and len(typed) > 2 and os.path.basename(eff) == "Kaka":
                notes.append("That folder already has other files, so Kaka will go in %s" % eff)
            if re.search(r"[^\x20-\x7E]", eff):
                notes.append("This folder name has non-English letters. Some Python packages break on such paths; a plain name like C:\\Kaka is safer.")
            pf = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("windir", "")]
            if IS_WIN and any(x and eff.lower().startswith(x.lower()) for x in pf):
                notes.append("Program Files / Windows folders need admin rights for every update. C:\\Kaka or D:\\Kaka is better.")
            self.folder_note.setText("\n".join(notes))
            self.folder_note.setVisible(bool(notes))
            only_info = bool(notes) and all(n.startswith(("Kaka is already", "An older Kaka")) for n in notes)
            self.folder_note.setObjectName("info" if only_info else "warn")
            self.folder_note.style().unpolish(self.folder_note)
            self.folder_note.style().polish(self.folder_note)
            g = free_gb(eff)
            drive = os.path.splitdrive(eff)[0] or "/"
            self.folder_free.setText("%s GB free on %s (needs about %d GB)" % (g, drive, NEED_GB) if g >= 0 else "")
            if 0 <= g < (1 if has_kaka(eff) else NEED_GB):
                self.folder_free.setStyleSheet("color:#f87171;")
            else:
                self.folder_free.setStyleSheet("")

        def browse(self):
            start = self.ed_folder.text().strip() or self.suggest
            while start and not os.path.exists(start):
                up = os.path.dirname(start)
                if up == start:
                    break
                start = up
            d = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Kaka by Abhinaba Chanda \u2014 choose the install folder (Kaka keeps its app, Data and Tools in it)", start)
            if d:
                d = os.path.normpath(d)
                parent = os.path.dirname(self.suggest.rstrip("\\/"))
                if d.rstrip("\\/").lower() == parent.rstrip("\\/").lower() and not has_kaka(d):
                    d = self.suggest          # picked the parent of the default (e.g. C:\) -> C:\Kaka
                self.ed_folder.setText(d)

        def leave_folder(self):
            raw = self.ed_folder.text().strip() or self.suggest
            eff, _old = normalize_folder(raw, self.suggest)
            if not eff:
                MB.warning(self, APP_TITLE, "That folder name is not valid. Pick a normal folder like C:\\Kaka or D:\\Kaka.")
                return False
            existing = has_kaka(eff)
            if self.mode == "uninstall" and not existing:
                MB.warning(self, APP_TITLE, "Kaka is not installed in %s, so there is nothing to uninstall.\n\n"
                           "Pick the folder where Kaka is installed." % eff)
                return False
            if existing and self.mode not in ("repair", "uninstall"):
                box = MB(self)
                box.setWindowTitle(APP_TITLE)
                box.setText("Kaka is already installed in this folder:\n%s\n\nWhat should Setup do?" % eff)
                b1 = box.addButton("1  Repair / update", MB.AcceptRole)
                b2 = box.addButton("2  Uninstall", MB.DestructiveRole)
                b3 = box.addButton("3  Exit without changes", MB.RejectRole)
                box.setDefaultButton(b1)
                box.exec()
                c = box.clickedButton()
                if c is b3 or c is None:
                    self.rc = 0
                    self.close()
                    return False
                self.mode = "uninstall" if c is b2 else "repair"
                (self.rb_uninst if c is b2 else self.rb_repair).setChecked(True)
                if self.mode == "uninstall":
                    self.eff_folder, self.raw_folder = eff, raw
                    self.go("uninstall")
                    return False
            elif not existing:
                self.mode = "install"
            if not writable(eff):
                r = MB.question(self, APP_TITLE, "Your Windows user cannot write to %s.\n\nRestart Setup as administrator for this folder?\n"
                                "(No = go back and pick a folder you own, for example C:\\Kaka or D:\\Kaka.)" % eff,
                                MB.Yes | MB.No, MB.Yes)
                if r == MB.Yes and IS_WIN and getattr(sys, "frozen", False):
                    import ctypes
                    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                                             "/folder=" + raw.encode("utf-8").hex(), None, 1)
                    if rc > 32:
                        self.rc = 20
                        self.close()
                        return False
                    MB.warning(self, APP_TITLE, "Administrator permission was not given.\nPick a folder you own, for example C:\\Kaka or D:\\Kaka.")
                return False
            self.eff_folder, self.raw_folder = eff, raw
            if self.mode == "uninstall":
                return True
            st = read_settings(eff) if existing else {}
            # defaults like the bat: saved value > user variable > Windows user name > dost / Kolkata
            self.cur_name = clean_name(st.get("user_name")) or clean_name(user_env("KAKA_USER_NAME"))
            self.cur_city = clean_name(st.get("user_city")) or clean_name(user_env("KAKA_USER_CITY"))
            self.cur_key = str(st.get("gemini_api_key") or "").strip()
            if not self.name_touched:
                self.ed_name.setText(clean_name(a.name) or self.cur_name or clean_name(os.environ.get("USERNAME")) or "dost")
            if not self.city_touched:
                self.ed_city.setText(clean_name(a.city) or self.cur_city or "Kolkata")
            if existing and not a.options:
                stl = startup_lnk()
                self.opt_boxes["startup"].setChecked(os.path.exists(stl) or os.path.exists(stl + ".txt"))
            return True

        def enter_name(self):
            if self.cur_name:
                self.name_info.setText("Abhi Kaka aapko '%s' bulata hai. Next dabao to yahi naam rahega." % self.cur_name)
            else:
                self.name_info.setText("Kaka aapko isi naam se bulayega \u2014 greetings, reminders, morning brief, sab mein.")
            self.ed_name.setFocus()

        def leave_name(self):
            n = clean_name(self.ed_name.text())
            self.name_warn.setVisible(not n)
            if not n:
                return False
            self.ed_name.setText(n)
            self.name_touched = True
            return True

        def enter_city(self):
            self.city_info.setText(("Abhi city '%s' hai. Next dabao to yahi rahega." % self.cur_city) if self.cur_city
                                   else "Kaka is city ka mausam batayega.")
            self.ed_city.setFocus()

        def leave_city(self):
            self.ed_city.setText(clean_name(self.ed_city.text()) or "Kolkata")
            self.city_touched = True
            return True

        def enter_key(self):
            if self.cur_key:
                self.key_info.setText("A Gemini key is already saved (\u2022\u2022\u2022\u2022%s). Leave the box empty to keep it." % self.cur_key[-4:])
            else:
                self.key_info.setText("No Gemini key is saved yet. Paste it now, or Kaka will ask on its first start.")
            if a.key and not self.ed_key.text():
                self.ed_key.setText(a.key)

        def leave_key(self):
            k = self.ed_key.text().strip()
            if k and re.search(r"\s", k):
                MB.warning(self, APP_TITLE, "The key has spaces in it. Copy it again from %s" % GEMINI_KEY_URL)
                return False
            return True

        def on_probed(self, pc):
            nvidia = [g for g in pc.get("gpus", []) if "NVIDIA" in str(g).upper()]
            self.opt_boxes["ffmpeg"].setText("4   " + OPTIONS[3][2].format(ff="NVENC on your NVIDIA GPU" if nvidia else "CPU recording"))
            notes = []
            if pc.get("ok") and not pc.get("cams"):
                self.opt_boxes["face"].setChecked(False)
                self.opt_boxes["gestures"].setChecked(False)
                notes.append("No webcam found, so face greet and hand gestures start unticked.")
            elif pc.get("cams"):
                notes.append("Webcam: %s." % pc["cams"][0])
            if nvidia:
                notes.append("NVIDIA GPU: %s \u2014 fast NVENC screen recording." % nvidia[0])
            self.opt_note.setText("  ".join(notes))
            if a.options:
                want = [x.strip().lower() for x in a.options.split(",")]
                for k2, cb in self.opt_boxes.items():
                    cb.setChecked(k2 in want)

        def leave_options(self):
            if self.shots is not None and self.shots.no_run:
                self.shots.finish()
                return False
            return True

        def enter_uninstall(self):
            self.un_info.setText("Kaka will be removed from:\n%s\n\nThe app, its Python environment (.venv), Tools and the "
                                 "Kaka shortcuts are deleted." % self.eff_folder)
            if a.delete_data:
                self.cb_deldata.setChecked(True)

        def leave_uninstall(self):
            if self.shots is not None and self.shots.no_run:
                self.shots.finish()
                return False
            if self.shots is None:
                msg = "Remove Kaka from %s?\n\n" % self.eff_folder
                msg += ("Your Data folder (notes, recordings, face data) will be DELETED too." if self.cb_deldata.isChecked()
                        else "Your Data folder is kept.")
                if MB.question(self, APP_TITLE, msg, MB.Yes | MB.No, MB.No) != MB.Yes:
                    return False
            return True

        def open_log(self):
            f = log_file()
            if f.exists():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(f)))
            else:
                MB.information(self, APP_TITLE, "The log file is not there yet:\n%s" % f)

        # ---------------- running the installer
        def enter_run(self):
            if self.running or self.rc is not None:
                return
            mode = self.mode or "install"
            self.pages["run"].title_label.setText({"repair": "Repairing / updating Kaka", "uninstall": "Uninstalling Kaka"}.get(mode, "Installing Kaka"))
            self.logview.clear()
            self.prog.setValue(0)
            if mode == "uninstall":
                name = city = opts = None
                answers = answers_for(mode, delete_data=self.cb_deldata.isChecked())
                self.step = (0, 1, "")
            else:
                name = clean_name(self.ed_name.text())
                city = clean_name(self.ed_city.text()) or "Kolkata"
                answers = answers_for(mode, name, city)
                on = [k for k, cb in self.opt_boxes.items() if cb.isChecked()]
                opts = ",".join(on) if on else "none"
                self.step = (0, 8, "")
            self.running = True
            br = self.br
            self.engine = Engine(self.raw_folder, mode, answers, opts, name, city,
                                 lambda t, k: br.line.emit(t, k), lambda q, d: br.ask.emit(q, d),
                                 lambda i: br.launch.emit(i), lambda rc: br.done.emit(rc), powershell=a.powershell)
            self.engine.start()
            QTimer.singleShot(0, self.update_buttons)

        def append_log(self, s, replace_last):
            color = "#b9c6d1"
            if "[ OK ]" in s:
                color = "#4ade80"
            elif "[ !  ]" in s:
                color = "#fbbf24"
            elif "[ X  ]" in s:
                color = "#f87171"
            elif "[ i  ]" in s:
                color = "#67e8f9"
            elif s.strip().startswith("Fix:"):
                color = "#f0abfc"
            elif STEP_RE.match(s):
                color = CYAN
            html = '<span style="color:%s">%s</span>' % (color, esc(s).replace(" ", "&nbsp;") or "&nbsp;")
            sb = self.logview.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            if replace_last:
                cur = self.logview.textCursor()
                cur.movePosition(QtGui.QTextCursor.End)
                cur.movePosition(QtGui.QTextCursor.StartOfBlock, QtGui.QTextCursor.KeepAnchor)
                cur.removeSelectedText()
                cur.insertHtml(html)
            else:
                self.logview.appendHtml(html)
            if at_bottom:
                sb.setValue(sb.maximum())

        def on_line(self, text, kind):
            m = STEP_RE.match(text)
            if m and kind == "line":
                n, tot, title = int(m.group(1)), int(m.group(2)), m.group(3)
                self.step = (n, tot, title)
                self.sub_frac = 0.0
                self.run_step.setText("Step %d of %d \u2014 %s" % (n, tot, title))
            mo = APP_RE.match(text)
            if mo:
                self.app_dir_seen = mo.group(1)
            st = text.strip()
            if "[ X  ]" in text or st.startswith("Fix:"):
                self.bad_lines.append(st)
            if "[ !  ]" in text and kind == "line":
                self.warn_lines.append(st)
            md = re.match(r"^\s{4}(\S.*?)\s+(\d{1,3})%\s", text + " ")
            mp = re.search(r"\[\s*(\d+)\]\s+Collecting", text)
            mi = re.search(r"Installing Python\.\.\. (\d+)s", text)
            if md:
                self.sub_frac = max(self.sub_frac, min(1.0, int(md.group(2)) / 100.0) * 0.7)
            elif mp:
                self.sub_frac = 1 - 1 / (1 + int(mp.group(1)) / 18.0)
            elif mi:
                self.sub_frac = min(0.95, int(mi.group(1)) / 150.0)
            if st:
                self.run_detail.setText(st[:140])
            n, tot, _t = self.step
            if tot:
                val = int(1000 * (max(0, n - 1) + self.sub_frac) / tot)
                self.prog.setValue(max(self.prog.value(), min(990, val)))
            self.append_log(text, self.last_cr)
            self.last_cr = (kind == "cr")

        def on_ask(self, q, d):
            """A question the bat asks that the earlier pages did not answer
            (for example "Continue anyway?" after a failed PC check)."""
            if self.shots is not None:
                self.append_log("    %s [%s] (auto: default)" % (q, d), False)
                self.engine.answer("")
                return
            m = re.match(r"^(.*)\s*\(Y/N\)\s*$", q)
            if m:
                r = MB.question(self, APP_TITLE, m.group(1).strip(), MB.Yes | MB.No,
                                MB.Yes if str(d).upper().startswith("Y") else MB.No)
                self.engine.answer("Y" if r == MB.Yes else "N")
            else:
                txt, ok = QtWidgets.QInputDialog.getText(self, APP_TITLE, q, QtWidgets.QLineEdit.Normal, str(d))
                self.engine.answer(txt if ok else "")

        def on_launch(self, info):
            self.launch_info = info

        def on_done(self, rc):
            self.running = False
            self.rc = rc
            self.prog.setValue(1000 if rc == 0 else self.prog.value())
            mode = self.mode or "install"
            key_line = ""
            if rc == 0 and mode != "uninstall":
                k = self.ed_key.text().strip()
                if k:
                    ok, msg = save_gemini_key(self.app_dir_seen or pjoin(self.eff_folder, "app"), k)
                    key_line = ("    [ OK ] Your Gemini key was saved in " + msg) if ok else ("    [ !  ] Your Gemini key was not saved: " + msg)
                    self.append_log(key_line, False)
                    append_to_log(key_line)
            self.show_finish(rc, mode, key_line)
            self.go("finish")

        def show_finish(self, rc, mode, key_line):
            launchable = rc == 0 and mode != "uninstall" and bool(self.launch_info)
            self.cb_launch.setVisible(launchable)
            nothing = any("Nothing was changed" in x for x in self.logview.toPlainText().splitlines()[-6:])
            if rc == 0 and mode == "uninstall":
                self.fin_title.setText("Kaka was uninstalled")
                self.fin_text.setText("Thank you for trying Kaka by Abhinaba Chanda!")
                kept = "deleted" if self.cb_deldata.isChecked() else "kept in %s" % esc(pjoin(self.eff_folder, "Data"))
                self.fin_card.setText("Your Data folder: %s<br>Python 3.13 was left installed (other programs may use it). "
                                      "Remove it in Settings &gt; Apps if you want." % kept)
            elif rc == 0 and nothing:
                self.fin_title.setText("Nothing was changed")
                self.fin_text.setText("Run Kaka Setup again any time.")
                self.fin_card.setText("Log: %s" % esc(log_file()))
            elif rc == 0:
                self.fin_title.setText("Kaka by Abhinaba Chanda is ready")
                self.fin_text.setText("Kaka %s. The first start takes up to a minute.<br>Then just say: <b>\u201cKaka, what time is it?\u201d</b>"
                                      % ("was repaired / updated" if mode == "repair" else "is installed"))
                lines = ["<b>Folder:</b> %s" % esc(self.eff_folder),
                         "<b>Name:</b> %s &nbsp;&nbsp; <b>City:</b> %s" % (esc(self.ed_name.text()), esc(self.ed_city.text()))]
                if "[ OK ]" in key_line:
                    lines.append("<b>Gemini key:</b> saved")
                elif self.cur_key:
                    lines.append("<b>Gemini key:</b> kept")
                else:
                    lines.append('<b>API keys:</b> paste your free Gemini key when Kaka opens (<a style="color:%s" href="%s">%s</a>); more keys: gear icon &gt; API KEYS'
                                 % (CYAN, GEMINI_KEY_URL, GEMINI_KEY_URL))
                skipped = [x for x in self.warn_lines if "retry with Setup > Repair" in x]
                for s in skipped[:4]:
                    lines.append('<span style="color:#fbbf24">%s</span>' % esc(s.replace("[ !  ]", "").strip()))
                lines.append('Help and all features: <a style="color:%s" href="%s">%s</a>' % (CYAN, REPO_URL, REPO_URL))
                self.fin_card.setText("<br>".join(lines))
            elif rc == 20:
                self.fin_title.setText("Setup continues as administrator")
                self.fin_text.setText("Setup continues in the new administrator window. You can close this one.")
                self.fin_card.setText("")
            else:
                self.fin_title.setText("Kaka Setup stopped (code %s)" % rc if rc != -1 else "Setup was cancelled")
                self.fin_text.setText("The reason and the fix are below. Fix it and run Kaka-Setup.exe again (choose Repair if Kaka is already there)."
                                      if rc != -1 else "Whatever was already done stays. Run Kaka-Setup.exe again and choose Repair.")
                body = "<br>".join(esc(x) for x in self.bad_lines[-4:]) or "See the log for details."
                self.fin_card.setText(body + "<br><br>Full log: %s" % esc(log_file()))
                self.fin_card.setStyleSheet("padding:12px 16px; color:#fecaca;")

        def leave_finish(self):
            if self.cb_launch.isVisible() and self.cb_launch.isChecked() and self.launch_info and not a.no_launch:
                launch_kaka(self.launch_info)
            return True

    # ---------------- screenshots / autopilot (used by the tests on a real Windows PC)
    class Shots:
        def __init__(self, win, out, autopilot, no_run, extra):
            self.win, self.out, self.autopilot, self.no_run, self.extra = win, Path(out), autopilot, no_run, extra
            self.out.mkdir(parents=True, exist_ok=True)
            self.n = 0
            self.seen = set()
            self.run_shots = 0
            self.last_step = 0

        def grab(self, name):
            self.n += 1
            QtWidgets.QApplication.processEvents()
            pm = self.win.grab()
            pm.save(str(self.out / ("%02d-%s.png" % (self.n, name))))

        def entered(self, key):
            if key in self.seen and key != "finish":
                return
            self.seen.add(key)
            QTimer.singleShot(900, lambda: self.on_page(key))

        def on_page(self, key):
            w = self.win
            if key == "run":
                self.timer = QTimer()
                self.timer.timeout.connect(self.poll_run)
                self.timer.start(1500)
                return
            self.grab(key)
            if key == "finish":
                QTimer.singleShot(500, w.close)
                return
            if key == "mode" and self.extra == "uninstall":
                w.rb_uninst.setChecked(True)
                QtWidgets.QApplication.processEvents()
                self.grab("mode-uninstall")
            if key == "key" and self.autopilot:
                pass
            if self.autopilot:
                QTimer.singleShot(400, w.next)

        def poll_run(self):
            n = self.win.step[0]
            if self.win.running and n != self.last_step:
                self.last_step = n
                if n in (2, 5, 6, 7):
                    QTimer.singleShot(3000, self.grab_run)
                if n == 6:
                    QTimer.singleShot(30000, self.grab_run)

        def grab_run(self):
            if self.win.running:
                self.grab("install-step%d" % self.win.step[0])

        def finish(self):
            QTimer.singleShot(300, self.win.close)

    win = Wizard()
    if a.shots:
        win.shots = Shots(win, a.shots, a.autopilot, a.shots_no_run, a.shots_extra)
        win.shots.entered("welcome")
    win.show()
    if IS_WIN:
        try:
            import ctypes
            hwnd = int(win.winId())
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass
    qapp.exec()
    if a.shots and win.rc is None:
        return 0
    return int(win.rc or 0) if win.rc is not None else 0


# ---------------------------------------------------------------- start
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="Kaka-Setup.exe", add_help=True)
    ap.add_argument("--silent", action="store_true", help="install without the window (uses the answers below)")
    ap.add_argument("--name")
    ap.add_argument("--city")
    ap.add_argument("--dir", help="install folder")
    ap.add_argument("--key", help="Gemini API key to save")
    ap.add_argument("--mode", choices=["install", "repair", "uninstall"])
    ap.add_argument("--options", help="comma list: desktop,startup,browsers,ffmpeg,face,gestures,discord,wake (or none)")
    ap.add_argument("--delete-data", action="store_true")
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--log-out")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--shots")
    ap.add_argument("--autopilot", action="store_true")
    ap.add_argument("--shots-no-run", action="store_true")
    ap.add_argument("--shots-extra", default="")
    ap.add_argument("--powershell")
    rest = []
    folder_preset = None
    for x in argv:                        # the bat's own switches: /folder=<hex> (admin restart) and /uninstall
        lx = x.lower()
        if lx.startswith("/folder="):
            try:
                folder_preset = bytes.fromhex(x.split("=", 1)[1]).decode("utf-8")
            except Exception:
                pass
        elif lx == "/uninstall":
            rest.append("--uninstall")
        else:
            rest.append(x)
    a = ap.parse_args(rest)
    a.folder_preset = folder_preset
    if a.uninstall and not a.mode:
        a.mode = "uninstall" if a.silent else None
    return a


def main():
    a = parse_args(sys.argv[1:])
    if a.silent:
        return run_silent(a)
    return run_gui(a)


if __name__ == "__main__":
    sys.exit(main())
