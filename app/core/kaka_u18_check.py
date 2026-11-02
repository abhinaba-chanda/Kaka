r"""
core/kaka_u18_check.py - Kaka update 18 installer self-check.

Run by Kaka-Update-18.bat from the app folder:  python -m core.kaka_u18_check
Prints one line per feature:  FEAT|<n>|ready|<text>  or  FEAT|<n>|skipped|<reason>
then  U18-CHECK DONE. Never changes anything on the PC except a test line in
F:\Kaka\Data\kaka-timing.txt. Never prints or writes any API key.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def line(n, ok, text):
    print(f"FEAT|{n}|{'ready' if ok else 'skipped'}|{text}", flush=True)


def ps(cmd: str, timeout=8) -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=timeout, **_NOWIN)
        return (r.stdout or "").strip()
    except Exception as e:
        return "ERR " + str(e)


def net_ok(url: str, timeout=5) -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Kaka"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    t0 = time.time()
    from core import kaka_services as ks
    import importlib
    mods = ["core.kaka_reminders", "core.kaka_facts", "core.kaka_game", "core.kaka_feeds",
            "core.kaka_files", "core.kaka_pc", "core.kaka_ai", "core.kaka_screen",
            "actions.reminder", "actions.kaka_memory", "actions.game_mode", "actions.kaka_update",
            "actions.weather_report", "actions.youtube_play", "actions.media_control",
            "actions.find_file", "actions.screen_explain", "actions.kaka_translate",
            "actions.vscode_helper"]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    if bad:
        print("LOAD-FAIL " + " ; ".join(bad)[:400])
    key = bool(ks.gemini_key())
    # 2 reminders
    try:
        from core import kaka_reminders as R
        from datetime import datetime
        ok = R.parse_when("kal subah 7 baje", datetime(2026, 1, 1, 12, 0)) == datetime(2026, 1, 2, 7, 0)
        p = ks.data_path("reminders.json")
        wr = os.access(str(p.parent), os.W_OK)
        n = len(R.pending())
        line(2, ok and wr, f"reminders ({p}; {n} pending)" if ok and wr else "data folder not writable")
    except Exception as e:
        line(2, False, f"error {e}")
    # 3 screen
    try:
        have = []
        for m in ("mss", "PIL.ImageGrab"):
            try:
                importlib.import_module(m)
                have.append(m.split(".")[0])
            except Exception:
                pass
        if not key:
            line(3, False, "Gemini key nahi mila (Settings -> API KEYS)")
        else:
            line(3, True, "screen samjhao (Gemini vision; screenshot via " + (", ".join(have) or "PowerShell") + ", saved nowhere)")
    except Exception as e:
        line(3, False, f"error {e}")
    # 4 morning update
    try:
        from core import kaka_feeds as KF
        w = net_ok("https://api.open-meteo.com/v1/forecast?latitude=22.57&longitude=88.36&current=temperature_2m")
        cal = "calendar connect nahi hai (skip)"
        line(4, True, "morning update 8:00 AM (" + ("internet OK" if w else "internet check failed - will try at 8") + "; " + cal + ")")
    except Exception as e:
        line(4, False, f"error {e}")
    # 5 volume / brightness / media
    try:
        pyc = True
        try:
            import pycaw.pycaw  # noqa: F401
            import comtypes  # noqa: F401
        except Exception:
            pyc = False
        br = "?"
        if sys.platform == "win32":
            out = ps("(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness", 10)
            br = ("brightness OK (" + out + "%)") if out.strip().isdigit() else "brightness: ye screen support nahi karti"
        line(5, True, ("volume exact (pycaw)" if pyc else "volume via keys (pycaw missing)") + "; media keys; " + br)
    except Exception as e:
        line(5, False, f"error {e}")
    # 6 file finder
    try:
        from core import kaka_files as KFi
        ws = ""
        if sys.platform == "win32":
            st = ps("(Get-Service WSearch -ErrorAction SilentlyContinue).Status", 6)
            ws = "Windows Search " + (st or "off")
        line(6, True, "file finder (" + (ws or "index cache") + "; index builds in background)")
    except Exception as e:
        line(6, False, f"error {e}")
    # 9 youtube
    try:
        import actions.youtube_play  # noqa: F401
        y = net_ok("https://www.youtube.com/results?search_query=test", 6)
        line(9, True, "YouTube by voice (" + ("youtube reachable" if y else "youtube check failed - will try at use") + ")")
    except Exception as e:
        line(9, False, f"error {e}")
    # 10 memory
    try:
        from core import kaka_facts as F
        line(10, True, f"personal memory ({ks.data_path('memory.json')}; {len(F.load())} saved)")
    except Exception as e:
        line(10, False, f"error {e}")
    # 11 VS Code
    try:
        code = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe")
        tools = [n for n in ("python", "py", "node", "gcc", "g++") if shutil.which(n)]
        ok = os.path.exists(code) or bool(shutil.which("code"))
        line(11, ok, ("VS Code found; run: " + (", ".join(tools) or "no compilers on PATH")
                      + ("" if key else "; error fix needs Gemini key")) if ok else "VS Code nahi mila")
    except Exception as e:
        line(11, False, f"error {e}")
    # 12 game mode
    try:
        from core import kaka_game as G
        plan = "?"
        if sys.platform == "win32":
            rc, out = G._powercfg("/list")
            plan = "High performance plan available" if G.HIGH_PERF in (out or "").lower() or G.ULTIMATE in (out or "").lower() else "no High performance plan (only quiet mode)"
        line(12, True, "game mode auto (" + plan + ")")
    except Exception as e:
        line(12, False, f"error {e}")
    # 13 translate
    line(13, key, "translate (Gemini)" if key else "Gemini key nahi mila")
    # 14 weather + news
    try:
        n = net_ok("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en", 6)
        line(14, True, "weather/news/Barca (" + ("news reachable" if n else "news check failed - will try at use") + ")")
    except Exception as e:
        line(14, False, f"error {e}")
    # timing file + sleep shortcut (unchanged features, checked like before)
    try:
        from core import timing_log as tl
        wr = tl.record("--- Kaka update 18 installed: 11 naye features ---")
        print("TIMING|" + ("OK " if wr else "NOT WRITABLE ") + str(tl.path()))
    except Exception as e:
        print("TIMING|NOT WRITABLE " + str(e))
    try:
        import core.sleep_hotkey as h
        k = h.SleepHotkey(lambda: None)
        lab = k.start()
        k.stop()
        print("SLEEP|" + (lab or "NONE - sirf Sleep button"))
    except Exception as e:
        print("SLEEP|NONE - " + str(e)[:60])
    print("U18-CHECK " + ("DONE" if not bad else "LOAD-FAIL") + f" in {time.time() - t0:.1f}s", flush=True)
    return 0 if not bad else 3


if __name__ == "__main__":
    sys.exit(main())
