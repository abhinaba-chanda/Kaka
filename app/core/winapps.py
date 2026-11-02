"""
core/winapps.py - find and start Windows desktop apps reliably (Kaka update 8, 9).

WHY
    open_app used to (1) treat any window whose TITLE mentioned the app as the
    app itself - a Brave tab about "Visual Studio Code" was "VS Code", so Kaka
    brought Brave to the front - and (2) fall back to typing the name into the
    Windows search box, which opens a Bing web search in the browser when the
    name is not an installed app. Both looked like "Kaka opens everything in the
    browser".

WHAT THIS DOES
    resolve("vs code kholo") -> where the real program is, found in this order:
        known install folders, App Paths registry, PATH (`where`), Start Menu
        shortcuts (user + all users, fuzzy name match), Windows' own app list
        (Get-StartApps, which also covers Store apps such as WhatsApp).
    Nothing here ever opens a browser or a web search. Internet shortcuts
    (.url) are ignored on purpose.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass
class App:
    key: str
    title: str
    aliases: tuple
    exes: tuple = ()          # program file names, e.g. ("Code.exe",)
    paths: tuple = ()         # likely full paths, %VARS% allowed
    lnk: tuple = ()           # Start Menu shortcut names
    winget: str = ""          # winget id, for an install the user agreed to
    procs: tuple = ()         # process names (no .exe) when it runs
    uri: str = ""             # a protocol that opens it (ms-settings:)
    always_new: bool = False  # explorer / cmd: open a new window even if running
    browser: bool = False
    proto: str = ""           # update 9: its own link (spotify:) - used only if registered
    appids: tuple = ()        # update 9: Store app ids for shell:AppsFolder
    prefer_proto: bool = False


KNOWN: tuple = (
    App("vscode", "VS Code",
        ("vs code", "vscode", "visual studio code", "vs code editor", "code editor",
         "code", "vs kod", "visual code", "vis code", "vsc", "v s code",
         "code application", "code app", "vscode editor", "studio code"),
        exes=("Code.exe",),
        paths=(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
               r"%ProgramFiles%\Microsoft VS Code\Code.exe",
               r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
               r"%ProgramW6432%\Microsoft VS Code\Code.exe"),
        lnk=("Visual Studio Code",), winget="Microsoft.VisualStudioCode",
        procs=("code",)),
    App("chrome", "Google Chrome", ("chrome", "google chrome", "crome"),
        exes=("chrome.exe",),
        paths=(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
               r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        lnk=("Google Chrome",), winget="Google.Chrome", procs=("chrome",), browser=True),
    App("edge", "Microsoft Edge", ("edge", "microsoft edge", "ms edge"),
        exes=("msedge.exe",),
        paths=(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
               r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        lnk=("Microsoft Edge",), procs=("msedge",), browser=True),
    App("brave", "Brave", ("brave", "brave browser"),
        exes=("brave.exe",),
        paths=(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
               r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
               r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        lnk=("Brave",), winget="Brave.Brave", procs=("brave",), browser=True),
    App("firefox", "Firefox", ("firefox", "mozilla firefox", "mozilla"),
        exes=("firefox.exe",),
        paths=(r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
               r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
        lnk=("Firefox", "Mozilla Firefox"), winget="Mozilla.Firefox",
        procs=("firefox",), browser=True),
    App("opera", "Opera", ("opera", "opera gx", "operagx"), exes=("opera.exe",),
        lnk=("Opera", "Opera GX Browser", "Opera Browser"), procs=("opera",), browser=True),
    App("notepad", "Notepad", ("notepad", "note pad", "notepad app", "notes"),
        exes=("notepad.exe",), paths=(r"%WINDIR%\System32\notepad.exe", r"%WINDIR%\notepad.exe"),
        procs=("notepad",)),
    App("notepadpp", "Notepad++", ("notepad++", "notepad plus plus", "notepad plus", "npp"),
        exes=("notepad++.exe",),
        paths=(r"%ProgramFiles%\Notepad++\notepad++.exe", r"%ProgramFiles(x86)%\Notepad++\notepad++.exe"),
        lnk=("Notepad++",), winget="Notepad++.Notepad++", procs=("notepad++",)),
    App("calculator", "Calculator", ("calculator", "calc", "calci", "calculater", "hisab"),
        exes=("calc.exe",), uri="calculator:", procs=("calculatorapp", "calculator", "calc")),
    App("explorer", "File Explorer",
        ("file explorer", "explorer", "my computer", "this pc", "files", "file manager",
         "folder", "windows explorer"),
        exes=("explorer.exe",), paths=(r"%WINDIR%\explorer.exe",), procs=("explorer",),
        always_new=True),
    App("cmd", "Command Prompt", ("cmd", "command prompt", "command line"),
        exes=("cmd.exe",), paths=(r"%WINDIR%\System32\cmd.exe",), procs=("cmd",), always_new=True),
    App("powershell", "PowerShell", ("powershell", "power shell"),
        exes=("powershell.exe",),
        paths=(r"%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe",),
        procs=("powershell",), always_new=True),
    App("terminal", "Windows Terminal", ("windows terminal", "terminal"),
        exes=("wt.exe",), lnk=("Terminal", "Windows Terminal"), procs=("windowsterminal",)),
    App("paint", "Paint", ("paint", "ms paint", "mspaint"), exes=("mspaint.exe",),
        paths=(r"%WINDIR%\System32\mspaint.exe",), lnk=("Paint",), procs=("mspaint",)),
    App("taskmgr", "Task Manager", ("task manager", "taskmanager", "taskmgr"),
        exes=("Taskmgr.exe",), paths=(r"%WINDIR%\System32\Taskmgr.exe",), procs=("taskmgr",)),
    App("control", "Control Panel", ("control panel",), exes=("control.exe",),
        paths=(r"%WINDIR%\System32\control.exe",), always_new=True),
    App("settings", "Settings", ("settings", "windows settings", "setting", "pc settings"),
        uri="ms-settings:", procs=("systemsettings",)),
    App("word", "Microsoft Word", ("word", "ms word", "microsoft word", "winword"),
        exes=("WINWORD.EXE",),
        paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE",
               r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\WINWORD.EXE"),
        lnk=("Word",), procs=("winword",)),
    App("excel", "Microsoft Excel", ("excel", "ms excel", "microsoft excel"),
        exes=("EXCEL.EXE",),
        paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE",
               r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\EXCEL.EXE"),
        lnk=("Excel",), procs=("excel",)),
    App("powerpoint", "PowerPoint", ("powerpoint", "power point", "ppt", "ms powerpoint"),
        exes=("POWERPNT.EXE",),
        paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\POWERPNT.EXE",
               r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\POWERPNT.EXE"),
        lnk=("PowerPoint",), procs=("powerpnt",)),
    App("spotify", "Spotify", ("spotify",), exes=("Spotify.exe",),
        paths=(r"%APPDATA%\Spotify\Spotify.exe",), lnk=("Spotify",),
        winget="Spotify.Spotify", procs=("spotify",), proto="spotify",
        appids=("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",), prefer_proto=True),
    App("whatsapp", "WhatsApp", ("whatsapp", "whats app", "watsapp", "wp", "whatsapp desktop"),
        exes=("WhatsApp.exe",), lnk=("WhatsApp",), procs=("whatsapp", "whatsapp.root"),
        proto="whatsapp", appids=("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",)),
    App("telegram", "Telegram", ("telegram",), exes=("Telegram.exe",),
        paths=(r"%APPDATA%\Telegram Desktop\Telegram.exe",), lnk=("Telegram",),
        winget="Telegram.TelegramDesktop", procs=("telegram",), proto="tg"),
    App("discord", "Discord", ("discord",), lnk=("Discord",), procs=("discord",),
        winget="Discord.Discord", proto="discord"),
    App("vlc", "VLC", ("vlc", "vlc player", "vlc media player"), exes=("vlc.exe",),
        paths=(r"%ProgramFiles%\VideoLAN\VLC\vlc.exe", r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"),
        lnk=("VLC media player",), winget="VideoLAN.VLC", procs=("vlc",)),
    App("gitbash", "Git Bash", ("git bash", "gitbash", "bash"), exes=("git-bash.exe",),
        paths=(r"%ProgramFiles%\Git\git-bash.exe", r"%ProgramFiles(x86)%\Git\git-bash.exe"),
        lnk=("Git Bash",), procs=("mintty",)),
    App("obs", "OBS Studio", ("obs", "obs studio"), exes=("obs64.exe",),
        paths=(r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe",), lnk=("OBS Studio",),
        winget="OBSProject.OBSStudio", procs=("obs64",)),
    App("steam", "Steam", ("steam",), exes=("steam.exe",),
        paths=(r"%ProgramFiles(x86)%\Steam\steam.exe",), lnk=("Steam",), procs=("steam",)),
    App("zoom", "Zoom", ("zoom",), exes=("Zoom.exe",),
        paths=(r"%APPDATA%\Zoom\bin\Zoom.exe",), lnk=("Zoom", "Zoom Workplace"), procs=("zoom",)),
    App("camera", "Camera", ("camera", "webcam app", "camera app"),
        uri="microsoft.windows.camera:", procs=("windowscamera",)),
    App("snipping", "Snipping Tool", ("snipping tool", "snip", "screenshot tool"),
        exes=("SnippingTool.exe",), lnk=("Snipping Tool",), procs=("snippingtool",)),
    App("store", "Microsoft Store", ("microsoft store", "ms store", "store", "play store"),
        uri="ms-windows-store:", procs=("winstore.app",)),
    App("clock", "Clock", ("clock", "alarms", "alarm clock", "ghadi"), uri="ms-clock:",
        procs=("time",)),
)

# Words that wrap an app name in a spoken command ("VS Code kholo na").
_FILLER = {
    "open", "launch", "start", "run", "the", "app", "application", "applications",
    "program", "software", "please", "pls", "plz", "kholo", "khol", "kholna", "kholiye",
    "kar", "karo", "kardo", "kar do", "do", "de", "dena", "dijiye", "na", "ko", "mein",
    "me", "mai", "main", "on", "chalu", "chalao", "chala", "wala", "wali", "wale",
    "zara", "jara", "ek", "baar", "bhai", "kaka", "my", "a", "an", "desktop", "pc",
    "computer", "ka", "ki", "ke", "se", "par", "pe", "window",
}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("+", " plus ").replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def clean_query(spoken: str) -> str:
    words = [w for w in fold(spoken).split() if w not in _FILLER]
    return " ".join(words) or fold(spoken)


def _alias_fold(a: str) -> str:
    return fold(a)


def known_app(spoken: str) -> Optional[App]:
    """The KNOWN entry the words refer to, or None. Hinglish wrappers are ignored."""
    q = clean_query(spoken)
    raw = fold(spoken)
    if not q:
        return None
    best, best_score = None, 0
    for app in KNOWN:
        for a in app.aliases + (app.title,):
            af = _alias_fold(a)
            if not af:
                continue
            if q == af or raw == af:
                score = 100
            elif len(af) >= 4 and re.search(r"(^| )" + re.escape(af) + r"( |$)", q):
                score = 80 + min(len(af), 15)
            else:
                r = difflib.SequenceMatcher(None, q, af).ratio()
                score = int(r * 85) if (r >= 0.86 and len(q) >= 4) else 0
            if score > best_score:
                best, best_score = app, score
    return best if best_score >= 80 else None


# ---------------------------------------------------------------- lookups
def _expand(p: str) -> str:
    out = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), p)
    out = os.path.expandvars(out)
    if os.sep != "\\":
        out = out.replace("\\", os.sep)      # tests on a non-Windows box
    return "" if "%" in out else out


def _isfile(p: str) -> bool:
    try:
        return bool(p) and os.path.isfile(p)
    except Exception:
        return False


def app_paths_registry(exe: str) -> str:
    """HKCU/HKLM ...\\App Paths\\<exe> default value (how Win+R finds programs)."""
    try:
        import winreg  # type: ignore
    except Exception:
        return ""
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    val = os.path.expandvars(str(val or "").strip().strip('"'))
                    if _isfile(val):
                        return val
            except Exception:
                continue
    return ""


def where(name: str) -> str:
    """PATH lookup: shutil.which first, then Windows' own `where`."""
    hit = shutil.which(name)
    if hit:
        return hit
    if not sys.platform.startswith("win"):
        return ""
    try:
        r = subprocess.run(["where", name], capture_output=True, text=True, timeout=4,
                           creationflags=_NO_WINDOW)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line and _isfile(line):
                return line
    except Exception:
        pass
    return ""


