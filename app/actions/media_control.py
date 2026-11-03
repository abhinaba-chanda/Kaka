r"""
actions/media_control.py - Kaka update 18: volume, brightness and media keys
by voice ("volume 40", "awaaz badhao", "mute", "brightness 70", "gaana pause
karo", "next", "previous").  Primitives live in core/kaka_pc.py.
Never raises; every answer says what actually happened.
"""
from __future__ import annotations

import re
import sys
import time

ACTIONS = ["volume_set", "volume_up", "volume_down", "mute", "unmute",
           "volume_get", "brightness_set", "brightness_up", "brightness_down",
           "brightness_get", "play_pause", "play", "pause", "next", "previous",
           "stop"]

_ALIASES = {
    "volume": "volume_set", "set_volume": "volume_set", "volumeset": "volume_set",
    "louder": "volume_up", "increase_volume": "volume_up", "vol_up": "volume_up",
    "quieter": "volume_down", "decrease_volume": "volume_down", "vol_down": "volume_down",
    "toggle_mute": "mute", "mute_on": "mute", "mute_off": "unmute",
    "brightness": "brightness_set", "set_brightness": "brightness_set",
    "resume": "play", "playpause": "play_pause", "toggle": "play_pause",
    "next_track": "next", "skip": "next", "prev": "previous",
    "previous_track": "previous", "back": "previous",
}


def _parse_text(text: str):
    """Local Hinglish fallback when the model sent words, not an action."""
    t = " " + (text or "").lower() + " "
    num = re.search(r"(\d{1,3})", t)
    n = int(num.group(1)) if num else None
    bright = re.search(r"bright|roshni|screen (?:ki )?light", t)
    up = re.search(r"badha|zyada|jyada|tez|increase|\bup\b|upar|louder|high", t)
    down = re.search(r"kam |ghata|dheem|decrease|\bdown\b|neeche|low", t)
    if re.search(r"unmute|mute hata|mute band|awaaz (?:wapas|chalu)", t):
        return "unmute", None
    if re.search(r"\bmute\b|chup|awaaz band", t):
        return "mute", None
    if bright:
        if n is not None and not up and not down:
            return "brightness_set", n
        if up:
            return "brightness_up", n
        if down:
            return "brightness_down", n
        return "brightness_get", None
    if re.search(r"volume|awaaz|awaz|sound|aawaz", t):
        if n is not None and not up and not down:
            return "volume_set", n
        if up:
            return "volume_up", n
        if down:
            return "volume_down", n
        return "volume_get", None
    if re.search(r"\bnext\b|agla|agle|skip", t):
        return "next", None
    if re.search(r"previous|pichla|pichhla|pehle wala|\bprev\b", t):
        return "previous", None
    if re.search(r"\bstop\b|band karo", t):
        return "stop", None
    if re.search(r"pause|ruko|rok", t):
        return "pause", None
    if re.search(r"\bplay\b|chalao|resume|chalu", t):
        return "play", None
    return "", None


def _int(v):
    try:
        if v is None or v == "":
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        return int(round(float(m.group(0)))) if m else None
    except Exception:
        return None


def _undo(label, fn):
    try:
        from core.undo import push_undo
        push_undo(label, fn)
    except Exception:
        pass


