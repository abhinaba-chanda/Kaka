"""
core/desktop_driver.py - shared hands for driving desktop apps (Kaka update 8, 9).

ensure_app("spotify") opens the real desktop app if needed (core.winapps: no
browser, no Windows-search typing), waits until its window exists, and brings
it to the front - so the keystrokes that follow land in THAT app. Every step
returns what actually happened, never a guess.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from core import appidentity
from core.lazyimport import LazyModule, installed
try:
    from core import speed
except Exception:          # pragma: no cover
    speed = None

try:
    from core import winapps
except Exception:          # pragma: no cover
    winapps = None
try:
    from core import winfocus
    winfocus.install()
except Exception:          # pragma: no cover
    winfocus = None

_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")
pyautogui = LazyModule("pyautogui")
pyperclip = LazyModule("pyperclip")


def can_drive() -> bool:
    return bool(_PYAUTOGUI)


_MCACHE: dict = {"key": None, "at": -1.0, "val": None}
_MCACHE_TTL = 0.3


def _matches(procs) -> list:
    """update 14: the full process scan (~0.3-1 s on Windows) is remembered for
    0.3 s, so one action's back-to-back checks do not each pay for it."""
    key = tuple(procs or ())
    now = time.monotonic()
    c = _MCACHE
    if c["key"] == key and c["val"] is not None and 0 <= now - c["at"] < _MCACHE_TTL:
        return list(c["val"])
    out = _matches_scan(procs)
    _MCACHE.update(key=key, at=now, val=list(out))
    return out


def _matches_scan(procs) -> list:
    out = []
    for p in procs:
        for m in appidentity.running(p, include_self=False):
            stem = appidentity.fold(m.name.rsplit(".", 1)[0])
            if stem in {appidentity.fold(x) for x in procs} and m not in out:
                out.append(m)
    return out


def titles(procs) -> list:
    """Visible window titles of the app's processes (Spotify shows the song)."""
    by_pid = appidentity._windows_by_pid()
    out = []
    for m in _matches(procs):
        out.extend(by_pid.get(m.pid, []))
    return out


# ------------------------------------------------------------ update 9
def app_windows(procs, title_word: str = "") -> list:
    """[(hwnd, pid, title)] of the app's visible titled windows: windows of its
    processes, or any non-browser window whose title has the app's name."""
    if winfocus is None:
        return []
    want = {appidentity.fold(p) for p in procs}
    wins = winfocus.windows()
    pids = _window_pids(procs, want, wins) if procs else set()
    word = appidentity.fold(title_word) if title_word else ""
    out = []
    for hwnd, pid, title in wins:
        if pid in pids:
            out.append((hwnd, pid, title))
            continue
        if word and word in appidentity.fold(title):
            name = winfocus.proc_name(pid)
            if name and winfocus.is_browser(name):
                continue            # a Brave tab called "Spotify - Web Player"
            if name and appidentity.fold(name.rsplit(".", 1)[0]) not in want and \
                    not winfocus.is_user_app(name):
                continue
            out.append((hwnd, pid, title))
    return out


_OWN_STEMS = {"python", "pythonw", "kaka", "py"}


def _stem_of(name: str) -> str:
    return appidentity.fold(str(name or "").rsplit(".", 1)[0])


def _window_pids(procs, want, wins) -> set:
    """update 14: which window owners ARE the app - by the owner's process name
    (one cheap lookup per window) instead of scanning every process on the PC.
    Falls back to the old full scan when names cannot be read."""
    names = {}
    for _h, pid, _t in wins:
        if pid not in names:
            try:
                names[pid] = winfocus.proc_name(pid) or ""
            except Exception:
                names[pid] = ""
    if names and not any(names.values()):
        return {m.pid for m in _matches(procs)}        # no names (no psutil): old way
    hit = {pid for pid, n in names.items() if n and _stem_of(n) in want}
    if hit and (want & _OWN_STEMS):
        try:                                           # never Kaka's own windows
            hit -= set(appidentity._chain_pids())
        except Exception:
            hit.discard(os.getpid())
    return hit


