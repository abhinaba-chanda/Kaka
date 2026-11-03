"""
actions/kaka_pcdoctor.py - KAKA v1.1: "PC check karo", "PC slow kyun hai?",
"junk saaf karo". Check-up: app/kaka_doctor.py. Junk: app/kaka_junk.py
(measures first, ASKS, deletes only after his haan - his rule).
"""
import threading
import time


def pc_doctor(parameters=None, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "checkup").strip().lower()
    try:
        from core import kaka_services as ks
    except Exception:
        ks = None
    t0 = time.monotonic()
    if action in ("checkup", "check", "slow", "report"):
        try:
            import kaka_doctor as Dr
            r = Dr.checkup()
        except Exception as e:
            return f"PC check nahi ho paya: {e}"
        if ks:
            ks.popup("PC doctor", r["detail"])
            ks.timing("pc doctor", time.monotonic() - t0)
        return ("Real check-up result (full detail is on the HUD). Explain it to him in "
                "detail, in his language, 4-8 sentences - every problem, why it slows the PC, "
                "and each tip: " + r["text"])
    if action in ("junk_scan", "scan", "junk"):
        try:
            import kaka_junk as J
            res = J.scan()
            say, detail = J.scan_text(res)
        except Exception as e:
            return f"Junk check nahi ho paya: {e}"
        if ks:
            ks.popup("Junk files", detail)
            ks.timing("junk scan", time.monotonic() - t0)
        if res["total"] < 5 * 1024 ** 2:
            return say + ". Itna kam hai ki saaf karne ki zaroorat nahi. Ek line mein bata do."
        return (f"{say}. [ASK_ONCE] Pehle ye sizes batao, phir EK baar poocho: "
                f"\"Ye {J.gb(res['total'])} junk saaf kar doon? Python, Kaka aur Git folders "
                f"safe rahenge.\" Sirf haan pe action=junk_clean aur go_ahead=true. "
                f"Nahi pe kuch mat karo.")
    if action in ("junk_clean", "clean"):
        try:
            import kaka_junk as J
        except Exception as e:
            return f"Junk cleaner load nahi hua: {e}"
        res = J.last_scan()
        if res is None:
            return ("Pehle junk check karna padega. Call action=junk_scan first, tell him the "
                    "sizes and ask once.")
        if not p.get("go_ahead"):
            return (f"[ASK_ONCE] Ek baar poocho: \"{J.gb(res['total'])} junk saaf kar doon?\" "
                    "Haan pe action=junk_clean, go_ahead=true.")

        def _work():
            try:
                out = J.clean(res)
                msg = (f"Junk saaf: {J.gb(out['freed'])} khaali hua ({out['removed']} files"
                       + (f", {out['failed']} files use mein thi isliye chhod di" if out["failed"] else "")
                       + (", Recycle Bin khaali" if out.get("recycle_emptied") else "") + ").")
            except Exception as e:
                msg = f"Junk saaf karne mein dikkat: {e}"
            if ks:
                ks.popup("Junk saaf", msg)
                ks.say(msg)
        threading.Thread(target=_work, name="kaka-junk-clean", daemon=True).start()
        return "Saaf karna shuru kar diya - ho jaane pe khud bataunga. Say 2-4 words only."
    return "pc_doctor: checkup, junk_scan ya junk_clean."


TOOL = {
    "name": "pc_doctor",
    "description": (
        "Full PC check-up and junk cleaner. 'PC slow kyun hai', 'PC check karo', 'full checkup', "
        "'PC ka health dekho' -> action=checkup (CPU/RAM/disks/temps/heavy apps/uptime/startup "
        "apps + tips; explain in detail). 'junk saaf karo', 'temp files delete karo', 'PC "
        "clean karo' -> action=junk_scan FIRST (only measures), tell him the sizes and ask once; "
        "ONLY after his haan -> action=junk_clean with go_ahead=true. Never clean without asking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["checkup", "junk_scan", "junk_clean"]},
            "go_ahead": {"type": "BOOLEAN", "description": "true only after he said haan to cleaning"},
        },
        "required": ["action"],
    },
    "handler": pc_doctor,
}
