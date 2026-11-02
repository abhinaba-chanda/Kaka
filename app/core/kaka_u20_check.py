r"""
core/kaka_u20_check.py - Kaka update 20 installer self-check.

Run by Kaka-Update-20.bat from the app folder:  python -m core.kaka_u20_check
Prints  FEAT|<n>|ready|<text>  or  FEAT|<n>|skipped|<reason>  for 27, 28, 29,
32, then  U20-CHECK DONE. Opens no camera, starts no recording, connects to
no Discord, writes no API key; only a test line in kaka-timing.txt and the
boot-intro sound file in F:\Kaka\Data\Cache.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODS = ["core.kaka_bootintro", "core.kaka_gesture", "core.kaka_u20_setup",
        "actions.boot_intro", "actions.gesture_mode"]
OPTIONAL = {"29": ["core.kaka_discord", "actions.discord_remote"],
            "32": ["core.kaka_ffmpeg", "core.kaka_recorder", "actions.screen_record"]}
SELF = {"27": "core.kaka_bootintro", "28": "core.kaka_gesture",
        "29": "core.kaka_discord", "32": "core.kaka_recorder"}


def line(n, ok, text):
    print(f"FEAT|{n}|{'ready' if ok else 'skipped'}|{str(text).replace(chr(10), ' ')[:200]}", flush=True)


def main() -> int:
    t0 = time.time()
    import importlib
    bad = []
    for m in MODS + [x for v in OPTIONAL.values() for x in v]:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    if bad:
        for b in bad:
            print("LOADFAIL|" + b[:200], flush=True)
        print("U20-CHECK LOAD-FAIL", flush=True)
        return 3
    for n in ("27", "28", "29", "32"):
        try:
            mod = importlib.import_module(SELF[n])
            fn = getattr(mod, "selfcheck", None)
            if fn is None:
                line(n, False, "no self-check")
                continue
            ok, why = fn()
            line(n, bool(ok), why)
        except Exception as e:
            line(n, False, f"error {e}")
    try:
        from core import timing_log as tl
        wr = tl.record("--- Kaka update 20 installed: boot intro, gestures, Discord remote, screen recording ---")
        print("TIMING|" + ("OK " if wr else "NOT WRITABLE ") + str(tl.path()))
    except Exception as e:
        print("TIMING|NOT WRITABLE " + str(e))
    print(f"U20-CHECK DONE in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
