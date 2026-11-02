"""
core/winfocus.py - see the apps Kaka opened, and bring them to the front
(Kaka update 9).

WHY THIS EXISTS - two bugs behind "Spotify was started but its window did not
appear in 15s" while Spotify was plainly open on his screen:

  1. Kaka could not SEE an app it had started itself. appidentity treats every
     process descended from Kaka as "Kaka itself" (so "close python" never
     kills Kaka). An app Kaka launches - Spotify, VS Code, Notepad - is a child
     of Kaka's process, so it was hidden from every "is it open yet?" check,
     forever, until Kaka restarted. install() narrows that rule: a descendant
     that is a normal desktop app (or anything Kaka launched for him) is NOT
     Kaka. Kaka's own python processes still are.

  2. Windows refuses SetForegroundWindow from a background program (the
     "foreground lock"). Kaka is in the background while he works, so the
     window existed but "bring to front" failed and that failure was reported
     as "did not appear". force_foreground() uses the standard workarounds
     (restore, ALT tap, AllowSetForegroundWindow, AttachThreadInput,
     BringWindowToTop) and then CHECKS GetForegroundWindow - keys are only ever
     typed into a window that is really in front.
"""
from __future__ import annotations

import os
import re
import sys
import time
import unicodedata

_IS_WIN = sys.platform.startswith("win")

# Desktop apps a user opens. A descendant of Kaka with one of these names is
# the user's app, not part of Kaka.
_USER_APP_STEMS = {
    "spotify", "code", "whatsapp", "whatsapproot", "discord", "telegram", "notepad",
    "notepadplusplus", "notepad++", "vlc", "winword", "excel", "powerpnt", "mspaint",
    "obs64", "steam", "zoom", "calculatorapp", "calc", "snippingtool", "taskmgr",
    "mintty", "gitbash", "windowsterminal", "wt", "msedge", "chrome", "brave",
    "firefox", "opera", "vlc", "teams", "msteams", "slack", "signal", "postman",
    "figma", "blender", "soffice", "windowscamera", "systemsettings",
}
_LAUNCHED: set = set()          # stems Kaka launched in this session
_NEVER_USER = {"python", "pythonw", "py", "pyw", "kaka", "cmd", "conhost",
               "powershell", "pwsh"}
_BROWSER_STEMS = {"chrome", "msedge", "brave", "firefox", "opera", "vivaldi",
                  "iexplore", "browser", "msedgewebview2"}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w+]|_", "", t).casefold()


def stem(name: str) -> str:
    n = os.path.basename(str(name or ""))
    if n.lower().endswith(".exe"):
        n = n[:-4]
    return fold(n)


def mark_user_app(*names) -> None:
    """Kaka is about to start this app for him: never count it as Kaka."""
    for n in names:
        s = stem(n)
        if s and s not in _NEVER_USER:
            _LAUNCHED.add(s)
            _LAUNCHED.add(s.replace(".", ""))


def is_user_app(name: str) -> bool:
    s = stem(name)
    if not s or s in _NEVER_USER:
        return False
    return s in _USER_APP_STEMS or s in _LAUNCHED or s.replace(".", "") in _LAUNCHED


def is_browser(name: str) -> bool:
    return stem(name) in _BROWSER_STEMS


# ------------------------------------------------------------ self filter
_INSTALLED = {"done": False}


def _chain_pids_v9() -> set:
    """Kaka's own process, its ancestors, and its descendants EXCEPT user apps
    it launched (and their children)."""
    from core import appidentity as ai
    pids = {os.getpid()}
    if not getattr(ai, "_PSUTIL", False):
        return pids
    psutil = ai.psutil
    try:
        me = psutil.Process(os.getpid())
        kids = me.children(recursive=True)
        info = {}
        for k in kids:
            try:
                info[k.pid] = (k.ppid(), k.name())
            except Exception:
                continue
        for pid, (ppid, name) in info.items():
            cur, ok, hops = pid, True, 0
            while cur in info and hops < 64:
                if is_user_app(info[cur][1]):
                    ok = False
                    break
                cur = info[cur][0]
                hops += 1
            if ok:
                pids.add(pid)
        parent, seen = me.parent(), set()
        while parent is not None and parent.pid not in seen and parent.pid > 1:
            seen.add(parent.pid)
            pids.add(parent.pid)
            try:
                parent = parent.parent()
            except Exception:
                break
    except Exception as exc:
        print(f"[winfocus] could not map own process tree: {exc}")
    return pids