def is_in_front(procs) -> bool:
    """Is one of the app's windows the foreground window right now?
    update 14: the foreground window's process NAME answers it (instant); the
    full process scan only runs when the name cannot be read."""
    if winfocus is None:
        return False
    _h, pid = winfocus.foreground()
    if not pid:
        return False
    want = {appidentity.fold(p) for p in procs}
    try:
        fname = winfocus.proc_name(pid) or ""
    except Exception:
        fname = ""
    if fname:
        st = _stem_of(fname)
        if st in want and not (st in _OWN_STEMS and pid == os.getpid()):
            return True
        return False
    if pid in {m.pid for m in _matches(procs)}:
        return True
    name = winfocus.proc_name(pid)
    return bool(name) and appidentity.fold(name.rsplit(".", 1)[0]) in \
        {appidentity.fold(p) for p in procs}


# ------------------------------------------------------------ update 12
# His PC (Nov 2, 10:48): "WhatsApp aage nahi aaya" - Windows refused every
# SetForegroundWindow trick for WhatsApp, while Update 7 (which opened WhatsApp
# through the Start menu: Win, type, Enter) always got it in front. Windows
# lets the SHELL give an app the foreground. So when the normal focus fails,
# focus_app asks the shell, in this order, checking after each that the app is
# really in front: (1) the app's own link (whatsapp:, spotify:, discord:),
# (2) explorer shell:AppsFolder\<Store id>, (3) the Start menu - Win, the
# app's name, Enter (exactly Update 7). The activity log says which one worked.
LAST_FOCUS: dict = {"how": "", "tried": []}
_LOG: dict = {"fn": None}
_SHELL_PROCS = {"startmenuexperiencehost", "searchapp", "searchui", "searchhost",
                "shellexperiencehost", "explorer"}
_NO_BARE_LINK = {"tg"}            # "tg:" alone is not a link Telegram opens


def set_log(fn) -> None:
    """Where focus_app writes what it tried (the activity log)."""
    _LOG["fn"] = fn


def _flog(text: str) -> None:
    print(f"[desktop] {text}")
    try:                                       # update 16: kaka-timing.txt
        from core import timing_log as _tl
        _tl.maybe(text)
    except Exception:
        pass
    fn = _LOG.get("fn")
    if fn:
        try:
            fn(text)
        except Exception:
            pass


def _wait_front(procs, secs: float) -> bool:
    """update 14: checked every 0.1 s (was 0.25 s; is_in_front is instant now),
    returns the moment the app is in front; `secs` stays the ceiling."""
    end = time.monotonic() + secs
    while True:
        if is_in_front(procs):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.1)


# update 14: the way that got each app in front last time is tried FIRST next
# time (on his PC Windows refuses SetForegroundWindow for WhatsApp every time,
# so 3 direct tries (~1.5-2 s) were wasted before the whatsapp: link worked).
_PREFER: dict = {}
_PREFER_STATE: dict = {"loaded": False}