def start_menu_dirs() -> list:
    out = []
    for base, rel in (("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
                      ("ProgramData", r"Microsoft\Windows\Start Menu\Programs"),
                      ("ALLUSERSPROFILE", r"Microsoft\Windows\Start Menu\Programs"),
                      ("USERPROFILE", "Desktop"), ("PUBLIC", "Desktop")):
        root = os.environ.get(base)
        if root:
            d = os.path.join(root, *rel.split("\\"))
            if os.path.isdir(d) and d not in out:
                out.append(d)
    return out


_LNK_CACHE: dict = {"at": 0.0, "items": []}
_BAD_LNK = re.compile(r"uninstall|readme|read me|help|website|documentation|manual|"
                      r"release notes|license|support|on the web", re.I)


def shortcuts(refresh: bool = False) -> list:
    """Every .lnk in the Start Menu (user + all users) and on the desktops."""
    now = time.monotonic()
    if not refresh and _LNK_CACHE["items"] and now - _LNK_CACHE["at"] < 300:
        return _LNK_CACHE["items"]
    items = []
    for d in start_menu_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".lnk") and not _BAD_LNK.search(f):
                    items.append((os.path.splitext(f)[0], os.path.join(root, f)))
    _LNK_CACHE.update(at=now, items=items)
    return items


