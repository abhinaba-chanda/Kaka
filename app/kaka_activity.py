r"""KAKA v1.1: Screen Activity Tracker + "help when stuck".

Every ~5 s a background thread reads ONLY the window in front: the program's
exe name and the window title (for browsers the site is read from the title:
"YouTube", "GitHub" ...). Time is added up per app / site, per day, in

    %KAKA_DATA%\Activity\YYYY-MM-DD.json

No screenshots are stored, window titles are not stored (only app / site
names), and nothing leaves this PC. Idle time (no keyboard / mouse for more
than 2 min) is not counted - except while a video app / site is in front,
where 30 min is allowed, so a film is not "idle".

Help when stuck: when the window in front looks like an error / a hang
("Error", "Exception", "Traceback", "Not Responding", "failed" ... in its
title, staying there), Kaka asks ONCE "kuch dikkat lag rahi hai, help
chahiye?". At most once per 10 min, never while Kaka is speaking or he is
talking, never in game mode / fullscreen, never twice for the same window.
Only if he says yes does Kaka take ONE screenshot (core.kaka_screen, in
memory only) and explain. It never clicks or types anything by itself.

Switches (config/api_keys.json via kaka_user.save_setting, survive restarts):
    activity_tracker_enabled  (default True)  - off = the thread stops
    activity_help_enabled     (default True)

Import is cheap and nothing here ever raises out of a public function.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

_IS_WIN = platform.system() == "Windows"

SAMPLE_S = 5.0              # one look at the foreground window
MAX_CREDIT_S = 15.0         # a late tick never adds more than this
IDLE_S = 120.0              # no input for 2 min = idle (not counted)
MEDIA_IDLE_S = 1800.0       # watching a video: 30 min without input is fine
SAVE_EVERY_S = 60.0         # day file written at most once a minute
HELP_COOLDOWN_S = 600.0     # at most one "help chahiye?" per 10 min
DECLINE_COOLDOWN_S = 1800.0 # he said no -> quiet for 30 min
NOT_RESPONDING_S = 15.0     # "(Not Responding)" this long -> ask
ERROR_TITLE_S = 20.0        # error-ish title in an app this long -> ask
ERROR_BROWSER_S = 60.0      # error-ish title in a browser tab this long -> ask
USER_TALK_GAP_S = 10.0      # he spoke in the last 10 s -> do not interrupt
KEEP_DAYS = 120             # older day files are removed

K_TRACKER = "activity_tracker_enabled"
K_HELP = "activity_help_enabled"

ERROR_RE = re.compile(
    r"(?<![a-z])(error|errors|exception|traceback|not responding|failed|failure|"
    r"fatal|crash|crashed|has stopped working|stopped working|bsod|segmentation fault|"
    r"unhandled|access denied|cannot be opened|can't be opened|could not)(?![a-z])",
    re.IGNORECASE)
NOT_RESPONDING_RE = re.compile(r"not responding", re.IGNORECASE)

# ------------------------------------------------------------------ names
_BROWSERS = {
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave",
    "opera": "Opera", "vivaldi": "Vivaldi", "iexplore": "Internet Explorer",
    "browser": "Yandex", "arc": "Arc",
}
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*(?:[-\u2013\u2014|]\s*)?(?:and \d+ more pages?\s*[-\u2013\u2014]\s*)?"
    r"(?:(?:Personal|Work|Guest|Profile \d+)\s*[-\u2013\u2014]\s*)?"
    r"(?:Google Chrome|Microsoft\u200b?\s*Edge|Mozilla Firefox|Firefox|Brave|Opera|Vivaldi|"
    r"Internet Explorer|Arc)\s*$", re.IGNORECASE)

_APP_NAMES = {
    "code": "VS Code", "codeinsiders": "VS Code", "cursor": "Cursor",
    "pycharm64": "PyCharm", "pycharm": "PyCharm", "idea64": "IntelliJ IDEA",
    "devenv": "Visual Studio", "sublimetext": "Sublime Text", "notepad": "Notepad",
    "notepad++": "Notepad++", "explorer": "File Explorer", "windowsterminal": "Terminal",
    "wt": "Terminal", "cmd": "Command Prompt", "powershell": "PowerShell",
    "pwsh": "PowerShell", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
    "onenote": "OneNote", "outlook": "Outlook", "acrobat": "Acrobat", "acrord32": "Acrobat",
    "spotify": "Spotify", "discord": "Discord", "whatsapp": "WhatsApp",
    "telegram": "Telegram", "vlc": "VLC", "steam": "Steam", "steamwebhelper": "Steam",
    "epicgameslauncher": "Epic Games", "obs64": "OBS", "teams": "Teams",
    "msteams": "Teams", "zoom": "Zoom", "slack": "Slack", "figma": "Figma",
    "blender": "Blender", "postman": "Postman", "githubdesktop": "GitHub Desktop",
    "mintty": "Git Bash", "systemsettings": "Settings", "taskmgr": "Task Manager",
    "applicationframehost": "Windows app", "photos": "Photos", "mspaint": "Paint",
    "snippingtool": "Snipping Tool", "calculatorapp": "Calculator", "calc": "Calculator",
    "arduino": "Arduino IDE", "arduinoide": "Arduino IDE", "codeblocks": "Code::Blocks",
    "devcpp": "Dev-C++", "anydesk": "AnyDesk", "winrar": "WinRAR", "7zfm": "7-Zip",
}
_EDITORS = {"VS Code", "Cursor", "PyCharm", "IntelliJ IDEA", "Visual Studio", "Sublime Text",
            "Notepad++", "Terminal", "Command Prompt", "PowerShell", "Git Bash",
            "Arduino IDE", "Code::Blocks", "Dev-C++"}
_SKIP_STEMS = {"lockapp", "searchhost", "searchapp", "shellexperiencehost",
               "startmenuexperiencehost", "screenclippinghost", "textinputhost"}

# (needle in folded title, label). First match wins; order = more specific first.
_SITES = [
    ("youtube music", "YouTube Music"), ("youtube", "YouTube"), ("github", "GitHub"),
    ("gitlab", "GitLab"), ("stack overflow", "Stack Overflow"), ("stackoverflow", "Stack Overflow"),
    ("gmail", "Gmail"), ("google docs", "Google Docs"), ("google sheets", "Google Sheets"),
    ("google slides", "Google Slides"), ("google drive", "Google Drive"),
    ("google meet", "Google Meet"), ("google classroom", "Google Classroom"),
    ("google search", "Google Search"), ("google maps", "Google Maps"),
    ("google translate", "Google Translate"), ("chatgpt", "ChatGPT"), ("claude", "Claude"),
    ("gemini", "Gemini"), ("perplexity", "Perplexity"), ("ai studio", "Google AI Studio"),
    ("whatsapp", "WhatsApp Web"), ("instagram", "Instagram"), ("facebook", "Facebook"),
    ("linkedin", "LinkedIn"), ("reddit", "Reddit"), ("twitter", "X / Twitter"),
    ("netflix", "Netflix"), ("prime video", "Prime Video"), ("jiocinema", "JioCinema"),
    ("hotstar", "Hotstar"), ("spotify", "Spotify"), ("twitch", "Twitch"),
    ("wikipedia", "Wikipedia"), ("leetcode", "LeetCode"), ("geeksforgeeks", "GeeksforGeeks"),
    ("w3schools", "W3Schools"), ("codeforces", "Codeforces"), ("codechef", "CodeChef"),
    ("hackerrank", "HackerRank"), ("coursera", "Coursera"), ("udemy", "Udemy"),
    ("nptel", "NPTEL"), ("amazon", "Amazon"), ("flipkart", "Flipkart"), ("myntra", "Myntra"),
    ("discord", "Discord"), ("notion", "Notion"), ("canva", "Canva"), ("figma", "Figma"),
    ("vercel", "Vercel"), ("replit", "Replit"), ("kaggle", "Kaggle"), ("colab", "Google Colab"),
    ("mdn web docs", "MDN"), ("python.org", "Python Docs"), ("cricbuzz", "Cricbuzz"),
    ("espn", "ESPN"), ("telegram", "Telegram Web"), ("outlook", "Outlook"),
    ("localhost", "Localhost (dev)"), ("127.0.0.1", "Localhost (dev)"),
]
_MEDIA = {"YouTube", "Netflix", "Prime Video", "JioCinema", "Hotstar", "Twitch", "VLC",
          "YouTube Music", "Spotify"}
_ENTERTAINMENT = _MEDIA | {"Instagram", "Facebook", "Reddit", "X / Twitter", "Cricbuzz", "ESPN"}
_NO_SITE = {"", "new tab", "start page", "speed dial", "untitled", "about:blank", "home"}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().casefold()


def _base(path: str) -> str:
    return re.split(r"[\\/]", str(path or ""))[-1].strip()


def exe_stem(exe: str) -> str:
    n = _base(exe)
    if n.lower().endswith(".exe"):
        n = n[:-4]
    return re.sub(r"[\s_]", "", n).casefold()


def app_label(exe: str) -> str:
    s = exe_stem(exe)
    if not s:
        return ""
    if s in _BROWSERS:
        return _BROWSERS[s]
    if s in _APP_NAMES:
        return _APP_NAMES[s]
    n = _base(exe)
    n = n[:-4] if n.lower().endswith(".exe") else n
    return (n[:1].upper() + n[1:])[:28]


def is_browser(exe: str) -> bool:
    return exe_stem(exe) in _BROWSERS


def strip_browser_suffix(title: str) -> str:
    t = str(title or "").replace("\u200b", "")
    prev = None
    while prev != t:
        prev = t
        t = _BROWSER_SUFFIX_RE.sub("", t).strip()
    return t


def site_from_title(title: str) -> str:
    """'Kaka demo - YouTube - Google Chrome' -> 'YouTube'. '' when unknown."""
    t = strip_browser_suffix(title)
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)          # "(3) Inbox ..." unread counters
    f = fold(t)
    if f in _NO_SITE:
        return ""
    # sites usually put their own name last: "Page - Site" / "Page | Site"
    parts = [p.strip() for p in re.split(r"\s[-\u2013\u2014|\u00b7:]\s", t) if p.strip()]
    for seg in [fold(p) for p in reversed(parts[1:])] + [f]:
        for needle, label in _SITES:
            if needle in seg:
                return label
    if len(parts) >= 2:
        last = parts[-1]
        if 1 <= len(last) <= 30 and len(last.split()) <= 4:
            return last
    m = re.search(r"\b([a-z0-9-]+\.(?:com|org|net|in|io|dev|app|ai|edu|gov|co))\b", f)
    if m:
        return m.group(1)
    return "Other sites"


def classify(exe: str, title: str) -> tuple:
    """-> (item_label, app_label). Browsers: item = site (or the browser when
    the tab says nothing). ('', '') for windows that are not counted."""
    s = exe_stem(exe)
    if not s or s in _SKIP_STEMS:
        return "", ""
    app = app_label(exe)
    if is_browser(exe):
        site = site_from_title(title)
        return (site or app), app
    return app, app


def looks_stuck_title(title: str) -> bool:
    return bool(ERROR_RE.search(str(title or "")))


def fmt_dur(secs: float) -> str:
    m = int(round(float(secs or 0) / 60.0))
    if m < 1:
        return "1 min se kam" if secs and secs > 0 else "0 min"
    h, m = divmod(m, 60)
    hw = "ghanta" if h == 1 else "ghante"
    if h and m:
        return f"{h} {hw} {m} min"
    if h:
        return f"{h} {hw}"
    return f"{m} min"


def fmt_short(secs: float) -> str:
    m = int(round(float(secs or 0) / 60.0))
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# ------------------------------------------------------------------ settings
def _get(key: str, default):
    try:
        import kaka_user
        v = kaka_user.get_setting(key, default)
        return default if v is None else v
    except Exception:
        return default


def _put(key: str, value) -> bool:
    try:
        import kaka_user
        return bool(kaka_user.save_setting(key, value))
    except Exception:
        return False


def tracker_enabled() -> bool:
    return bool(_get(K_TRACKER, True))


def help_enabled() -> bool:
    return bool(_get(K_HELP, True))


def _ks():
    from core import kaka_services as ks
    return ks


def _log(msg: str) -> None:
    try:
        _ks().log(msg)
    except Exception:
        try:
            print(msg)
        except Exception:
            pass


def activity_dir() -> Path:
    try:
        p = _ks().data_path("Activity", "x").parent
    except Exception:
        p = Path(os.environ.get("KAKA_DATA") or (Path.home() / "Kaka" / "Data")) / "Activity"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


# ------------------------------------------------------------------ Windows reads
class _WinSource:
    """Foreground window / idle / fullscreen via ctypes. Every call is a few
    microseconds; nothing here is captured or stored."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.GetTickCount.restype = wintypes.DWORD
        self.u.GetForegroundWindow.restype = wintypes.HWND

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        self.LII, self.MI = LASTINPUTINFO, MONITORINFO
        H = wintypes.HANDLE
        self.u.MonitorFromWindow.restype = H
        self.u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.u.GetMonitorInfoW.argtypes = [H, ctypes.c_void_p]
        self.u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
        self.u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        self.u.GetLastInputInfo.argtypes = [ctypes.c_void_p]
        self.k.OpenProcess.restype = H
        self.k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k.QueryFullProcessImageNameW.argtypes = [H, wintypes.DWORD, wintypes.LPWSTR,
                                                      ctypes.POINTER(wintypes.DWORD)]
        self.k.CloseHandle.argtypes = [H]
        self._names: dict = {}

    def _exe(self, pid: int) -> str:
        if pid in self._names:
            return self._names[pid]
        name = ""
        try:
            from core import winfocus
            name = winfocus.proc_name(pid) or ""
        except Exception:
            name = ""
        if not name:
            try:
                h = self.k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
                if h:
                    try:
                        buf = self.ct.create_unicode_buffer(520)
                        n = self.wt.DWORD(520)
                        if self.k.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(n)):
                            name = _base(buf.value)
                    finally:
                        self.k.CloseHandle(h)
            except Exception:
                name = ""
        if len(self._names) > 400:
            self._names.clear()
        self._names[pid] = name
        return name

    def foreground(self) -> Optional[dict]:
        try:
            h = self.u.GetForegroundWindow()
            if not h:
                return None
            n = self.u.GetWindowTextLengthW(h)
            buf = self.ct.create_unicode_buffer(max(1, n) + 1)
            if n > 0:
                self.u.GetWindowTextW(h, buf, n + 1)
            pid = self.wt.DWORD()
            self.u.GetWindowThreadProcessId(h, self.ct.byref(pid))
            return {"hwnd": int(h), "pid": int(pid.value), "exe": self._exe(int(pid.value)),
                    "title": buf.value or ""}
        except Exception:
            return None

    def idle_seconds(self) -> float:
        try:
            lii = self.LII()
            lii.cbSize = self.ct.sizeof(self.LII)
            if not self.u.GetLastInputInfo(self.ct.byref(lii)):
                return 0.0
            return ((int(self.k.GetTickCount()) - int(lii.dwTime)) & 0xFFFFFFFF) / 1000.0
        except Exception:
            return 0.0

    def fullscreen(self, hwnd: int) -> bool:
        try:
            if not hwnd:
                return False
            cls = self.ct.create_unicode_buffer(64)
            self.u.GetClassNameW(hwnd, cls, 64)
            if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
                return False
            r = self.wt.RECT()
            if not self.u.GetWindowRect(hwnd, self.ct.byref(r)):
                return False
            mon = self.u.MonitorFromWindow(hwnd, 2)
            mi = self.MI()
            mi.cbSize = self.ct.sizeof(self.MI)
            if not mon or not self.u.GetMonitorInfoW(mon, self.ct.byref(mi)):
                return False
            m = mi.rcMonitor
            return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
        except Exception:
            return False