def _do(action: str, value):
    from core import kaka_pc as pc
    if sys.platform != "win32" and not getattr(pc, "_TEST", False):
        return "Ye control sirf Windows pe chalta hai."

    if action == "volume_set":
        if value is None:
            return "Kitna volume? 0 se 100 ke beech number bolo."
        r = pc.volume_set(value)
        if not r["ok"]:
            return "Volume set nahi ho paya - na pycaw mila na volume keys chali."
        if r["before"] is not None:
            b = r["before"]
            _undo(f"volume {b}% -> {r['level']}%", lambda b=b: (pc.volume_set(b), f"Volume wapas {b}%.")[1])
        if r["exact"]:
            return f"Volume {r['level']}% kar diya."
        return (f"Volume lagbhag {r['level']}% kar diya (volume keys se; "
                "exact level padh nahi paya).")

    if action in ("volume_up", "volume_down"):
        step = abs(value) if value else 10
        step = max(2, min(100, step))
        r = pc.volume_step(step if action == "volume_up" else -step)
        if not r["ok"]:
            return "Volume badal nahi paya - volume keys nahi chali."
        word = "badha" if action == "volume_up" else "kam kar"
        if r["exact"]:
            if r["before"] is not None:
                b = r["before"]
                _undo(f"volume {b}% -> {r['level']}%", lambda b=b: (pc.volume_set(b), f"Volume wapas {b}%.")[1])
                if r["before"] == r["level"]:
                    return f"Volume pehle se {r['level']}% pe hai (limit)."
            return f"Volume {word} diya, ab {r['level']}%."
        return f"Volume {step}% {word} diya (keys se; exact level padh nahi paya)."

    if action in ("mute", "unmute"):
        r = pc.set_mute(action == "mute")
        if not r["ok"]:
            return "Mute nahi ho paya - mute key nahi chali."
        if r["toggled"]:
            return ("Mute key daba di - ye on/off toggle hai, exact status padh nahi "
                    "paya. Ulta hua ho toh ek baar aur bolo.")
        if action == "mute":
            return "Pehle se mute tha." if r["was"] is True else "Mute kar diya."
        return "Mute pehle se band tha." if r["was"] is False else "Unmute kar diya, awaaz wapas."

    if action == "volume_get":
        v = pc.volume_get()
        return f"Volume abhi {v}% hai." if v is not None else "Volume padh nahi paya (pycaw nahi mila)."

    if action in ("brightness_set", "brightness_up", "brightness_down", "brightness_get"):
        if action == "brightness_set":
            if value is None:
                return "Kitni brightness? 0 se 100 ke beech number bolo."
            r = pc.brightness_set(level=value)
        elif action == "brightness_get":
            r = pc.brightness_set()
        else:
            step = max(5, min(100, abs(value) if value else 10))
            r = pc.brightness_set(delta=step if action == "brightness_up" else -step)
        if not r["ok"]:
            why = (r.get("why") or "")[:80]
            return ("Ye monitor brightness control support nahi karta (laptop screen "
                    "pe chalega; external monitor ke button se karo)." + (f" [{why}]" if why else ""))
        if action == "brightness_get":
            return f"Brightness abhi {r['level']}% hai."
        if r.get("before") is not None and r["before"] != r["level"]:
            b = r["before"]
            _undo(f"brightness {b}% -> {r['level']}%",
                  lambda b=b: (pc.brightness_set(level=b), f"Brightness wapas {b}%.")[1])
        if r.get("before") == r["level"] and action != "brightness_set":
            return f"Brightness pehle se {r['level']}% pe hai (limit)."
        return f"Brightness {r['level']}% kar di."

    if action in ("play_pause", "play", "pause", "next", "previous", "stop"):
        key = {"play": "play_pause", "pause": "play_pause"}.get(action, action)
        if not pc.media_key(key):
            return "Media key nahi chali."
        return {
            "play": "Play key daba di (media play/pause).",
            "pause": "Pause key daba di (media play/pause).",
            "play_pause": "Play/pause key daba di.",
            "next": "Agla gaana/video - next key daba di.",
            "previous": "Pichla - previous key daba di.",
            "stop": "Stop key daba di.",
        }[action]
    return ""


def media_control(parameters: dict = None, player=None) -> str:
    t0 = time.monotonic()
    p = parameters or {}
    action = str(p.get("action") or "").strip().lower().replace(" ", "_").replace("-", "_")
    action = _ALIASES.get(action, action)
    value = _int(p.get("value"))
    if action not in ACTIONS:
        a2, v2 = _parse_text(" ".join(str(p.get(k) or "") for k in ("text", "action")))
        if a2:
            action = a2
            if value is None:
                value = v2
    if action not in ACTIONS:
        return ("Samjha nahi kaunsa control - volume/brightness number, mute, "
                "pause, play, next ya previous bolo.")
    try:
        out = _do(action, value) or "Kuch nahi hua."
    except Exception as e:
        out = f"{action} nahi ho paya: {str(e)[:120]}"
    try:
        from core import kaka_services as ks
        ks.timing(f"media_control {action}", time.monotonic() - t0)
    except Exception:
        pass
    return out


TOOL = {
    "name": "media_control",
    "description": (
        "Volume, screen brightness and media playback keys. USE THIS (not computer_settings) for: "
        "'volume 40' / 'awaaz 40 karo' (volume_set, value=40), 'awaaz badhao/tez karo' (volume_up), "
        "'awaaz kam karo/dheemi' (volume_down), 'mute'/'awaaz band' (mute), 'unmute' (unmute), "
        "'brightness 70' (brightness_set), 'brightness badhao/kam' (brightness_up/down), "
        "'video pause karo' / 'pause' / 'gaana roko' (pause), 'play/resume' / 'gaana chalu karo' "
        "(play), 'next' / 'next gaana' / 'agla gaana' (next), 'previous' / 'pichla gaana' "
        "(previous), 'stop' - media keys, for whatever is playing (YouTube in the "
        "browser, VLC, Spotify). Spotify song controls via play_music also fine. Never asks."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ACTIONS,
                       "description": "What to do."},
            "value": {"type": "INTEGER",
                      "description": "Level 0-100 for volume_set/brightness_set; optional step for up/down (default 10)."},
            "text": {"type": "STRING",
                     "description": "Optional: his words, used only if action is unclear."},
        },
        "required": ["action"],
    },
    "handler": media_control,
}