def _load_prefer() -> None:
    """update 17: the remembered ways survive a Kaka restart (F:\\Kaka\\Data cache)."""
    if _PREFER_STATE["loaded"]:
        return
    _PREFER_STATE["loaded"] = True
    try:
        from core import speed as _sp
        obj = _sp.load("focus_ways", 30 * 86400)
        for k, v in ((obj or {}).get("data") or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k not in _PREFER:
                _PREFER[k] = v
    except Exception:
        pass


def _remember(key: str, how: str) -> None:
    if _PREFER.get(key) == how:
        return
    _PREFER[key] = how
    try:
        from core import speed as _sp
        _sp.save("focus_ways", dict(_PREFER))
    except Exception:
        pass


def _app_key(procs, title_word: str = "") -> str:
    return appidentity.fold(title_word or (procs[0] if procs else ""))


def _shell_is_front() -> bool:
    _h, pid = winfocus.foreground()
    name = appidentity.fold((winfocus.proc_name(pid) or "").rsplit(".", 1)[0]) if pid else ""
    return name in _SHELL_PROCS


def _front_desc() -> str:
    if winfocus is None:
        return "?"
    h, pid = winfocus.foreground()
    if not h:
        return "koi nahi"
    name = winfocus.proc_name(pid) or f"pid {pid}"
    title = ""
    for hw, _p, t in winfocus.windows(pids={pid}):
        if hw == h:
            title = t
            break
    return f"{name} '{title[:40]}'"


def _start_menu_open(title: str) -> bool:
    """Update 7's way: Win, type the name, Enter. Typed ONLY when the Start
    menu really came up (else the letters would land in another window)."""
    pyautogui.press("win")
    # update 14: go on the moment the Start menu is in front (ceiling 0.7 s as before)
    t_end = time.monotonic() + 0.7
    while not _shell_is_front() and time.monotonic() < t_end:
        time.sleep(0.05)
    _h, pid = winfocus.foreground()
    name = appidentity.fold((winfocus.proc_name(pid) or "").rsplit(".", 1)[0]) if pid else ""
    if name not in _SHELL_PROCS:
        _flog(f"Kaka: Start menu nahi khula (aage: {name or '?'}) - Start se try nahi kiya")
        return False
    paste(title, restore=False)
    time.sleep(0.8)
    pyautogui.press("enter")
    return True


def shell_activate(procs, title_word: str = "") -> str:
    """-> how it got in front ('' = nothing worked). Each way is checked with
    GetForegroundWindow before the next is tried."""
    tried = []
    app = None
    if winapps is not None:
        try:
            app = winapps.known_app(title_word or (procs[0] if procs else ""))
        except Exception:
            app = None
    title = app.title if app else (title_word or (procs[0] if procs else ""))
    try:
        winfocus.mark_user_app(*(list(procs) + [title]))
    except Exception:
        pass
    ways = []
    proto = getattr(app, "proto", "") if app else ""
    if proto and proto not in _NO_BARE_LINK:
        try:
            reg = winapps.protocol_registered(proto)
        except Exception:
            reg = False
        if reg:
            ways.append((f"{proto}: link", lambda p=proto: os.startfile(p + ":")))  # noqa: S606

    def _store(aid):
        # update 14: the Store id is looked up only when this way is reached
        # (the lookup can start PowerShell - seconds - and the link usually works)
        try:
            real = winapps.appid_installed(aid)
        except Exception:
            real = ""
        if not real:
            return False
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + real])
        return True
    for aid in (getattr(app, "appids", ()) or ()) if app else ():
        ways.append(("shell:AppsFolder (Store app)", lambda a=aid: _store(a)))
    if title and _PYAUTOGUI:
        ways.append(("Start menu (Win + naam + Enter)", lambda t=title: _start_menu_open(t)))
    pref = _PREFER.get(_app_key(procs, title_word))
    if pref:
        ways.sort(key=lambda w: 0 if w[0] == pref else 1)
    for how, fn in ways:
        try:
            r = fn()
        except Exception as exc:
            tried.append(f"{how}: {exc}")
            continue
        if r is False:
            tried.append(f"{how}: nahi chala")
            continue
        if _wait_front(procs, 4.0):
            LAST_FOCUS.update(how=how, tried=tried)
            _remember(_app_key(procs, title_word), how)
            _flog(f"Kaka: {title} aage aaya - {how} se"
                  + (f" (pehle fail: {'; '.join(tried)})" if tried else ""))
            return how
        tried.append(f"{how}: aage nahi aaya (aage: {_front_desc()})")
    LAST_FOCUS.update(how="", tried=tried)
    _flog(f"Kaka: {title} ko aage laane ke sab tareeke fail - " + ("; ".join(tried) or "koi tareeka nahi"))
    return ""