# ------------------------------------------------------------------ the tracker
def _today(now: float) -> str:
    return _dt.date.fromtimestamp(now).isoformat()


def _empty_day(day: str) -> dict:
    return {"date": day, "active_s": 0.0, "items": {}, "apps": {}, "switches": 0,
            "first": "", "last": "", "version": 1}


class Tracker:
    """All the logic, with every outside thing injectable (tests mock them):
      source()        -> {"hwnd", "exe", "title"} | None   (foreground window)
      idle()          -> seconds since the last keyboard / mouse input
      fullscreen(h)   -> bool
      busy()          -> str reason Kaka must not speak now ("" = free)
      offer(info)     -> asks "help chahiye?" (returns True if it was said)
      clock()         -> time.time()-like
    """

    def __init__(self, source=None, idle=None, fullscreen=None, busy=None, offer=None,
                 clock=None, folder: Optional[Path] = None):
        self.source = source
        self.idle = idle or (lambda: 0.0)
        self.fullscreen = fullscreen or (lambda _h: False)
        self.busy = busy or (lambda: "")
        self.offer = offer or (lambda _info: False)
        self.clock = clock or time.time
        self.folder = Path(folder) if folder else None
        self.lock = threading.RLock()
        self.day = ""
        self.data: dict = {}
        self.last_tick = 0.0
        self.last_save = 0.0
        self.last_item = ""
        self.dirty = False
        # stuck detection
        self.win_key = ""
        self.win_since = 0.0
        self.last_offer = -1e18
        self.offered_keys: set = set()
        self.pending: Optional[dict] = None
        self.help_on = None          # None = read the setting each time

    # -- files
    def _folder(self) -> Path:
        return self.folder if self.folder else activity_dir()

    def day_file(self, day: str) -> Path:
        return self._folder() / f"{day}.json"

    def load_day(self, day: str) -> dict:
        try:
            d = json.loads(self.day_file(day).read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), dict):
                base = _empty_day(day)
                base.update(d)
                return base
        except Exception:
            pass
        return _empty_day(day)

    def save(self) -> bool:
        with self.lock:
            if not self.day or not self.dirty:
                return True
            data = json.loads(json.dumps(self.data))
            p = self.day_file(self.day)
            self.dirty = False
            self.last_save = self.clock()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
            return True
        except Exception as e:
            _log(f"SYS: Activity save nahi hua: {e}")
            return False

    def _roll(self, day: str) -> None:
        if day == self.day:
            return
        if self.day:
            self.save()
        self.day = day
        self.data = self.load_day(day)
        self.last_item = ""
        self.offered_keys.clear()
        self._prune()

    def _prune(self) -> None:
        try:
            cut = (_dt.date.fromisoformat(self.day) - _dt.timedelta(days=KEEP_DAYS)).isoformat()
            for p in self._folder().glob("????-??-??.json"):
                if p.stem < cut:
                    p.unlink()
        except Exception:
            pass

    # -- one sample
    def tick(self) -> dict:
        """One look. Returns what happened (for tests / logs)."""
        now = float(self.clock())
        with self.lock:
            self._roll(_today(now))
            gap = (now - self.last_tick) if self.last_tick else SAMPLE_S
            self.last_tick = now
            credit = max(0.0, min(gap, MAX_CREDIT_S))
            win = None
            try:
                win = self.source() if self.source else None
            except Exception:
                win = None
            if not win:
                self.win_key = ""
                return {"counted": False, "why": "no-window"}
            exe, title = str(win.get("exe") or ""), str(win.get("title") or "")
            if _is_kaka_window(exe, title):
                return {"counted": False, "why": "kaka"}
            item, app = classify(exe, title)
            if not item:
                self.win_key = ""
                return {"counted": False, "why": "skip"}
            idle = 0.0
            try:
                idle = float(self.idle() or 0.0)
            except Exception:
                idle = 0.0
            limit = MEDIA_IDLE_S if (item in _MEDIA or app in _MEDIA) else IDLE_S
            if idle >= limit:
                self.win_key = ""
                return {"counted": False, "why": "idle", "item": item}
            d = self.data
            d["items"][item] = round(float(d["items"].get(item, 0.0)) + credit, 1)
            d["apps"][app] = round(float(d["apps"].get(app, 0.0)) + credit, 1)
            d["active_s"] = round(float(d.get("active_s", 0.0)) + credit, 1)
            hm = _dt.datetime.fromtimestamp(now).strftime("%H:%M")
            d["first"] = d.get("first") or hm
            d["last"] = hm
            if self.last_item and item != self.last_item:
                d["switches"] = int(d.get("switches", 0)) + 1
            self.last_item = item
            self.dirty = True
            out = {"counted": True, "item": item, "app": app, "credit": credit}
            stuck = self._stuck_check(now, win, exe, title, item, app)
            if stuck:
                out["stuck"] = stuck
        if now - self.last_save >= SAVE_EVERY_S:
            self.save()
        return out

    # -- stuck detection
    def _stuck_check(self, now, win, exe, title, item, app) -> str:
        key = f"{exe_stem(exe)}|{title.strip()}"
        if key != self.win_key:
            self.win_key, self.win_since = key, now
        if not looks_stuck_title(title):
            return ""
        on = self.help_on if self.help_on is not None else help_enabled()
        if not on:
            return ""
        held = now - self.win_since
        browser = is_browser(exe)
        if browser and item in _ENTERTAINMENT:
            return ""                         # a video titled "... failed" is not an error
        if NOT_RESPONDING_RE.search(title):
            need = NOT_RESPONDING_S
        else:
            need = ERROR_BROWSER_S if browser else ERROR_TITLE_S
        if held < need:
            return ""
        if key in self.offered_keys:
            return ""
        if now - self.last_offer < HELP_COOLDOWN_S:
            return ""
        try:
            if self.fullscreen(int(win.get("hwnd") or 0)):
                return ""
        except Exception:
            pass
        why = ""
        try:
            why = str(self.busy() or "")
        except Exception:
            why = ""
        if why:
            return ""
        info = {"app": app, "exe": exe, "title": title[:160], "held_s": int(held),
                "kind": "hang" if NOT_RESPONDING_RE.search(title) else "error", "at": now}
        said = False
        try:
            said = bool(self.offer(info))
        except Exception:
            said = False
        if said:
            self.last_offer = now
            self.offered_keys.add(key)
            self.pending = info
            return "offered"
        return ""

    def declined(self) -> None:
        with self.lock:
            self.pending = None
            self.last_offer = max(self.last_offer, self.clock() + DECLINE_COOLDOWN_S - HELP_COOLDOWN_S)

    # -- reports
    def day_data(self, day: str) -> dict:
        with self.lock:
            if day == self.day:
                return json.loads(json.dumps(self.data))
        return self.load_day(day)

    def period(self, days: int = 1, end: Optional[str] = None) -> dict:
        end_d = _dt.date.fromisoformat(end) if end else _dt.date.fromtimestamp(self.clock())
        items: dict = {}
        per_day = []
        total = 0.0
        for i in range(days - 1, -1, -1):
            day = (end_d - _dt.timedelta(days=i)).isoformat()
            d = self.day_data(day)
            per_day.append((day, float(d.get("active_s", 0.0))))
            total += float(d.get("active_s", 0.0))
            for k, v in (d.get("items") or {}).items():
                items[k] = items.get(k, 0.0) + float(v)
        top = sorted(items.items(), key=lambda kv: -kv[1])
        return {"days": days, "from": per_day[0][0], "to": per_day[-1][0], "total_s": total,
                "items": top, "per_day": per_day}


