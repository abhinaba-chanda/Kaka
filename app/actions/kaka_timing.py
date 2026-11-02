"""Kaka action - "timing" / "kitna time laga": read out how long the last action took (update 16).

Reads only. The numbers come from F:\\Kaka\\Data\\kaka-timing.txt (core/timing_log.py).
"""
from core import timing_log


def kaka_timing(parameters: dict = None, player=None) -> str:
    try:
        text = timing_log.summary()
    except Exception:
        text = "Timing abhi padh nahi paya."
    if player is not None:
        try:
            player.write_log(f"Kaka: {text}")
        except Exception:
            pass
    return text + " (Say exactly this to him, nothing more.)"


TOOL = {
    "name": "kaka_timing",
    "description": (
        "Tells how long Kaka's LAST action took (from the saved timing file). "
        "Call this when he says just 'timing', 'kitna time laga', 'time kitna laga', "
        "'kitni der lagi' or 'how long did that take'. Read out the result as given."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    },
    "handler": kaka_timing,
}
