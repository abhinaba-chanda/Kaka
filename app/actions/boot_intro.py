r"""
actions/boot_intro.py - Kaka update 20: "boot intro band karo / chalu karo",
"boot intro dikhao" (plays it now). Logic: core/kaka_bootintro.py.
"""


def boot_intro(parameters: dict = None, player=None) -> str:
    try:
        from core import kaka_bootintro as B
    except Exception as e:
        return f"Boot intro load nahi hua: {e}"
    a = str((parameters or {}).get("action") or "status").lower()
    try:
        if a == "off":
            return ("Theek hai, PC on hone pe wala Iron Man intro band." if B.set_enabled(False)
                    else "Boot intro band karna save nahi hua (Data folder mein likh nahi saka).")
        if a == "on":
            return ("Boot intro chalu: agli baar PC on hone pe Kaka Jarvis style mein start hoga." if B.set_enabled(True)
                    else "Boot intro chalu karna save nahi hua (Data folder mein likh nahi saka).")
        if a == "play":
            import threading
            from datetime import datetime
            def _go():
                B.play_sound()
                B.show_animation()
            threading.Thread(target=_go, daemon=True, name="kaka-u20-bootintro-demo").start()
            return "Boot intro chala diya (HUD dekho). " + B.greeting(datetime.now())
        return ("Boot intro chalu hai - PC on hone ke baad pehli baar Kaka khulne pe chalega." if B.is_enabled()
                else "Boot intro band hai.")
    except Exception as e:
        return f"Boot intro mein dikkat: {e}"


TOOL = {
    "name": "boot_intro",
    "description": ("Iron Man style boot intro (PC start hone pe animation + sound + 'Systems online' "
                    "+ status). 'boot intro band karo' -> off; 'chalu karo' -> on; "
                    "'boot intro dikhao/chalao' -> play; 'boot intro on hai?' -> status. No question."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["on", "off", "play", "status"]}},
        "required": ["action"]},
    "handler": boot_intro,
}