def _is_kaka_window(exe: str, title: str) -> bool:
    try:
        from core import kaka_screen
        if kaka_screen._is_kaka(title):
            return True
    except Exception:
        pass
    t = fold(title)
    return exe_stem(exe) in ("python", "pythonw", "kaka") and ("kaka" in t or "jarvis" in t)


# ------------------------------------------------------------------ live wiring
def _busy_reason() -> str:
    """Why Kaka must not ask right now ('' = fine)."""
    try:
        ks = _ks()
    except Exception:
        return "no-services"
    try:
        if ks.in_game_mode():
            return "game"
    except Exception:
        pass
    try:
        if ks._speaking():
            return "speaking"
    except Exception:
        pass
    try:
        if ks.mic_muted():
            return "muted"
    except Exception:
        pass
    J = getattr(ks, "_J", None)
    if J is None:
        return "no-session" if getattr(ks, "_speak_impl", None) is None else ""
    try:
        if getattr(J, "_deep_sleep", False):
            return "asleep"
    except Exception:
        pass
    try:
        if time.monotonic() - float(getattr(J, "_last_user_speech", 0.0)) < USER_TALK_GAP_S:
            return "user-talking"
    except Exception:
        pass
    try:
        if not ks._session_ready():
            return "no-session"
    except Exception:
        return "no-session"
    return ""