def _name_score(query: str, name: str) -> int:
    q, n = fold(query), fold(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    qt, nt = q.split(), n.split()
    if all(t in nt for t in qt):
        return 90 - min(len(nt) - len(qt), 10)
    if n.startswith(q + " ") or (len(q) >= 4 and n.startswith(q)):
        return 82
    r = difflib.SequenceMatcher(None, q, n).ratio()
    return int(r * 85)


def find_shortcut(names, threshold: int = 80) -> str:
    best, best_s = "", 0
    for want in names:
        for stem, path in shortcuts():
            s = _name_score(want, stem)
            if s > best_s:
                best, best_s = path, s
    return best if best_s >= threshold else ""


def lnk_target(lnk: str) -> str:
    """The program a .lnk points to (PowerShell's WScript.Shell; '' on failure)."""
    if not sys.platform.startswith("win"):
        return ""
    try:
        cmd = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath"
               % lnk.replace("'", "''"))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW)
        t = (r.stdout or "").strip().splitlines()
        return t[-1].strip() if t and _isfile(t[-1].strip()) else ""
    except Exception:
        return ""


_START_APPS: dict = {"at": 0.0, "items": None}


def start_apps(refresh: bool = False) -> list:
    """Windows' own installed-app list (Get-StartApps): [(name, appid)]. Covers
    Store apps. Web links (AppID starting with http) are dropped."""
    if not sys.platform.startswith("win"):
        return []
    now = time.monotonic()
    if not refresh and _START_APPS["items"] is not None and now - _START_APPS["at"] < 600:
        return _START_APPS["items"]
    if not refresh and _START_APPS["items"] is None:
        # update 14: Get-StartApps starts PowerShell (1-4 s) - answer from the
        # copy saved in F:\Kaka\Data\Cache (up to 7 days old) and refresh it
        # quietly in the background.
        disk = _start_apps_disk()
        if disk is not None:
            _START_APPS.update(at=now, items=disk)
            _refresh_start_apps_bg()
            return disk
    items = _get_start_apps()
    _START_APPS.update(at=now, items=items)
    try:
        from core import speed
        if items:
            speed.save("start-apps", items)
    except Exception:
        pass
    return items


