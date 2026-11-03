"""
actions/kaka_selfupdate_tool.py - KAKA v1.1: "Kaka update check karo",
"update kar do". Logic: app/kaka_selfupdate.py. Installs ONLY after his yes.
"""
import threading


def kaka_selfupdate(parameters=None, player=None) -> str:
    try:
        import kaka_selfupdate as U
    except Exception as e:
        return f"Updater load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "check").strip().lower()
    if action in ("on", "off"):
        try:
            import kaka_user
            kaka_user.save_setting("auto_update_enabled", action == "on")
        except Exception as e:
            return f"Setting save nahi hui: {e}"
        return ("Auto-update chalu: din mein ek baar check karunga, update se pehle poochunga."
                if action == "on" else "Auto-update band kar diya. 'update check karo' bolo jab chaho.")
    if action == "apply":
        if not p.get("go_ahead"):
            return ("[ASK_ONCE] Ek baar poocho: \"Naya version aaya hai, update karu?\" "
                    "Haan pe action=apply, go_ahead=true.")
        threading.Thread(target=U.apply_and_restart, name="kaka-selfupdate", daemon=True).start()
        return ("Update shuru: download, sha256 check, backup, phir Kaka restart hoga. "
                "Tell him that in one line.")
    res = U.check(force=True)
    st = res["status"]
    if st == "newer":
        try:
            U.ask_banner(res["remote"])
        except Exception:
            pass
        return (f"GitHub pe naya version v{res['remote']} hai (abhi v{res['local']}). "
                "[ASK_ONCE] Ek baar poocho: \"Naya version aaya hai, update karu?\" Haan pe "
                "action=apply, go_ahead=true (ya woh HUD pe CONFIRM dabaye).")
    if st == "current":
        return f"Kaka latest hai (v{res['local']}). One line."
    return "GitHub tak nahi pahunch paya (internet?), baad mein try karo. One line."


TOOL = {
    "name": "kaka_selfupdate",
    "description": (
        "Kaka's own app update from GitHub. 'Kaka update check karo' / 'naya version hai?' -> "
        "action=check. After he says haan to updating -> action=apply, go_ahead=true (never "
        "without his yes). 'auto update band karo / chalu karo' -> off / on. This is NOT "
        "kaka_update (news / morning update)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["check", "apply", "on", "off"]},
            "go_ahead": {"type": "BOOLEAN", "description": "true only after his haan"},
        },
        "required": [],
    },
    "handler": kaka_selfupdate,
}
