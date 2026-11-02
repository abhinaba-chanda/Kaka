r"""
actions/thief_alarm.py - Kaka update 21 (feature 33): "chor alarm on / off /
on hai?". Logic: core/kaka_alarm.py.
"""
try:                                     # stdlib-only import; registers the starter
    from core import kaka_alarm as _A
except Exception:                        # never block tool discovery
    _A = None


def thief_alarm(parameters: dict = None, player=None) -> str:
    try:
        from core import kaka_alarm as A
    except Exception as e:
        return f"Chor alarm load nahi hua: {e}"
    a = str((parameters or {}).get("action") or "status").lower().strip()
    try:
        if a == "on":
            return A.set_alarm(True)
        if a == "off":
            return A.set_alarm(False)
        return A.status()
    except Exception as e:
        return f"Chor alarm mein dikkat: {e}"


TOOL = {
    "name": "thief_alarm",
    "description": ("Chor alarm: PC unlock / sleep se jagne / login pe webcam photo; chehra uska na ho toh "
                    "photo + time uske Discord pe. 'chor alarm on' -> on, 'chor alarm off/band' -> off, "
                    "'chor alarm on hai?' -> status. No question. Say the result as-is."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["on", "off", "status"]}},
        "required": ["action"]},
    "handler": thief_alarm,
}