def _start_apps_disk():
    try:
        from core import speed
        obj = speed.load("start-apps", 7 * 86400)
        if obj and isinstance(obj.get("data"), list) and obj["data"]:
            return [(str(n), str(a)) for n, a in obj["data"]]
    except Exception:
        pass
    return None


_SA_BG = {"running": False}


def _refresh_start_apps_bg() -> None:
    if _SA_BG["running"]:
        return
    _SA_BG["running"] = True

    def _run():
        try:
            items = _get_start_apps()
            if items:
                _START_APPS.update(at=time.monotonic(), items=items)
                from core import speed
                speed.save("start-apps", items)
        except Exception:
            pass
        finally:
            _SA_BG["running"] = False
    threading.Thread(target=_run, daemon=True, name="start-apps-refresh").start()


def _get_start_apps() -> list:
    items = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-StartApps | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=12, creationflags=_NO_WINDOW)
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for d in data:
            n, a = str(d.get("Name") or ""), str(d.get("AppID") or "")
            if n and a and not a.lower().startswith(("http:", "https:")) and not _BAD_LNK.search(n):
                items.append((n, a))
    except Exception as e:
        print(f"[winapps] Get-StartApps failed: {e}")
    return items


def find_start_app(names, threshold: int = 85) -> tuple:
    best, best_s = ("", ""), 0
    for want in names:
        for n, a in start_apps():
            s = _name_score(want, n)
            if s > best_s:
                best, best_s = (n, a), s
    return best if best_s >= threshold else ("", "")


