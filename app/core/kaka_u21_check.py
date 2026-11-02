r"""
core/kaka_u21_check.py - Kaka update 21 installer self-check.

Run by Kaka-Update-21.bat from the app folder:  python -u -m core.kaka_u21_check
Prints  FEAT|<n>|ready|<text>  or  FEAT|<n>|skipped|<reason>  for 30, 33, 34
(and 201 = the Discord server fix 20.1), then  U21-CHECK DONE. Opens no
camera, connects to no Discord, writes no API key.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODS = ["core.kaka_face", "core.kaka_alarm", "core.kaka_u21_setup", "core.kaka_discord",
        "core.kaka_bootintro", "actions.face_greet", "actions.thief_alarm", "actions.discord_remote"]


def line(n, ok, text):
    print(f"FEAT|{n}|{'ready' if ok else 'skipped'}|{str(text).replace(chr(10), ' ')[:200]}", flush=True)


def main() -> int:
    t0 = time.time()
    import importlib
    bad = []
    for m in MODS:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    if bad:
        for b in bad:
            print("LOADFAIL|" + b[:200], flush=True)
        print("U21-CHECK LOAD-FAIL", flush=True)
        return 3
    from core import kaka_face as F, kaka_alarm as A, kaka_discord as D
    try:
        ok, why = F.selfcheck()
    except Exception as e:
        ok, why = False, f"error {e}"
    line("30", ok, why if ok else why)
    face_ok = ok
    try:
        aok, awhy = A.selfcheck()
    except Exception as e:
        aok, awhy = False, f"error {e}"
    dc = D.lib_installed()
    tok = bool(D.token())
    if not aok:
        line("33", False, awhy)
    else:
        bits = [f"abhi {awhy}"]
        if not face_ok:
            bits.append("chehra pehchan nahi (face model nahi) - har unlock pe photo")
        if not dc:
            bits.append("discord.py nahi - photo sirf F:\\Kaka\\Data\\Alarm mein")
        elif not tok:
            bits.append("Discord token nahi - photo sirf F:\\Kaka\\Data\\Alarm mein")
        line("33", True, ", ".join(bits))
    cv_ok, cv_why = F.cv2_ok()
    if not cv_ok:
        line("34", False, cv_why)
    elif not dc:
        line("34", False, "discord.py nahi hai")
    else:
        line("34", True, "Discord pe likho: webcam photo / camera / photo lo" + ("" if tok else " (pehle token daalo)"))
    try:
        fixed = D.allowed(D.owner_id(), False) and D.strip_mention("<@1> status") == "status"
        line("201", fixed, "server channels mein bhi sirf tumhari ID" if fixed else "purana DM-only code")
    except Exception as e:
        line("201", False, f"error {e}")
    try:
        from core import timing_log as tl
        wr = tl.record("--- Kaka update 21 installed: face greet, chor alarm, Discord webcam photo ---")
        print("TIMING|" + ("OK " if wr else "NOT WRITABLE ") + str(tl.path()))
    except Exception as e:
        print("TIMING|NOT WRITABLE " + str(e))
    print(f"U21-CHECK DONE in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
