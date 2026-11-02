r"""
core/kaka_game.py - Kaka update 18: GAME MODE.

When a game is in front (known game exe / a game-store folder / any
full-screen app that is not a browser, video player or Kaka itself):
  - Kaka goes quiet: mic paused (like the HUD mic button), no check-ins or
    system alerts, reminders wait until the game closes (alarms don't wait)
  - Windows power plan -> High performance (powercfg, no admin), if it exists
  - nothing is closed
When the game process exits: power plan, mic and the waiting reminders come
back. Voice: "game mode on" / "game mode off". State: F:\Kaka\Data\game_mode.json
Nothing here raises.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Optional

from core import kaka_services as ks

TICK_S = 3.0
HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"

GAME_EXES = {
    "valorant-win64-shipping.exe", "valorant.exe", "cs2.exe", "csgo.exe", "gta5.exe",
    "gta5_enhanced.exe", "rdr2.exe", "fortniteclient-win64-shipping.exe", "rocketleague.exe",
    "robloxplayerbeta.exe", "league of legends.exe", "dota2.exe", "tslgame.exe",
    "eldenring.exe", "cyberpunk2077.exe", "fc24.exe", "fc25.exe", "fc26.exe", "fifa23.exe",
    "bf2042.exe", "cod.exe", "modernwarfare.exe", "r5apex.exe", "r5apex_dx12.exe",
    "overwatch.exe", "witcher3.exe", "forzahorizon5.exe", "forzahorizon4.exe",
    "minecraft.windows.exe", "javaw.exe#minecraft", "genshinimpact.exe", "starrail.exe",
    "zenlesszonezero.exe", "hogwartslegacy.exe", "pubg.exe", "freefire.exe", "hd-player.exe",
    "bgmi.exe", "rainbowsix.exe", "rainbowsix_vulkan.exe", "destiny2.exe", "warframe.x64.exe",
    "palworld-win64-shipping.exe", "helldivers2.exe", "marvel-win64-shipping.exe",
    "deltaforceclient-win64-shipping.exe", "wutheringwaves.exe", "client-win64-shipping.exe",
    "asphalt9_steam_x64_rtl.exe", "need for speed heat.exe", "nfs21.exe", "eafc25.exe",
}
GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\xboxgames\\",
             "\\ea games\\", "\\ubisoft game launcher\\games\\", "\\gog galaxy\\games\\",
             "\\rockstar games\\", "\\battle.net\\", "\\games\\")
NOT_GAMES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "vlc.exe", "mpc-hc64.exe", "mpc-be64.exe", "potplayermini64.exe", "wmplayer.exe",
    "video.ui.exe", "microsoft.media.player.exe", "spotify.exe", "explorer.exe",
    "powerpnt.exe", "code.exe", "kaka.exe", "python.exe", "pythonw.exe", "applicationframehost.exe",
    "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe", "lockapp.exe",
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "whatsapp.exe", "discord.exe",
    "obs64.exe", "textinputhost.exe", "taskmgr.exe", "zoom.exe", "teams.exe", "ms-teams.exe",
}
_STATE = {"on": False, "pid": None, "exe": "", "manual": None, "suppress_pid": None,
          "plan_before": None, "muted_by_us": False, "started": False}
_LOCK = threading.RLock()


# ------------------------------------------------------------------ OS probes (mockable)
def _foreground() -> Optional[dict]:
    """{'hwnd','pid','exe','path','title','fullscreen'} of the foreground window."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = ""
        h = k32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                path = buf.value
            k32.CloseHandle(h)
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, title, 512)
        r = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        mon = u32.MonitorFromWindow(hwnd, 2)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        full = False
        if mon and u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            m = mi.rcMonitor
            full = (r.left <= m.left and r.top <= m.top and r.right >= m.right
                    and r.bottom >= m.bottom)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            full = False
        return {"hwnd": hwnd, "pid": pid.value, "exe": os.path.basename(path).lower(),
                "path": path.lower(), "title": title.value, "fullscreen": full,
                "cls": cls.value}
    except Exception:
        return None


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        pass
    if sys.platform != "win32":
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259     # STILL_ACTIVE
    except Exception:
        return False