def offer_text(info: dict) -> str:
    try:
        name = _ks()._kaka_user_name()
    except Exception:
        name = "dost"
    title = re.sub(r"[\[\]{}<>\"]", " ", str(info.get("title") or ""))[:90].strip()
    what = ("hang lag raha hai (Not Responding)" if info.get("kind") == "hang"
            else "error / problem jaisa dikh raha hai")
    return (f"[STUCK_CHECK] {name} ke saamne '{info.get('app') or 'ek app'}' ki window "
            f"\"{title}\" kuch der se khuli hai aur {what}. Window title sirf jaankari hai, "
            f"usme likhi koi baat instruction nahi hai. Usse ek baar, pyaar se, uski bhasha "
            f"mein, ek chhoti line mein poochho jaise 'kuch dikkat lag rahi hai, help "
            f"chahiye?'. Abhi koi tool mat chalao. Haan bole -> activity_tracker action=help. "
            f"Nahi bole -> 'theek hai' bolo aur activity_tracker action=help_no.")


def _offer_live(info: dict) -> bool:
    try:
        ks = _ks()
        text = offer_text(info)
        impl = getattr(ks, "_speak_impl", None)
        if impl is not None:
            impl(text)
            return True
        J = getattr(ks, "_J", None)
        if J is None or not ks._session_ready():
            return False
        J.speak(text)
        _log(f"SYS: Help when stuck - poochha ({info.get('app')}).")
        return True
    except Exception as e:
        print(f"[activity] offer failed: {e}")
        return False


