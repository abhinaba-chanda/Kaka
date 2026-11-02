r"""
actions/gesture_mode.py - Kaka update 20: "gesture mode on/off" (webcam hand
gestures). Logic: core/kaka_gesture.py (imported here so its HUD button is
registered; the camera is never touched until he switches it on).
"""
try:                                     # stdlib-only import; registers the HUD button starter
    from core import kaka_gesture as _G
except Exception:                        # never block tool discovery
    _G = None


def gesture_mode(parameters: dict = None, player=None) -> str:
    G = _G
    try:
        if G is None:
            from core import kaka_gesture as G
    except Exception as e:
        return f"Gesture mode load nahi hua: {e}"
    a = str((parameters or {}).get("action") or "status").lower()
    try:
        if a == "on":
            return G.start()
        if a == "off":
            return G.stop()
        return G.status()
    except Exception as e:
        return f"Gesture mode mein dikkat: {e}"


TOOL = {
    "name": "gesture_mode",
    "description": ("Webcam haath ke ishare (camera). 'gesture mode on / haath se chalao' -> on "
                    "(camera on); 'gesture mode off / camera band karo' -> off; 'gesture mode on hai?' "
                    "-> status. Ishare: khula haath play/pause, angootha upar next, neeche previous, "
                    "ungli upar/neeche volume, V = Kaka sune. Always tell him camera on/off. No question."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["on", "off", "status"]}},
        "required": ["action"]},
    "handler": gesture_mode,
}