def focus_app(procs, title_word: str = "", shell: bool = True) -> bool:
    """Bring the app to the front and CHECK it is in front. Update 12: when
    Windows refuses, ask the shell (link / Store id / Start menu)."""
    if winfocus is not None and winfocus._IS_WIN:
        t0 = time.monotonic()
        # update 14: already in front -> nothing to do (was: a window search and
        # the whole SetForegroundWindow chain every time)
        try:
            front = is_in_front(procs)
        except Exception:
            front = False
        if front:
            LAST_FOCUS.update(how="already in front", tried=[])
            return True
        wins = app_windows(procs, title_word)
        key = _app_key(procs, title_word)
        _load_prefer()
        learned = bool(shell) and _PREFER.get(key, "") not in ("", "SetForegroundWindow")
        if learned and wins:
            # update 17: Windows refused SetForegroundWindow for this app before
            # (his WhatsApp: every time) - the remembered way goes FIRST, with no
            # failed tries in front of it (saved across Kaka restarts)
            ok = bool(shell_activate(procs, title_word))
            _flog(f"[time] foreground {title_word or key}: {time.monotonic() - t0:.1f}s "
                  f"({LAST_FOCUS.get('how') or 'fail'}, yaad wala tareeka)")
            if ok:
                return True
            wins = app_windows(procs, title_word)
            for hwnd, _pid, _t in wins:            # the remembered way failed: the old chain
                try:
                    ok = winfocus.force_foreground(hwnd)
                except Exception:
                    ok = False
                if ok:
                    LAST_FOCUS.update(how="SetForegroundWindow", tried=[])
                    _remember(key, "SetForegroundWindow")
                    return True
            return False
        for hwnd, _pid, _t in wins:
            try:       # update 14: Windows refused last time -> one quick try only
                ok = winfocus.force_foreground(hwnd, tries=1) if learned else \
                    winfocus.force_foreground(hwnd)
            except TypeError:
                ok = winfocus.force_foreground(hwnd)
            if ok:
                LAST_FOCUS.update(how="SetForegroundWindow", tried=[])
                _remember(key, "SetForegroundWindow")
                return True
        if not shell:
            return False
        name = title_word or (procs[0] if procs else "?")
        _flog(f"Kaka: {name} - Windows ne seedha aage nahi aane diya "
              f"({len(wins)} window, aage: {_front_desc()}) - shell se try")
        ok = bool(shell_activate(procs, title_word))
        _flog(f"[time] foreground {title_word or name}: {time.monotonic() - t0:.1f}s "
              f"({LAST_FOCUS.get('how') or 'fail'})")
        return ok
    for m in _matches(procs):
        try:
            if appidentity.focus(m):
                return True
        except Exception:
            continue
    return False


def _say_log(log, text: str) -> None:
    print(f"[desktop] {text}")
    if log:
        try:
            log(text)
        except Exception:
            pass