def _powercfg(*args, timeout=6) -> tuple:
    try:
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        r = subprocess.run(["powercfg", *args], capture_output=True, text=True,
                           timeout=timeout, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


# ------------------------------------------------------------------ logic
def is_game(fg: Optional[dict]) -> bool:
    if not fg:
        return False
    exe = fg.get("exe", "")
    path = fg.get("path", "")
    if not exe or exe in NOT_GAMES:
        return False
    if "kaka" in (fg.get("title") or "").lower() and exe in ("python.exe", "pythonw.exe", "kaka.exe"):
        return False
    if exe in GAME_EXES:
        return True
    if exe == "javaw.exe" and "minecraft" in (fg.get("title") or "").lower():
        return True
    if any(d in path for d in GAME_DIRS) and "launcher" not in exe:
        return True
    # any other full-screen app counts, except Windows' own and Store apps
    # (Photos, Netflix, Movies & TV live in \windowsapps\)
    return (bool(fg.get("fullscreen")) and "\\windows\\" not in path
            and "\\windowsapps\\" not in path)


def _guid(text: str) -> Optional[str]:
    import re
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", text or "", re.I)
    return m.group(1).lower() if m else None


def _boost_power() -> str:
    rc, out = _powercfg("/getactivescheme")
    cur = _guid(out) if rc == 0 else None
    if not cur:
        return "power plan padh nahi paya"
    if cur in (HIGH_PERF, ULTIMATE):
        return "power plan pehle se High performance"
    rc, lst = _powercfg("/list")
    target = HIGH_PERF if HIGH_PERF in (lst or "").lower() else (ULTIMATE if ULTIMATE in (lst or "").lower() else None)
    if not target:
        return "High performance plan is PC pe nahi hai (skip)"
    rc, out = _powercfg("/setactive", target)
    if rc != 0:
        return "power plan badal nahi paya (" + (out or "").strip()[:60] + ")"
    _STATE["plan_before"] = cur
    return "power plan High performance"


def _restore_power() -> str:
    before = _STATE.get("plan_before")
    if not before:
        return ""
    rc, out = _powercfg("/setactive", before)
    _STATE["plan_before"] = None
    return "power plan wapas" if rc == 0 else "power plan wapas nahi hua"


def enter(exe: str = "", pid=None, manual: bool = False) -> str:
    with _LOCK:
        if _STATE["on"]:
            return "Game mode pehle se on hai."
        t0 = time.monotonic()
        _STATE.update(on=True, pid=pid, exe=exe)
        ks.set_game_mode(True, exe)
        muted_before = ks.mic_muted()
        _STATE["muted_by_us"] = False
        if not muted_before and ks.set_mic_muted(True):
            _STATE["muted_by_us"] = True
        power = _boost_power()
        ks.log(f"SYS: Game mode ON ({exe or 'manual'}) - mic band, Kaka chup; {power}. "
               "Baat karni ho toh HUD ka mic button dabao.")
        ks.timing("game mode on", time.monotonic() - t0, exe or "manual")
        return f"Game mode on: mic band, reminders game ke baad; {power}."


def leave(reason: str = "game band") -> str:
    with _LOCK:
        if not _STATE["on"]:
            return "Game mode pehle se off hai."
        t0 = time.monotonic()
        power = _restore_power()
        if _STATE.get("muted_by_us"):
            ks.set_mic_muted(False)
        _STATE.update(on=False, pid=None, exe="", muted_by_us=False)
        ks.log(f"SYS: Game mode OFF ({reason}) - mic wapas on" + (f", {power}" if power else ""))
        ks.timing("game mode off", time.monotonic() - t0, reason)
        ks.set_game_mode(False)          # flushes the waiting reminders
        return "Game mode off" + (f", {power}" if power else "") + "."


def tick() -> None:
    fg = _foreground()
    with _LOCK:
        if _STATE["on"]:
            if _STATE["manual"]:
                return
            pid = _STATE.get("pid")
            if pid and not _pid_alive(pid):
                leave("game band hua")
            return
        if _STATE["manual"] is False:
            return
        if fg and is_game(fg):
            if _STATE.get("suppress_pid") and fg.get("pid") == _STATE["suppress_pid"]:
                return
            known = fg.get("exe", "") in GAME_EXES or any(d in fg.get("path", "") for d in GAME_DIRS)
            # a plain full-screen app must stay full-screen for 2 checks (~6 s)
            if not known and _STATE.get("seen_pid") != fg.get("pid"):
                _STATE["seen_pid"] = fg.get("pid")
                return
            enter(fg.get("exe", ""), fg.get("pid"))
        else:
            _STATE["seen_pid"] = None


def manual(on: bool) -> str:
    with _LOCK:
        if on:
            _STATE["manual"] = True
            _STATE["suppress_pid"] = None
            return enter("manual", None, manual=True)
        pid = _STATE.get("pid")
        _STATE["manual"] = None
        _STATE["suppress_pid"] = pid          # don't re-enter for this same game
        return leave("tumne bola off")


def set_auto(on: bool) -> str:
    st = ks.read_json(ks.data_path("game_mode.json"), {})
    st["auto"] = bool(on)
    ks.write_json(ks.data_path("game_mode.json"), st)
    _STATE["manual"] = None if on else False
    return "Game aate hi game mode apne aap lagega." if on else "Ab game mode apne aap nahi lagega (sirf 'game mode on' bolne pe)."


def status() -> str:
    if _STATE["on"]:
        return f"Game mode ON ({_STATE.get('exe') or 'manual'})."
    return "Game mode off" + (" (auto band)" if _STATE["manual"] is False else " (game aate hi apne aap lagega)") + "."


def _install_quiet_hooks(j) -> None:
    """Check-ins and system alerts stay silent in game mode (wrap, don't edit main.py)."""
    try:
        pro = getattr(j, "_proactive", None)
        if pro is not None and not getattr(pro, "_u18_wrapped", False):
            orig = pro.should_trigger
            pro.should_trigger = lambda last, _o=orig: (False if ks.in_game_mode() else _o(last))
            pro._u18_wrapped = True
    except Exception:
        pass
    try:
        mon = getattr(j, "_sys_monitor", None)
        if mon is not None and not getattr(mon, "_u18_wrapped", False):
            orig = mon.check
            mon.check = lambda _o=orig: (None if ks.in_game_mode() else _o())
            mon._u18_wrapped = True
    except Exception:
        pass


def _loop():
    while True:
        try:
            tick()
        except Exception as e:
            ks._print(f"[game] tick failed: {e}")
        time.sleep(TICK_S)


def start() -> None:
    if _STATE["started"]:
        return
    _STATE["started"] = True
    st = ks.read_json(ks.data_path("game_mode.json"), {})
    if st.get("auto") is False:
        _STATE["manual"] = False
    j = ks.jarvis()
    if j is not None:
        _install_quiet_hooks(j)
    if sys.platform == "win32":
        threading.Thread(target=_loop, name="kaka-u18-game", daemon=True).start()


ks.on_start(start)