# ---------------------------------------------------------------- resolution
@dataclass
class Target:
    title: str
    kind: str            # exe | lnk | uri | appid
    value: str
    procs: tuple = ()
    app: Optional[App] = None
    how: str = ""


def _exe_for(app: App) -> tuple:
    for p in app.paths:
        e = _expand(p)
        if _isfile(e):
            return e, "install folder"
    for exe in app.exes:
        hit = app_paths_registry(exe)
        if hit:
            return hit, "App Paths registry"
    for exe in app.exes:
        hit = where(exe)
        if hit:
            return hit, "PATH"
    return "", ""


def find_vscode() -> str:
    """Path to Code.exe (or bin\\code.cmd as a last resort); '' when VS Code is
    not installed. Checks install folders, App Paths, PATH (`code`), `where code`
    and the Start Menu shortcut."""
    vs = KNOWN[0]
    exe, _ = _exe_for(vs)
    if exe:
        return exe
    for name in ("code.cmd", "code"):
        hit = where(name)
        if hit:
            # ...\Microsoft VS Code\bin\code.cmd -> ...\Microsoft VS Code\Code.exe
            guess = os.path.join(os.path.dirname(os.path.dirname(hit)), "Code.exe")
            return guess if _isfile(guess) else hit
    lnk = find_shortcut(vs.lnk, threshold=95)
    if lnk:
        t = lnk_target(lnk)
        return t or lnk
    return ""


def resolve(spoken: str) -> Optional[Target]:
    """Where the app the user named really is, or None. Never a web address."""
    app = known_app(spoken)
    if app is not None:
        if app.key == "vscode":
            p = find_vscode()
            if p:
                kind = "lnk" if p.lower().endswith(".lnk") else "exe"
                return Target(app.title, kind, p, app.procs, app, "vscode finder")
            return None
        plan = launch_plan(spoken)
        if plan:
            return plan[0]
        return None
    return _resolve_other(spoken)


def _resolve_known_v8(app: App) -> Optional[Target]:
    """Update-8 order for a KNOWN app (kept as the fallback part of the plan)."""
    if True:
        exe, how = _exe_for(app)
        if exe:
            return Target(app.title, "exe", exe, app.procs, app, how)
        if app.uri:
            return Target(app.title, "uri", app.uri, app.procs, app, "protocol")
        lnk = find_shortcut(app.lnk or (app.title,), threshold=90)
        if lnk:
            return Target(app.title, "lnk", lnk, app.procs, app, "Start Menu")
        n, a = find_start_app(app.lnk + (app.title,) if app.lnk else (app.title,))
        if a:
            return Target(n, "appid", a, app.procs, app, "Windows app list")
        return None


def _resolve_other(spoken: str) -> Optional[Target]:
    # Anything else: a program on this PC by its Start Menu / app-list name.
    q = clean_query(spoken)
    if not q:
        return None
    lnk = find_shortcut((q,))
    if lnk:
        return Target(os.path.splitext(os.path.basename(lnk))[0], "lnk", lnk, (), None,
                      "Start Menu")
    for exe in (q.replace(" ", "") + ".exe", q.replace(" ", "")):
        hit = app_paths_registry(exe if exe.endswith(".exe") else exe + ".exe") or (
            where(exe) if " " not in q else "")
        if hit:
            return Target(q, "exe", hit, (os.path.splitext(os.path.basename(hit))[0].lower(),),
                          None, "PATH")
    n, a = find_start_app((q,))
    if a:
        return Target(n, "appid", a, (), None, "Windows app list")
    return None


# ---------------------------------------------------------------- update 9: launching
LAST_LAUNCH: dict = {}


def is_exec_alias(path: str) -> bool:
    """%LOCALAPPDATA%/Microsoft/WindowsApps/X.exe is a Store 'App Execution
    Alias', not the real program: start it through the shell, never Popen."""
    p = str(path or "").replace("/", "\\").lower()
    return "\\microsoft\\windowsapps\\" in p


_PROTO_CACHE: dict = {}


