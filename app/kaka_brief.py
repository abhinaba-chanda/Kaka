"""KAKA v1.1: the morning brief, upgraded.

core/kaka_feeds.py still builds and speaks it (greeting with your name,
weather, Barca, news). v1.1 adds, through extras():
  * weather for YOUR city (kaka_user.user_city, not always Kolkata)
  * 5 headlines instead of 3 (Google News RSS India, no key)
  * today's reminders, your open notes, birthdays today / tomorrow,
    the newest diary line from yesterday
and the timing: it runs at the FIRST Kaka start of the day (from 5 AM), or at
8 AM if Kaka is already running - not only between 8 and 11.
"aaj ka brief do" gives it any time (kaka_update tool). Settings toggle:
Settings > v1.1 FEATURES > MORNING BRIEF (or "morning brief band karo").
Every part is optional: one failing just leaves its line out.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

HEADLINES = 5
EARLIEST = (5, 0)


def _reminders_today(now: datetime) -> list:
    try:
        from core import kaka_reminders as R
        out = []
        for it in R.pending(now):
            try:
                due = datetime.fromisoformat(str(it.get("due")))
            except Exception:
                continue
            if due.date() == now.date() and due >= now - timedelta(minutes=5):
                out.append(f"{due.strftime('%I:%M %p').lstrip('0')} - {it.get('text', '')}")
        return out[:5]
    except Exception:
        return []


def _open_notes() -> list:
    try:
        from core import kaka_notes as N
        return [i.get("text", "") for i in N.items() if not i.get("done")][:5]
    except Exception:
        return []


def _birthdays() -> list:
    try:
        from core import kaka_facts as F
        return [f"{who} ka birthday {when}" for who, when, _n in F.upcoming_birthdays(1)]
    except Exception:
        return []


def _yesterday_diary(now: datetime) -> str:
    try:
        import kaka_diary as D
        items = D.on_date(now.date() - timedelta(days=1))
        return items[-1]["text"][:160] if items else ""
    except Exception:
        return ""


def extras(now: Optional[datetime] = None) -> list:
    """Extra sentences for the brief (Hinglish). Never raises."""
    now = now or datetime.now()
    lines = []
    try:
        r = _reminders_today(now)
        if r:
            lines.append("Aaj ke reminders: " + "; ".join(r) + ".")
        n = _open_notes()
        if n:
            lines.append(f"Aapke notes ({len(n)}): " + "; ".join(n) + ".")
        b = _birthdays()
        if b:
            lines.append("Yaad dila doon: " + "; ".join(b) + ".")
        d = _yesterday_diary(now)
        if d:
            lines.append(f"Kal diary mein likha tha: {d}.")
    except Exception:
        pass
    return lines


def first_start_due(now: datetime, state: dict, enabled: bool) -> bool:
    """First Kaka start of the day (from 5 AM) and not run today."""
    if not enabled:
        return False
    if state.get("last_run") == now.strftime("%Y-%m-%d"):
        return False
    return (now.hour, now.minute) >= EARLIEST
