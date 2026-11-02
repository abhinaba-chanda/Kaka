r"""
actions/game_mode.py - Kaka update 18: "game mode on / off". Logic in core/kaka_game.py.
"""
import time


def game_mode(parameters: dict) -> str:
    try:
        from core import kaka_game as G
    except Exception as e:
        return f"Game mode load nahi hua: {e}"
    a = str((parameters or {}).get("action") or "status").lower()
    try:
        if a == "on":
            return G.manual(True)
        if a == "off":
            return G.manual(False)
        if a == "auto_on":
            return G.set_auto(True)
        if a == "auto_off":
            return G.set_auto(False)
        return G.status()
    except Exception as e:
        return f"Game mode mein dikkat: {e}"


TOOL = {
    "name": "game_mode",
    "description": ("Game mode (Kaka chup + mic band + High performance power plan; comes on "
                    "by itself when a game is in front, off when the game closes). "
                    "'game mode on/off' -> on/off; 'game mode apne aap mat lagao' -> auto_off; "
                    "'apne aap lagao' -> auto_on; 'game mode on hai?' -> status. No question."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["on", "off", "auto_on", "auto_off", "status"]}},
        "required": ["action"]},
    "handler": game_mode,
}
