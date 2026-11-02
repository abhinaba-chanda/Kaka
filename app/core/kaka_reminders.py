r"""
core/kaka_reminders.py - Kaka update 18: reminders and alarms by voice.

  "5 baje yaad dilana chai"      "10 minute baad bolna chai"
  "kal subah 7 baje alarm"       "parso sham 5 baje doctor"
  "mere reminders batao"         "chai wala reminder hatao"

Saved in F:\Kaka\Data\reminders.json, so they survive a restart. A reminder
whose time passed while Kaka was closed is said on the next start with
"chhoot gaya tha". At the time: beep + HUD popup + Kaka says it - even when
Kaka is asleep (wakes for the one line, then sleeps again). In game mode a
reminder waits for the game to close; an alarm does not wait.
Nothing here raises into Kaka.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from core import kaka_services as ks

FILE = "reminders.json"
TICK_S = 5.0
_LOCK = threading.RLock()
_STARTED = {"on": False}

# ------------------------------------------------------------------ parsing
_NUM_WORDS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4,
    "four": 4, "paanch": 5, "panch": 5, "five": 5, "chhe": 6, "che": 6, "chheh": 6, "six": 6,
    "saat": 7, "seven": 7, "aath": 8, "eight": 8, "nau": 9, "nine": 9, "das": 10, "dus": 10,
    "ten": 10, "gyarah": 11, "gyara": 11, "eleven": 11, "barah": 12, "bara": 12, "twelve": 12,
    "pandrah": 15, "pandra": 15, "fifteen": 15, "bees": 20, "twenty": 20, "pachees": 25,
    "pachis": 25, "tees": 30, "thirty": 30, "chalis": 40, "chaalis": 40, "forty": 40,
    "pachas": 50, "pachaas": 50, "fifty": 50,
}
_FRACTION_PREFIX = {"saade": 0.5, "sade": 0.5, "sawa": 0.25, "savva": 0.25, "sava": 0.25,
                    "paune": -0.25, "pone": -0.25}
_SPECIAL_HOURS = {"dedh": 1.5, "derh": 1.5, "dhai": 2.5, "dhaai": 2.5, "adhai": 2.5}
_PERIODS = {
    "subah": "am", "subha": "am", "savere": "am", "sawere": "am", "morning": "am", "am": "am",
    "a.m.": "am", "dopahar": "noon", "dopehar": "noon", "dupahar": "noon", "afternoon": "noon",
    "sham": "pm", "shaam": "pm", "evening": "pm", "pm": "pm", "p.m.": "pm",
    "raat": "night", "rat": "night", "night": "night", "tonight": "night",
}
_WEEKDAYS = {
    "monday": 0, "somvar": 0, "somwar": 0, "tuesday": 1, "mangalvar": 1, "mangalwar": 1,
    "wednesday": 2, "budhvar": 2, "budhwar": 2, "thursday": 3, "guruvar": 3, "guruwar": 3,
    "brihaspativar": 3, "friday": 4, "shukravar": 4, "shukrawar": 4, "saturday": 5,
    "shanivar": 5, "shaniwar": 5, "sunday": 6, "ravivar": 6, "raviwar": 6, "itvaar": 6,
    "itwar": 6, "etwar": 6,
}
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def _num(tok: str) -> Optional[float]:
    tok = tok.strip().lower()
    if not tok:
        return None
    try:
        return float(tok)
    except ValueError:
        pass
    if tok in ("aadha", "aadhe", "adha", "adhe", "half"):
        return 0.5
    return _NUM_WORDS.get(tok)


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("\u0915\u0932", " kal ")  # Devanagari kal
    t = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", t)
    t = re.sub(r"(\d)(baje|bje|min|mins|minute|minutes|ghante|ghanta|hr|hrs|sec)\b", r"\1 \2", t)
    t = re.sub(r"[,!?]", " ", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


_UNIT_RE = (r"(second|seconds|sec|secs|second|minute|minutes|min|mins|minat|ghante|ghanta|"
            r"ghanto|hour|hours|hr|hrs|din|day|days)")


def _relative(t: str, now: datetime) -> Optional[datetime]:
    m = re.search(r" (\d+(?:\.\d+)?|[a-z]+) " + _UNIT_RE + r" (?:baad|bad|mein|me|main|later|after|ke baad)? ?", t)
    m2 = re.search(r" (?:in|after) (\d+(?:\.\d+)?|[a-z]+) " + _UNIT_RE + " ", t)
    m = m2 or m
    if m:
        n = _num(m.group(1))
        if n is None and m.group(1) in _SPECIAL_HOURS:
            n = _SPECIAL_HOURS[m.group(1)]
        if n is None:
            return None
        u = m.group(2)
        # only treat as relative when "baad/mein/in/after" really follows or precedes
        tail = t[m.end(2):m.end(2) + 14]
        if not m2 and not re.match(r" ?(baad|bad|mein|me|main|later|ke baad|after)", tail):
            return None
        if u.startswith("sec"):
            return now + timedelta(seconds=n)
        if u.startswith(("min",)):
            return now + timedelta(minutes=n)
        if u.startswith(("ghant", "hour", "hr")):
            return now + timedelta(hours=n)
        if u.startswith(("din", "day")):
            return now + timedelta(days=n)
    m = re.search(r" (aadhe|adhe|aadha|half) (ghante|ghanta|hour) ", t)
    if m:
        return now + timedelta(minutes=30)
    m = re.search(r" (dedh|derh|dhai|dhaai|adhai) (ghante|ghanta|hour) ", t)
    if m:
        return now + timedelta(hours=_SPECIAL_HOURS[m.group(1)])
    return None


def _clock(t: str):
    """-> (hour_float, minute, period or None, explicit) or None"""
    period = None
    for w, p in _PERIODS.items():
        if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t):
            period = p
            break
    # 5:30 / 5.30 / 17:45
    m = re.search(r" (\d{1,2})[:.](\d{2}) ", t)
    if m:
        return int(m.group(1)), int(m.group(2)), period
    # "5 bajkar 30 minute" / "5 baj ke 30"
    m = re.search(r" (\d{1,2}|[a-z]+) baj ?(?:kar|ke|k) (\d{1,2}|[a-z]+) ?(?:minute|min|mins)? ", t)
    if m and _num(m.group(1)) is not None and _num(m.group(2)) is not None:
        return int(_num(m.group(1))), int(_num(m.group(2))), period
    # saade 5 / sawa 5 / paune 5
    m = re.search(r" (saade|sade|sawa|savva|sava|paune|pone) (\d{1,2}|[a-z]+) ", t)
    if m and _num(m.group(2)) is not None:
        h = _num(m.group(2)) + _FRACTION_PREFIX[m.group(1)]
        return int(h), int(round((h - int(h)) * 60)), period
    m = re.search(r" (dedh|derh|dhai|dhaai|adhai) (?:baje|bje) ", t)
    if m:
        h = _SPECIAL_HOURS[m.group(1)]
        return int(h), int(round((h - int(h)) * 60)), period
    # 5 baje / 5 bje / at 5 / 5 pm / sham 5 / 7 o'clock
    m = (re.search(r" (\d{1,2}|[a-z]+) (?:baje|bje|bajey|o'clock|oclock) ", t)
         or re.search(r" (\d{1,2}) (?:am|pm|a\.m\.|p\.m\.) ", t)
         or re.search(r" at (\d{1,2}) ", t))
    if m and _num(m.group(1)) is not None:
        return int(_num(m.group(1))), 0, period
    if period:
        m = re.search(r"(?:subah|subha|savere|sawere|dopahar|dopehar|dupahar|sham|shaam|raat|rat|morning|evening|night) (\d{1,2}|[a-z]+) ", t)
        if m and _num(m.group(1)) is not None and _num(m.group(1)) <= 12:
            return int(_num(m.group(1))), 0, period
    return None


def _apply_period(h: int, period: Optional[str]) -> Optional[int]:
    if period is None:
        return None
    if period == "am":
        return 0 if h == 12 else h
    if period == "noon":
        return h if h == 12 else (h + 12 if h < 7 else h)
    if period == "pm":
        return h if h >= 12 else h + 12
    if period == "night":
        if h == 12:
            return 0
        return h + 12 if 7 <= h <= 11 else (h if h < 7 else h)  # raat 1-6 -> early hours
    return None


def _day_offset(t: str, now: datetime) -> Optional[int]:
    if re.search(r" (parso|parson|parsoon|day after tomorrow) ", t):
        return 2
    if re.search(r" (kal|kl|tomorrow|tmrw) ", t):
        return 1
    if re.search(r" (aaj|aj|today|tonight) ", t):
        return 0
    for w, wd in _WEEKDAYS.items():
        if re.search(r" " + w + r" ", t):
            d = (wd - now.weekday()) % 7
            return d if d else 7
    return None


def _date_explicit(t: str, now: datetime) -> Optional[datetime]:
    m = re.search(r" (\d{1,2}) (?:tarikh |th |st |nd |rd )?([a-z]+) ", t)
    if m and m.group(2) in _MONTHS:
        d, mo = int(m.group(1)), _MONTHS[m.group(2)]
        try:
            dt = datetime(now.year, mo, d)
        except ValueError:
            return None
        if dt.date() < now.date():
            dt = dt.replace(year=now.year + 1)
        return dt
    m = re.search(r" ([a-z]+) (\d{1,2}) ", t)
    if m and m.group(1) in _MONTHS:
        try:
            dt = datetime(now.year, _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
        if dt.date() < now.date():
            dt = dt.replace(year=now.year + 1)
        return dt
    return None


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """His spoken time words -> a datetime in the future, or None."""
    now = now or datetime.now()
    t = _norm(text)
    rel = _relative(t, now)
    if rel is not None:
        return rel.replace(microsecond=0)
    clock = _clock(t)
    offset = _day_offset(t, now)
    day = _date_explicit(t, now)
    if clock is None and offset is None and day is None:
        return None
    if clock is None:
        # "kal" with no time: morning 9 (or the same hour for "kal subah"? -> 9/8)
        period = None
        for w, p in _PERIODS.items():
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t):
                period = p
                break
        h = {"am": 8, "noon": 13, "pm": 18, "night": 21}.get(period, 9)
        mi = 0
        explicit_h = h
    else:
        h, mi, period = clock
        if h > 23 or mi > 59:
            return None
        explicit_h = _apply_period(h, period) if h <= 12 else h
    if day is not None:
        base = day
    else:
        base = datetime(now.year, now.month, now.day) + timedelta(days=offset or 0)
    if explicit_h is not None:
        cand = base.replace(hour=explicit_h % 24, minute=mi)
        if offset is None and day is None and cand <= now:
            cand += timedelta(days=1)
        return cand
    # no period word: 5 -> 05:00 or 17:00, whichever comes first after now (on that day)
    opts = []
    for hh in sorted({h % 24, (h + 12) % 24} if h < 12 else {h % 24}):
        c = base.replace(hour=hh, minute=mi)
        opts.append(c)
    if offset is None and day is None:
        opts += [o + timedelta(days=1) for o in opts]
        fut = [o for o in opts if o > now]
        return min(fut) if fut else None
    # a named day: prefer the daytime reading (7 -> 7 AM, 5 -> 5 PM, 1-6 -> PM except "kal 6"?)
    hh = h if h >= 7 else h + 12
    return base.replace(hour=hh % 24, minute=mi)


def speak_time(dt: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    d = (dt.date() - now.date()).days
    day = {0: "aaj", 1: "kal", 2: "parso"}.get(d, dt.strftime("%d %b"))
    h = dt.hour
    part = ("subah" if 4 <= h < 12 else "dopahar" if 12 <= h < 16 else
            "sham" if 16 <= h < 20 else "raat")
    h12 = h % 12 or 12
    mm = f":{dt.minute:02d}" if dt.minute else ""
    return f"{day} {part} {h12}{mm} baje"


# ------------------------------------------------------------------ storage
def _path():
    return ks.data_path(FILE)


def load() -> list:
    with _LOCK:
        data = ks.read_json(_path(), [])
        return data if isinstance(data, list) else []


def save(items: list) -> bool:
    with _LOCK:
        return ks.write_json(_path(), items)


def add(text: str, due: datetime, kind: str = "reminder") -> dict:
    item = {"id": uuid.uuid4().hex[:8], "text": (text or "reminder").strip()[:200],
            "due": due.replace(microsecond=0).isoformat(), "kind": kind,
            "created": datetime.now().replace(microsecond=0).isoformat(), "fired": False}
    with _LOCK:
        items = load()
        items.append(item)
        save(items)
    return item


def pending(now: Optional[datetime] = None) -> list:
    items = [i for i in load() if not i.get("fired")]
    return sorted(items, key=lambda i: i.get("due", ""))


def find(query: str = "", index: int = 0) -> list:
    items = pending()
    if index and 1 <= index <= len(items):
        return [items[index - 1]]
    q = (query or "").lower().strip()
    if not q:
        return items
    words = [w for w in re.findall(r"[a-z0-9\u0900-\u097f]+", q)
             if w not in ("reminder", "alarm", "wala", "wali", "ka", "ki", "ke", "hatao",
                          "delete", "remove", "cancel", "karo", "mera", "meri", "mere")]
    if not words:
        return items
    out = [i for i in items if any(w in i["text"].lower() for w in words)]
    if not out:
        out = [i for i in items if any(w in speak_time(datetime.fromisoformat(i["due"])).lower()
                                        for w in words)]
    return out


def remove(ids) -> int:
    ids = set(ids)
    with _LOCK:
        items = load()
        keep = [i for i in items if i.get("id") not in ids]
        save(keep)
        return len(items) - len(keep)


# ------------------------------------------------------------------ firing
def _due_now(now: datetime) -> tuple:
    fire, missed = [], []
    for i in pending():
        try:
            due = datetime.fromisoformat(i["due"])
        except Exception:
            continue
        if due <= now:
            (missed if (now - due).total_seconds() > 90 else fire).append(i)
    return fire, missed


def _mark_fired(items: list, now: datetime) -> None:
    ids = {i["id"] for i in items}
    with _LOCK:
        all_items = load()
        for i in all_items:
            if i.get("id") in ids:
                i["fired"] = True
                i["fired_at"] = now.replace(microsecond=0).isoformat()
        # keep the file small: drop fired ones older than 3 days
        cut = (now - timedelta(days=3)).isoformat()
        all_items = [i for i in all_items if not i.get("fired") or i.get("fired_at", "") > cut]
        save(all_items)


def tick(now: Optional[datetime] = None, first: bool = False) -> list:
    """One scheduler step. Returns what it announced (for tests)."""
    now = now or datetime.now()
    fire, missed = _due_now(now)
    said = []
    if not fire and not missed:
        return said
    _mark_fired(fire + missed, now)
    t0 = time.monotonic()
    for i in fire:
        alarm = i.get("kind") == "alarm"
        label = "Alarm" if alarm else "Reminder"
        if alarm or not ks.in_game_mode():
            ks.beep("reminder")
        ks.popup(f"{label} \u23f0", i["text"])
        ks.log(f"SYS: {label}: {i['text']}")
        ks.say_async(f"{label}: {i['text']}", urgent=alarm)
        said.append(("now", i["text"]))
    if missed:
        names = "; ".join(f"{i['text']} ({speak_time(datetime.fromisoformat(i['due']), now)})"
                          for i in missed[:5])
        more = f" aur {len(missed) - 5} aur" if len(missed) > 5 else ""
        ks.beep("reminder")
        ks.popup("Chhoota reminder", names + more)
        ks.log(f"SYS: Chhoote reminders: {names}{more}")
        ks.say_async(f"Ye reminder chhoot gaya tha (Kaka band tha): {names}{more}",
                     urgent=any(i.get("kind") == "alarm" for i in missed))
        said += [("missed", i["text"]) for i in missed]
    ks.timing("reminder fire", time.monotonic() - t0, f"{len(fire)} now, {len(missed)} missed")
    return said


def _loop():
    first = True
    while True:
        try:
            tick(first=first)
        except Exception as e:
            ks._print(f"[reminders] tick failed: {e}")
        first = False
        time.sleep(TICK_S)


def start() -> None:
    if _STARTED["on"]:
        return
    _STARTED["on"] = True
    threading.Thread(target=_loop, name="kaka-u18-reminders", daemon=True).start()


ks.on_start(start)