def install() -> bool:
    """Patch core.appidentity once: user apps Kaka started are visible again."""
    if _INSTALLED["done"]:
        return True
    try:
        from core import appidentity as ai
    except Exception as exc:
        print(f"[winfocus] appidentity unavailable: {exc}")
        return False
    if hasattr(ai, "_chain_pids"):
        ai._chain_pids = _chain_pids_v9
    orig_own = getattr(ai, "own_exes", None)
    if orig_own is not None and not getattr(orig_own, "_kaka9", False):
        def own_exes(chain=None, _orig=orig_own):
            found = _orig(chain)
            keep = getattr(sys, "executable", "") or ""
            return {e for e in found
                    if os.path.normcase(e) == os.path.normcase(keep) or not is_user_app(e)}
        own_exes._kaka9 = True
        ai.own_exes = own_exes
    orig_focus = getattr(ai, "focus", None)
    if orig_focus is not None and not getattr(orig_focus, "_kaka9", False):
        def focus(match, _orig=orig_focus):
            if _IS_WIN:
                for hwnd, _pid, _t in windows(pids={getattr(match, "pid", -1)}):
                    if force_foreground(hwnd):
                        return True
                return False
            return _orig(match)
        focus._kaka9 = True
        ai.focus = focus
    _INSTALLED["done"] = True
    return True


# ------------------------------------------------------------ windows
def _user32():
    import ctypes
    return ctypes.WinDLL("user32", use_last_error=True)


def windows(pids=None) -> list:
    """Visible top-level windows with a title: [(hwnd, pid, title)]."""
    if not _IS_WIN:
        return []
    try:
        import ctypes
        from ctypes import wintypes
        u = _user32()
        out = []
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _l):
            try:
                if not u.IsWindowVisible(hwnd):
                    return True
                n = u.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pids is None or int(pid.value) in pids:
                    out.append((int(hwnd), int(pid.value), buf.value))
            except Exception:
                pass
            return True

        u.EnumWindows(PROC(_cb), 0)
        return out
    except Exception as exc:
        print(f"[winfocus] window list unavailable: {exc}")
        return []


def proc_name(pid: int) -> str:
    try:
        from core import appidentity as ai
        if getattr(ai, "_PSUTIL", False):
            return ai.psutil.Process(pid).name() or ""
    except Exception:
        pass
    return ""


def foreground() -> tuple:
    """(hwnd, pid) of the window in front, (0, 0) if unknown."""
    if not _IS_WIN:
        return 0, 0
    try:
        import ctypes
        from ctypes import wintypes
        u = _user32()
        h = u.GetForegroundWindow()
        if not h:
            return 0, 0
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return int(h), int(pid.value)
    except Exception:
        return 0, 0


def force_foreground(hwnd: int, tries: int = 3) -> bool:
    """Bring hwnd to the front despite Windows' foreground lock, and VERIFY."""
    if not _IS_WIN or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = _user32()
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        SW_RESTORE, SW_SHOW, VK_MENU, KEYUP = 9, 5, 0x12, 0x0002

        def _front_ok() -> bool:
            f = u.GetForegroundWindow()
            if not f:
                return False
            if int(f) == int(hwnd):
                return True
            a = wintypes.DWORD()
            b = wintypes.DWORD()
            u.GetWindowThreadProcessId(f, ctypes.byref(a))
            u.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(b))
            return a.value == b.value and a.value != 0

        for attempt in range(max(1, tries)):
            h = wintypes.HWND(hwnd)
            if u.IsIconic(h):
                u.ShowWindow(h, SW_RESTORE)
            else:
                u.ShowWindow(h, SW_SHOW)
            try:
                u.AllowSetForegroundWindow(-1)          # ASFW_ANY
            except Exception:
                pass
            if u.SetForegroundWindow(h) and _front_ok():
                return True
            # ALT tap: Windows lets the process that got the last input event
            # change the foreground window.
            u.keybd_event(VK_MENU, 0, 0, 0)
            u.keybd_event(VK_MENU, 0, KEYUP, 0)
            if u.SetForegroundWindow(h) and _front_ok():
                return True
            fore = u.GetForegroundWindow()
            their = u.GetWindowThreadProcessId(fore, None) if fore else 0
            ours = k.GetCurrentThreadId()
            attached = False
            if their and their != ours:
                attached = bool(u.AttachThreadInput(ours, their, True))
            try:
                u.BringWindowToTop(h)
                u.SetForegroundWindow(h)
                try:
                    u.SetActiveWindow(h)
                except Exception:
                    pass
            finally:
                if attached:
                    u.AttachThreadInput(ours, their, False)
            time.sleep(0.15)
            if _front_ok():
                return True
            try:
                u.SwitchToThisWindow(h, True)
            except Exception:
                pass
            time.sleep(0.25 + 0.2 * attempt)
            if _front_ok():
                return True
        return False
    except Exception as exc:
        print(f"[winfocus] focus failed: {exc}")
        return False
