"""
actions/weather_report.py - Kaka update 18: real weather, spoken (it used to
only open a Google search in the browser).

Open-Meteo (keyless): now + today's min/max + rain chance, or tomorrow's
("kal barish hogi?"). City is optional - default Kolkata; any other city is
geocoded by Open-Meteo's keyless geocoding API.
"""
from __future__ import annotations

import time


def weather_action(parameters: dict = None, player=None, session_memory=None) -> str:
    t0 = time.monotonic()
    p = parameters or {}
    city = str(p.get("city") or "").strip()
    when = str(p.get("when") or p.get("time") or "today").strip().lower()
    if when in ("kal", "tomorrow", "kal ka", "next day"):
        when = "tomorrow"
    elif when not in ("today", "now"):
        when = "today"
    try:
        from core import kaka_feeds as f
        w = f.weather(city)
        msg = f.weather_text(w, when)
        ok = bool(w) and not w.get("error")
    except Exception as e:
        msg, ok = f"Mausam laane mein dikkat aayi ({type(e).__name__}).", False
    try:
        from core import kaka_services as ks
        if ok:
            ks.popup("Mausam", msg)
        ks.timing("weather_report", time.monotonic() - t0, city or "Kolkata")
    except Exception:
        pass
    if player:
        try:
            player.write_log(f"Kaka: {msg}")
        except Exception:
            pass
    return msg


# -- Tool declaration (auto-discovered by core/action_loader.py) -------------
TOOL = {
    "name": "weather_report",
    "description": (
        "Live weather, spoken: 'aaj mausam kaisa hai', 'bahar garmi hai?', 'kal barish hogi?', "
        "'Delhi ka mausam'. Gives temperature now, today's min/max and rain chance, or "
        "tomorrow's forecast. City optional (default Kolkata). Never opens a browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "City name; leave empty for Kolkata"},
            "when": {"type": "STRING", "enum": ["today", "now", "tomorrow"],
                     "description": "today (default) | now | tomorrow ('kal')"},
        },
        "required": [],
    },
    "handler": weather_action,
}
