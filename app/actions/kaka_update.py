"""
actions/kaka_update.py - Kaka update 18: "aaj ka update" (morning update on
demand), morning update on/off, India news, Barca score.

The daily 8:00 AM run lives in core/kaka_feeds.py (scheduler registered with
core.kaka_services.on_start when this module is imported at startup).
"""
from __future__ import annotations

import time

try:                                   # stdlib-only module; registers the 8 AM scheduler
    from core import kaka_feeds as _feeds
except Exception:                      # never block tool discovery
    _feeds = None


def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def kaka_update(parameters: dict = None, player=None) -> str:
    t0 = time.monotonic()
    p = parameters or {}
    action = str(p.get("action") or "morning_update").strip().lower()
    ks = _ks()
    try:
        f = _feeds
        if f is None:
            from core import kaka_feeds as f
        if action in ("morning_off", "off", "band"):
            ok = f.set_enabled(False)
            msg = ("Theek hai, roz subah wala brief band kar diya." if ok else
                   "Morning update band karna save nahi ho paya (Data folder mein likh nahi saka).")
        elif action in ("morning_on", "on", "chalu"):
            ok = f.set_enabled(True)
            msg = ("Morning brief chalu: roz din mein pehli baar Kaka khulne par (ya subah 8 baje) "
                   "sunaunga." if ok else
                   "Morning update chalu karna save nahi ho paya (Data folder mein likh nahi saka).")
        elif action == "status":
            msg = ("Morning brief chalu hai, roz pehli baar Kaka khulne par." if f.is_enabled()
                   else "Morning update abhi band hai.")
        elif action == "news":
            msg = f.news_text(f.news(3))
            if ks:
                ks.popup("News", msg)
        elif action in ("barca", "barca_score", "score"):
            msg = f.barca_text(f.barca())
            if ks:
                ks.popup("Barca", msg)
        else:                          # morning_update / aaj ka update
            msg, _st = f.compose_morning()
            if ks:
                ks.popup("Aaj ka update", msg)
            msg += " (Ye poora brief sunao - har point, natural Hinglish mein.)"
    except Exception as e:
        msg = f"Update laane mein dikkat aayi ({type(e).__name__}), thodi der baad try karo."
    if ks:
        ks.timing(f"kaka_update {action}", time.monotonic() - t0)
    return msg


TOOL = {
    "name": "kaka_update",
    "description": (
        "Kaka's live update / morning brief: 'aaj ka update' / 'aaj ka brief do' / 'morning brief' / "
        "'morning update' (greeting + weather for his city + reminders/notes/birthdays + Barca + "
        "India news in one go), 'news sunao' (top India headlines), 'Barca ka score' (live score if a "
        "match is on, else last result + next match in IST), and turning the daily morning "
        "brief on/off ('morning brief / update band karo / chalu karo'). Read-only, never "
        "ask a question before it. Weather alone goes to weather_report."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["morning_update", "news", "barca", "morning_on", "morning_off", "status"],
                "description": "morning_update (default) | news | barca | morning_on | morning_off | status",
            },
        },
        "required": ["action"],
    },
    "handler": kaka_update,
}
