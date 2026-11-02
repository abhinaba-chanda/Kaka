r"""
core/kaka_u19_check.py - Kaka update 19 installer self-check.

Run by Kaka-Update-19.bat from the app folder:  python -m core.kaka_u19_check
Prints  FEAT|<n>|ready|<text>  or  FEAT|<n>|skipped|<reason>  per feature,
NOTES|<hotkey label>, then  U19-CHECK DONE. Changes nothing on the PC except a
test line in F:\Kaka\Data\kaka-timing.txt; writes no API key, takes no
screenshot, touches no clipboard.
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def line(n, ok, text):
    print(f"FEAT|{n}|{'ready' if ok else 'skipped'}|{text}", flush=True)


def main() -> int:
    t0 = time.time()
    import importlib
    mods = ["core.kaka_clipboard", "core.kaka_shot", "core.kaka_timer", "core.kaka_health",
            "core.kaka_notes", "core.kaka_notes_panel",
            "actions.clipboard_history", "actions.screenshot_send", "actions.timer",
            "actions.pc_health", "actions.notes"]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    if bad:
        for b in bad:
            print("LOADFAIL|" + b[:200], flush=True)
        print("U19-CHECK LOAD-FAIL", flush=True)
        return 3
    win = sys.platform == "win32"
    # 15 clipboard
    try:
        from core import kaka_clipboard as C
        ok = C.is_secret("sk-abc123def456ghi789") and not C.is_secret("kal 5 baje milte hain")
        line(15, ok, "last 20 text copies (password/OTP skip)" if ok else "filter self-test failed")
    except Exception as e:
        line(15, False, f"error {e}")
    # 16 screenshot + WhatsApp
    try:
        from core import kaka_shot as K
        has_grab = False
        for mod in ("mss", "PIL.ImageGrab"):
            try:
                importlib.import_module(mod)
                has_grab = True
                break
            except Exception:
                pass
        has_pil = True
        try:
            import PIL.Image  # noqa: F401
        except Exception:
            has_pil = False
        from actions import whatsapp_call as wa
        ready = hasattr(wa, "open_chat") and has_pil
        d = K.shots_dir()
        why = ("screenshots -> " + str(d) + (", WhatsApp send ready" if ready else
               ", WhatsApp send needs Pillow (save works)"))
        if not has_grab:
            why += ", PowerShell grabber (slower)"
        line(16, True, why)
    except Exception as e:
        line(16, False, f"error {e}")
    # 17 timer
    try:
        from core import kaka_timer as T
        ok = T.parse_duration("dedh ghanta") == 5400 and T.parse_duration("2 min 30 sec") == 150
        line(17, ok, "timer / stopwatch / pomodoro (HUD pe time)" if ok else "parse self-test failed")
    except Exception as e:
        line(17, False, f"error {e}")
    # 18 PC health
    try:
        from core import kaka_health as H
        b = H.battery()
        bits = [H.battery_text(b)]
        try:
            import psutil  # noqa: F401
            bits.append("CPU/RAM/apps ok")
        except Exception:
            bits.append("psutil nahi - sirf RAM total/used")
        g = H._nvidia_smi()
        bits.append("GPU temp via nvidia-smi" if g else "GPU temp nahi (nvidia-smi nahi mila)")
        bits.append("low-battery alert on" if b.get("has") else "no battery alert (desktop)")
        line(18, True, ", ".join(bits))
    except Exception as e:
        line(18, False, f"error {e}")
    # 19 notes
    try:
        from core import kaka_notes as N
        n = len(N.items())
        try:
            import PyQt6.QtWidgets  # noqa: F401
            qt = True
        except Exception:
            qt = False
        line(19, qt, f"notes panel ({n} notes saved)" if qt else "PyQt6 nahi mila - notes sirf awaaz se")
    except Exception as e:
        line(19, False, f"error {e}")
    try:
        from core import kaka_notes_panel as P
        lab = P.check_hotkey() if hasattr(P, "check_hotkey") else ""
        print("NOTES|" + (lab or "NONE - sirf HUD button / awaaz"), flush=True)
    except Exception as e:
        print("NOTES|NONE - " + str(e)[:60], flush=True)
    try:
        from core import timing_log as tl
        wr = tl.record("--- Kaka update 19 installed: clipboard, screenshot, timer, PC health, notes ---")
        print("TIMING|" + ("OK " if wr else "NOT WRITABLE ") + str(tl.path()))
    except Exception as e:
        print("TIMING|NOT WRITABLE " + str(e))
    print(f"U19-CHECK DONE in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
