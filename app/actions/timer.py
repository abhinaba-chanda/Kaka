"""
actions/timer.py - Kaka update 19: "25 minute ka timer", "timer kitna bacha",
"timer band karo", stopwatch, pomodoro. Logic: core/kaka_timer.py.
"""
import time


def timer(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_timer as T
        from core import kaka_services as ks
    except Exception as e:
        return f"Timer load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "start").lower().strip()
    words = str(p.get("duration") or "").strip()
    label = str(p.get("label") or "").strip()
    try:
        if action == "start":
            secs = T.parse_duration(words) if words else None
            if secs is None and p.get("minutes"):
                try:
                    secs = int(float(p["minutes"]) * 60)
                except Exception:
                    secs = None
            if secs is None:
                return "Kitne der ka timer? Poocho (jaise '25 minute', 'dedh ghanta', '2 min 30 sec')."
            if secs > 24 * 3600:
                return "Itna lamba timer nahi - iske liye reminder use karo (jaise 'kal subah 7 baje')."
            T.add(secs, label)
            ks.timing("timer start", time.monotonic() - t0, T.fmt(secs))
            return (f"{T.fmt(secs)} ka timer shuru" + (f" ({label})" if label else "") +
                    ". Khatam hone pe beep karke bataunga, HUD pe time dikh raha hai.")
        if action == "status":
            s = T.status_text()
            if not s:
                return "Koi timer ya stopwatch nahi chal raha."
            return s + "."
        if action == "stop":
            gone = T.cancel(label=label, all_=bool(p.get("all")))
            if not gone:
                if T._POMO["on"]:
                    return T.pomo_stop()
                return "Koi timer chal nahi raha tha."
            return f"{len(gone)} timer band kiya" + (f" ({gone[0]['label']})" if gone[0]["label"] else "") + "."
        if action == "stopwatch_start":
            return T.sw_start()
        if action == "stopwatch_stop":
            return T.sw_stop()
        if action == "stopwatch_status":
            v = T.sw_value()
            if not v and T._SW["start"] is None:
                return "Stopwatch chal nahi raha. 'stopwatch shuru karo' bolo."
            return f"Stopwatch: {T.fmt(v)}" + ("" if T._SW["start"] is not None else " (ruka hua)") + "."
        if action == "stopwatch_reset":
            return T.sw_reset()
        if action == "pomodoro_start":
            def _i(k, d):
                try:
                    return int(p.get(k) or d)
                except Exception:
                    return d
            return T.pomo_start(_i("work", 25), _i("break", 5), _i("cycles", 4))
        if action == "pomodoro_stop":
            return T.pomo_stop()
        return "Ye timer action samajh nahi aaya."
    except Exception as e:
        return f"Timer mein dikkat aayi: {e}"


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "timer",
    "description": (
        "Countdown timer / stopwatch / pomodoro (for minutes-hours from now; a clock time like "
        "'5 baje' is a reminder instead). '25 minute ka timer' -> start duration='25 minute'; "
        "'dedh ghanta', '2 min 30 sec' -> pass his exact words in duration; 'timer kitna bacha' "
        "-> status; 'timer band karo' -> stop; 'stopwatch shuru/ruko/kitna hua/reset' -> "
        "stopwatch_start/stopwatch_stop/stopwatch_status/stopwatch_reset; 'pomodoro shuru' -> "
        "pomodoro_start (25/5 default), 'pomodoro band' -> pomodoro_stop. Never ask first."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["start", "status", "stop", "stopwatch_start", "stopwatch_stop",
                                "stopwatch_status", "stopwatch_reset", "pomodoro_start",
                                "pomodoro_stop"]},
            "duration": {"type": "STRING", "description": "His own words, e.g. '25 minute', 'dedh ghanta'"},
            "label": {"type": "STRING", "description": "Optional name, e.g. 'chai', 'padhai'"},
            "work": {"type": "INTEGER", "description": "pomodoro work minutes (default 25)"},
            "break": {"type": "INTEGER", "description": "pomodoro break minutes (default 5)"},
            "cycles": {"type": "INTEGER", "description": "pomodoro rounds (default 4)"},
        },
        "required": [],
    },
    "handler": timer,
}
