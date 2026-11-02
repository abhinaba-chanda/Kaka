"""
actions/reminder.py - Kaka update 18: reminders and alarms by voice.

Replaces the old Task-Scheduler reminder. The logic lives in
core/kaka_reminders.py; this file is only the tool Gemini calls.
Old Task-Scheduler reminders made by earlier versions still fire on their own.
"""
import time
from datetime import datetime


def reminder(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_reminders as R
        from core import kaka_services as ks
    except Exception as e:
        return f"Reminder system load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "add").lower().strip()
    try:
        if action in ("list", "show", "batao"):
            items = R.pending()
            ks.timing("reminder list", time.monotonic() - t0)
            if not items:
                return "Abhi koi reminder set nahi hai."
            now = datetime.now()
            lines = [f"{n}. {i['text']} - {R.speak_time(datetime.fromisoformat(i['due']), now)}"
                     + (" (alarm)" if i.get("kind") == "alarm" else "")
                     for n, i in enumerate(items[:10], 1)]
            ks.popup("Reminders", "\n".join(lines))
            more = f" (aur {len(items) - 10})" if len(items) > 10 else ""
            return f"{len(items)} reminder hain: " + "; ".join(lines) + more
        if action in ("remove", "delete", "cancel", "hatao"):
            q = str(p.get("message") or p.get("query") or "")
            idx = int(p.get("index") or 0)
            match = R.find(q, idx)
            if not match:
                return "Aisa koi reminder nahi mila. 'Mere reminders batao' bolo, list sun lo."
            if p.get("all"):
                pass
            elif len(match) > 1 and not idx:
                now = datetime.now()
                opts = "; ".join(f"{n}. {i['text']} ({R.speak_time(datetime.fromisoformat(i['due']), now)})"
                                 for n, i in enumerate(match[:5], 1))
                return (f"{len(match)} reminder milte hain: {opts}. Poocho kaunsa hatana hai "
                        "(number se), phir index ke saath dobara call karo.")
            if not (p.get("go_ahead") or p.get("confirm")):
                names = ", ".join(f"'{i['text']}'" for i in match[:3])
                return (f"[ASK_ONCE] Ek baar poocho: \"{names} wala reminder hata doon?\" "
                        "Haan pe isi tool ko action=remove, same message/index aur go_ahead=true ke saath call karo.")
            n = R.remove([i["id"] for i in match])
            ks.timing("reminder remove", time.monotonic() - t0)
            return f"{n} reminder hata diya."
        # ---- add
        msg = str(p.get("message") or "").strip() or "Reminder"
        kind = "alarm" if str(p.get("kind") or "").lower() == "alarm" or "alarm" in msg.lower() else "reminder"
        when_words = str(p.get("when") or "").strip()
        due = R.parse_when(when_words) if when_words else None
        if due is None and p.get("time"):
            try:
                d = str(p.get("date") or datetime.now().strftime("%Y-%m-%d"))
                due = datetime.strptime(f"{d} {str(p['time'])[:5]}", "%Y-%m-%d %H:%M")
            except Exception:
                due = None
        if due is None and when_words:
            due = R.parse_when(when_words + " " + msg)
        if due is None:
            return ("Time samajh nahi aaya. Poocho kab yaad dilana hai "
                    "(jaise '5 baje', '10 minute baad', 'kal subah 7 baje').")
        if due <= datetime.now():
            return "Ye time nikal chuka hai. Aage ka time bolo."
        item = R.add(msg, due, kind)
        R.start()
        when = R.speak_time(due)
        ks.timing("reminder add", time.monotonic() - t0, when)
        bday = ""
        try:
            from core import kaka_facts as F
            bday = F.birthday_hint(msg)
        except Exception:
            pass
        label = "Alarm" if kind == "alarm" else "Reminder"
        return f"{label} set: {when} - '{item['text']}'. Kaka band ho tab bhi agli baar khulte hi bata dega." + bday
    except Exception as e:
        return f"Reminder mein dikkat aayi: {e}"


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "reminder",
    "description": (
        "Reminders and alarms (saved, survive restart; Kaka beeps + speaks at the time, "
        "even from sleep). action=add with `when` = his EXACT time words "
        "('5 baje', '10 minute baad', 'kal subah 7 baje', 'parso sham 5') and `message`; "
        "kind=alarm for 'alarm laga do'. action=list for 'mere reminders batao'. "
        "action=remove for 'reminder hatao' (asks once; go_ahead=true after his haan)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["add", "list", "remove"],
                       "description": "add (default), list, remove"},
            "when": {"type": "STRING",
                     "description": "His own time words, unchanged, e.g. 'sham 5 baje', '10 minute baad', 'kal subah 7 baje'"},
            "message": {"type": "STRING",
                        "description": "What to remind (add) or which reminder (remove), e.g. 'chai'"},
            "kind": {"type": "STRING", "enum": ["reminder", "alarm"]},
            "date": {"type": "STRING", "description": "Optional YYYY-MM-DD if `when` is missing"},
            "time": {"type": "STRING", "description": "Optional HH:MM 24h if `when` is missing"},
            "index": {"type": "INTEGER", "description": "Number from the list, for remove"},
            "go_ahead": {"type": "BOOLEAN", "description": "true after he said haan to removing"},
        },
        "required": [],
    },
    "handler": reminder,
}