def ensure_app(spoken: str, wait: float = 40.0, log=None, uri: str = "",
               need_front: bool = True) -> tuple:
    """-> (ok, procs, message). Opens the desktop app (trying every way it is
    installed), waits up to `wait` s for its window, and brings it to the front.

    ok is True only when the window exists AND (need_front) it is verified in
    front - so callers never type keys into the wrong window. When the window
    exists but Windows would not let Kaka bring it forward, the message says
    exactly that, never "did not appear"."""
    if winapps is None:
        return False, (), "app finder unavailable"
    app = winapps.known_app(spoken)
    procs = tuple(app.procs) if app else ()
    title = app.title if app else spoken
    word = title if app else ""
    running = bool(procs) and bool(app_windows(procs, word))
    if running and not uri:
        if not need_front:
            return True, procs, "already open"
        if focus_app(procs, word):
            return True, procs, "already open"
        # update 12: focus_app already tried the link / Store id / Start menu
        return False, procs, (f"{title} khula hai par Windows use aage nahi aane de raha "
                              f"(link, Store app, Start menu - sab try kiya). Ek baar uski "
                              f"window pe click kar do, phir dobara bolo")
    plan = winapps.launch_plan(spoken, uri=uri) if hasattr(winapps, "launch_plan") else \
        [t for t in [winapps.resolve(spoken)] if t]
    if not plan:
        if running:
            return False, procs, (f"{title} khula hai par main use aage (front) nahi la paya - "
                                  f"Windows ne rok diya")
        return False, procs, f"{title} is not installed on this PC"
    procs = procs or tuple(plan[0].procs)
    tried = []
    started = time.monotonic()
    deadline = started + max(5.0, wait)
    seen_window = False
    for i, t in enumerate(plan):
        last = i == len(plan) - 1
        if not winapps.launch(t):
            err = (getattr(winapps, "LAST_LAUNCH", {}) or {}).get("error") or "start refused"
            tried.append(f"{t.how}: {err}")
            _say_log(log, f"Kaka: {title} - {t.how} se start nahi hua ({err})")
            continue
        _say_log(log, f"Kaka: {title} khol raha hoon ({t.how})...")
        # this way gets up to 12 s to show a window (the last way: all the rest)
        try_until = deadline if last else min(deadline, time.monotonic() + 12.0)
        next_note = time.monotonic() + 10.0
        while time.monotonic() < try_until:
            time.sleep(0.2)          # update 14: was 0.7 s; window seen sooner
            if app_windows(procs, word):
                seen_window = True
                break
            if _matches(procs) and not last:
                try_until = deadline      # it is starting - give it the full time
            if time.monotonic() >= next_note:
                _say_log(log, f"Kaka: {title} ka wait... {int(time.monotonic() - started)}s")
                next_note += 10.0
        if seen_window:
            break
        tried.append(f"{t.how}: no window")
        if time.monotonic() >= deadline:
            break
    if not seen_window:
        why = "; ".join(tried) or "nothing to start"
        _say_log(log, f"Kaka: {title} ki window {int(wait)}s mein nahi dikhi ({why})")
        return False, procs, f"{title} was started but its window did not appear in {int(wait)}s ({why})"
    time.sleep(1.0)                       # let the window finish drawing (cold start only)
    for i in range(3):
        if is_in_front(procs) or focus_app(procs, word, shell=(i == 2 and need_front)):
            return True, procs, "opened"
        if i < 2 and _wait_front(procs, 0.8):   # update 14: poll instead of a fixed 0.8 s
            return True, procs, "opened"
    if not need_front:
        return True, procs, "opened (not in front)"
    _say_log(log, f"Kaka: {title} khul gaya, par Windows ne use aage nahi aane diya")
    return False, procs, (f"{title} khul gaya hai, par main use aage (front) nahi la paya - "
                          f"Windows ne rok diya. Ek baar uski window pe click kar do, phir dobara bolo")


def paste(text: str, restore: bool = True) -> None:
    """Type any text (Hindi too) by pasting it - pyautogui.write is ASCII-only.
    Update 12: restore=False leaves the text on the clipboard, exactly like
    Update 7 (WhatsApp reads the clipboard a moment AFTER Ctrl+V; putting his
    old clipboard back 0.35 s later could make it paste the old one - or
    nothing). When restoring, Kaka now waits 1.5 s first."""
    if _PYPERCLIP:
        old = None
        if restore:
            try:
                old = pyperclip.paste()
            except Exception:
                old = None
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        if old is not None:
            def _back(o=old, t=text):
                time.sleep(1.5)
                try:
                    if pyperclip.paste() == t:
                        pyperclip.copy(o)
                except Exception:
                    pass
            import threading
            threading.Thread(target=_back, daemon=True, name="clip-restore").start()
    else:
        pyautogui.write(text, interval=0.03)


def write(text: str) -> None:
    """update 12: type the letters one by one (fallback when a paste left
    nothing) - ASCII names only."""
    pyautogui.write(text, interval=0.03)


def click(x: int, y: int) -> None:
    """One left click at a screen point (update 10: the chat message box)."""
    pyautogui.click(int(x), int(y))


def hotkey(*keys) -> None:
    pyautogui.hotkey(*keys)


def press(key: str, times: int = 1) -> None:
    for _ in range(max(1, times)):
        pyautogui.press(key)
        time.sleep(0.08)


# Global media keys - work for Spotify, YouTube in a browser, any player.
_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
       "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def media_key(name: str) -> bool:
    vk = _VK.get(name)
    if vk is None:
        return False
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, 2, 0)    # KEYEVENTF_KEYUP
        return True
    except Exception:
        try:
            pyautogui.press({"play_pause": "playpause", "next": "nexttrack",
                             "previous": "prevtrack", "stop": "stop",
                             "volume_up": "volumeup", "volume_down": "volumedown",
                             "mute": "volumemute"}[name])
            return True
        except Exception:
            return False