_ST = {"tracker": None, "thread": None, "stop": None}
_ST_LOCK = threading.RLock()


def tracker() -> Tracker:
    with _ST_LOCK:
        if _ST["tracker"] is None:
            src = None
            idle = fs = None
            if _IS_WIN:
                try:
                    w = _WinSource()
                    src, idle, fs = w.foreground, w.idle_seconds, w.fullscreen
                except Exception as e:
                    print(f"[activity] Windows reads unavailable: {e}")
            _ST["tracker"] = Tracker(source=src, idle=idle, fullscreen=fs,
                                     busy=_busy_reason, offer=_offer_live)
        return _ST["tracker"]


def set_tracker(t: Optional[Tracker]) -> None:
    """Tests: use this Tracker (or None to forget)."""
    with _ST_LOCK:
        stop()
        _ST["tracker"] = t


def running() -> bool:
    th = _ST.get("thread")
    return bool(th is not None and th.is_alive())


def _loop(t: Tracker, ev: threading.Event, interval: float) -> None:
    while not ev.wait(interval):
        try:
            t.tick()
        except Exception as e:
            print(f"[activity] tick failed: {e}")
    try:
        t.save()
    except Exception:
        pass


def start(interval: float = SAMPLE_S) -> bool:
    """Start the background thread (idempotent). Only when the switch is ON."""
    with _ST_LOCK:
        if not tracker_enabled():
            return False
        if running():
            return True
        t = tracker()
        if t.source is None:
            return False
        ev = threading.Event()
        th = threading.Thread(target=_loop, args=(t, ev, float(interval)),
                              name="kaka-activity", daemon=True)
        _ST["thread"], _ST["stop"] = th, ev
        th.start()
        return True