def protocol_registered(scheme: str) -> bool:
    """Is 'scheme:' (spotify:, whatsapp:) registered on this PC? Checked first so
    Windows never shows 'You'll need a new app to open this link'."""
    scheme = str(scheme or "").rstrip(":").lower()
    if not scheme or not sys.platform.startswith("win"):
        return False
    if scheme in _PROTO_CACHE:
        return _PROTO_CACHE[scheme]
    ok = False
    try:
        import winreg  # type: ignore
        for hive, sub in ((winreg.HKEY_CURRENT_USER, "Software\\Classes\\" + scheme),
                          (winreg.HKEY_CLASSES_ROOT, scheme)):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    try:
                        winreg.QueryValueEx(k, "URL Protocol")
                        ok = True
                    except OSError:
                        try:
                            with winreg.OpenKey(k, "shell\\open\\command"):
                                ok = True
                        except OSError:
                            pass
            except OSError:
                continue
            if ok:
                break
    except Exception:
        pass
    if not ok:
        try:      # Store apps register their links through the package
            import ctypes
            from ctypes import wintypes
            n = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(260)
            r = ctypes.windll.shlwapi.AssocQueryStringW(0x1000, 4, scheme, None, buf,
                                                        ctypes.byref(n))
            ok = (r == 0 and bool(buf.value))
        except Exception:
            pass
    _PROTO_CACHE[scheme] = ok
    return ok


def appid_installed(appid: str) -> str:
    """The AppID as Windows lists it (Get-StartApps), or '' if not installed."""
    want = str(appid or "").lower()
    if not want:
        return ""
    fam = want.split("!")[0]
    for _n, a in start_apps():
        al = a.lower()
        if al == want or al.split("!")[0] == fam:
            return a
    return ""


def launch_plan(spoken: str, uri: str = "") -> list:
    """Every way to start the app, best first, only ways that exist on this PC.
    uri: a deeper link to open instead of the bare 'spotify:' (spotify:search:..)."""
    app = known_app(spoken)
    if app is None:
        t = _resolve_other(spoken)
        return [t] if t else []
    if app.key == "vscode":
        p = find_vscode()
        if p:
            kind = "lnk" if p.lower().endswith(".lnk") else "exe"
            return [Target(app.title, kind, p, app.procs, app, "vscode finder")]
        return []
    plan: list = []

    def add(t):
        if t is None:
            return
        for o in plan:
            if o.kind == t.kind and o.value.lower() == t.value.lower():
                return
        plan.append(t)

    proto = ""
    if app.proto and protocol_registered(app.proto):
        proto = uri or (app.proto + ":")
    folder = ""
    for p in app.paths:
        e = _expand(p)
        if _isfile(e):
            folder = e
            break
    exe, how = _exe_for(app)
    alias = exe if (exe and is_exec_alias(exe)) else ""
    if app.prefer_proto and proto:
        add(Target(app.title, "uri", proto, app.procs, app, f"{app.proto}: link"))
    if folder:
        add(Target(app.title, "exe", folder, app.procs, app, "install folder"))
    if exe and not alias:
        add(Target(app.title, "exe", exe, app.procs, app, how))
    if proto and not app.prefer_proto:
        add(Target(app.title, "uri", proto, app.procs, app, f"{app.proto}: link"))
    if app.uri:
        add(Target(app.title, "uri", app.uri, app.procs, app, "protocol"))
    for aid in app.appids:
        real = appid_installed(aid)
        if real:
            add(Target(app.title, "appid", real, app.procs, app, "Store app"))
    lnk = find_shortcut(app.lnk or (app.title,), threshold=90)
    if lnk:
        add(Target(app.title, "lnk", lnk, app.procs, app, "Start Menu"))
    if alias:
        add(Target(app.title, "exe", alias, app.procs, app, "Store alias"))
    if not plan:
        add(_resolve_known_v8(app))
    return plan