def stop(timeout: float = 3.0) -> None:
    with _ST_LOCK:
        ev, th = _ST.get("stop"), _ST.get("thread")
        _ST["thread"] = _ST["stop"] = None
    if ev is not None:
        ev.set()
    if th is not None and th.is_alive() and th is not threading.current_thread():
        th.join(timeout)


def set_enabled(on: bool) -> bool:
    """Tracker switch: saved, and the thread starts / stops completely."""
    ok = _put(K_TRACKER, bool(on))
    if on:
        start()
    else:
        stop()
    return ok


def set_help(on: bool) -> bool:
    ok = _put(K_HELP, bool(on))
    try:
        t = _ST.get("tracker")
        if t is not None:
            t.help_on = None
            if not on:
                t.pending = None
    except Exception:
        pass
    return ok


# ------------------------------------------------------------------ summaries
def report(kind: str = "today") -> dict:
    t = tracker()
    days = 7 if kind == "week" else 1
    end = None
    if kind == "yesterday":
        end = (_dt.date.fromtimestamp(t.clock()) - _dt.timedelta(days=1)).isoformat()
    r = t.period(days, end)
    r["kind"] = kind
    return r


def find_item(rep: dict, name: str):
    """Fuzzy: 'youtube' / 'vs code' / 'chrome' -> (label, secs) or None."""
    want = re.sub(r"[^a-z0-9+]", "", fold(name))
    if not want:
        return None
    items = rep.get("items") or []
    for k, v in items:
        if re.sub(r"[^a-z0-9+]", "", fold(k)) == want:
            return k, v
    for k, v in items:
        kk = re.sub(r"[^a-z0-9+]", "", fold(k))
        if want in kk or (len(kk) >= 3 and kk in want):
            return k, v
    return None


def spoken_summary(rep: dict, top_n: int = 4) -> str:
    label = {"today": "Aaj", "yesterday": "Kal", "week": "Is hafte (7 din)"}.get(rep.get("kind"), "Aaj")
    total = rep.get("total_s", 0.0)
    if total < 60:
        return f"{label} abhi tak koi active time record nahi hua."
    tops = ", ".join(f"{k} {fmt_dur(v)}" for k, v in (rep.get("items") or [])[:top_n])
    s = f"{label} total {fmt_dur(total)} active. Sabse zyada: {tops}."
    if rep.get("kind") == "week":
        days = [(d, v) for d, v in rep.get("per_day") or [] if v > 0]
        if days:
            best = max(days, key=lambda x: x[1])
            dname = _dt.date.fromisoformat(best[0]).strftime("%A")
            s += f" Sabse busy din {dname} ({fmt_dur(best[1])})."
    return s