def launch(t: Target, args: Optional[list] = None) -> bool:
    """Start it. No shell window, no browser. -> was the start requested OK.
    Update 9: a Store alias / link / shortcut goes through the shell
    (ShellExecute); LAST_LAUNCH says which way was used and why it failed."""
    LAST_LAUNCH.clear()
    LAST_LAUNCH.update(title=t.title, kind=t.kind, value=t.value, how=t.how, error="")
    try:
        from core import winfocus
        names = list(t.procs or ())
        if t.kind == "exe":
            names.append(os.path.basename(t.value))
        elif t.kind == "lnk":
            names.append(os.path.splitext(os.path.basename(t.value))[0])
            names.append(t.title)
        winfocus.mark_user_app(*names)
    except Exception:
        pass
    try:
        if t.kind == "exe":
            path = t.value
            if is_exec_alias(path) and not args:
                LAST_LAUNCH["method"] = "shell (Store alias)"
                os.startfile(path)  # noqa: S606
            elif path.lower().endswith((".cmd", ".bat")):
                LAST_LAUNCH["method"] = "cmd file"
                subprocess.Popen([path] + list(args or []), creationflags=_NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                LAST_LAUNCH["method"] = "program file"
                subprocess.Popen([path] + list(args or []), cwd=os.path.dirname(path) or None,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            return True
        if t.kind in ("lnk", "uri"):
            LAST_LAUNCH["method"] = "link" if t.kind == "uri" else "Start Menu shortcut"
            os.startfile(t.value)  # noqa: S606 - no shell, the OS opens it
            return True
        if t.kind == "appid":
            LAST_LAUNCH["method"] = "Store app (shell:AppsFolder)"
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + t.value])
            return True
        LAST_LAUNCH["error"] = f"unknown kind {t.kind}"
    except Exception as e:
        LAST_LAUNCH["error"] = f"{type(e).__name__}: {e}"
        print(f"[winapps] launch failed for {t.value!r}: {e}")
    return False


def open_in_editor(path: str) -> tuple:
    """Open a file in VS Code; Notepad if VS Code is missing.
    -> (ok, editor_name)."""
    code = find_vscode()
    if code:
        try:
            if code.lower().endswith(".lnk"):
                os.startfile(code)
                return True, "VS Code (file Saved\\Code folder mein hai)"
            flags = _NO_WINDOW if code.lower().endswith((".cmd", ".bat")) else 0
            subprocess.Popen([code, path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags)
            return True, "VS Code"
        except Exception as e:
            print(f"[winapps] VS Code open failed: {e}")
    try:
        np = _expand(r"%WINDIR%\System32\notepad.exe") or "notepad.exe"
        subprocess.Popen([np if _isfile(np) else "notepad.exe", path])
        return True, "Notepad"
    except Exception as e:
        print(f"[winapps] Notepad open failed: {e}")
    return False, ""


def find_c_compiler() -> str:
    for c in ("gcc", "clang", "tcc", "cc"):
        hit = shutil.which(c)
        if hit:
            return hit
    return ""


_INSTALLS: dict = {}


def winget_install(app: App, log=None) -> str:
    """Install with winget, as the normal user, in the background. Only ever
    called after the user said yes AND pressed CONFIRM on screen."""
    if not app.winget:
        return f"{app.title} ko winget se install karne ka tareeka mujhe nahi pata."
    wg = where("winget.exe") or where("winget")
    if not wg:
        return "winget is PC pe nahi mila, isliye install nahi kar paya."
    if _INSTALLS.get(app.key) == "running":
        return f"{app.title} pehle se install ho raha hai."

    def _run():
        _INSTALLS[app.key] = "running"
        try:
            r = subprocess.run([wg, "install", "--id", app.winget, "-e",
                                "--accept-source-agreements", "--accept-package-agreements",
                                "--silent"], capture_output=True, text=True,
                               timeout=900, creationflags=_NO_WINDOW)
            ok = r.returncode == 0
            msg = (f"{app.title} install ho gaya." if ok else
                   f"{app.title} install nahi hua (winget code {r.returncode}).")
        except Exception as e:
            ok, msg = False, f"{app.title} install nahi hua: {e}"
        _INSTALLS[app.key] = "done" if ok else "failed"
        _LNK_CACHE.update(at=0.0, items=[])
        if log:
            try:
                log(f"Kaka: {msg}")
            except Exception:
                pass
        if ok:
            t = resolve(app.title)
            if t:
                launch(t)

    threading.Thread(target=_run, daemon=True, name=f"winget-{app.key}").start()
    return f"{app.title} install shuru (winget --id {app.winget}). 1-3 min lagenge."


# Update 9: apps Kaka starts are no longer hidden as "part of Kaka", and
# bringing a window to the front works past Windows' foreground lock.
try:
    from core import winfocus as _winfocus
    _winfocus.install()
except Exception as _e:      # pragma: no cover
    print(f"[winapps] winfocus not installed: {_e}")
