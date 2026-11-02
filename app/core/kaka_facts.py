r"""
core/kaka_facts.py - Kaka update 18: Kaka remembers his personal facts.

  "yaad rakhna Didi ka birthday 5 March hai"   "mujhe biryani pasand hai"
  "kya yaad hai mere baare mein"              "ye bhool jao" / "biryani wali baat bhool jao"

Stored in F:\Kaka\Data\memory.json. A compact summary goes into Kaka's
instructions at every session start (memory/memory_manager.py calls
prompt_block()). Birthdays: Kaka only MENTIONS a day-before reminder, never
creates one by itself. Nothing here raises.
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from core import kaka_services as ks

FILE = "memory.json"
MAX_FACTS = 300
PROMPT_CHARS = 900
_LOCK = threading.RLock()
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


def _path():
    return ks.data_path(FILE)


def load() -> list:
    with _LOCK:
        d = ks.read_json(_path(), {})
        f = d.get("facts") if isinstance(d, dict) else None
        return f if isinstance(f, list) else []


def _save(facts: list) -> bool:
    with _LOCK:
        return ks.write_json(_path(), {"version": 1, "facts": facts[-MAX_FACTS:]})


def parse_date(text: str) -> Optional[str]:
    """'5 March' / 'March 5' / '05-03' / '5/3' -> 'DD-MM'."""
    t = (text or "").lower()
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th|tarikh)?\s*(?:of\s+)?([a-z]{3,9})", t)
    if m:
        for i, mo in enumerate(_MONTHS):
            if m.group(2) == mo or (len(m.group(2)) >= 3 and mo.startswith(m.group(2))):
                return f"{int(m.group(1)):02d}-{i + 1:02d}"
    m = re.search(r"([a-z]{3,9})\s+(\d{1,2})", t)
    if m:
        for i, mo in enumerate(_MONTHS):
            if m.group(1) == mo or (len(m.group(1)) >= 3 and mo.startswith(m.group(1))):
                return f"{int(m.group(2)):02d}-{i + 1:02d}"
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})\b", t)
    if m and 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def _guess_person(text: str) -> str:
    m = re.search(r"([A-Za-z][A-Za-z ]{0,30}?)\s+(?:ka|ki|ke|'s)\s+(?:birthday|janamdin|janmdin|bday)", text, re.I)
    if m:
        p = m.group(1).strip()
        p = re.sub(r"^(yaad rakhna|yaad rakho|remember|ki|ke|ka|mere|meri|mera)\s+", "", p, flags=re.I)
        return p.strip()
    m = re.search(r"(?:birthday|bday) of ([A-Za-z ]{1,30})", text, re.I)
    return m.group(1).strip() if m else ""


def add(fact: str, category: str = "", person: str = "", when: str = "") -> dict:
    fact = re.sub(r"^\s*(yaad rakhna|yaad rakho|remember( that)?|note karo)\s*[:,-]?\s*", "",
                  (fact or "").strip(), flags=re.I)[:300]
    cat = (category or "").lower().strip()
    low = fact.lower()
    if not cat:
        if any(w in low for w in ("birthday", "janamdin", "janmdin", "bday")):
            cat = "birthday"
        elif any(w in low for w in ("pasand nahi", "nahi pasand", "hate", "dislike", "nafrat")):
            cat = "napasand"
        elif any(w in low for w in ("pasand", "favourite", "favorite", "like", "love")):
            cat = "pasand"
        else:
            cat = "general"
    item = {"id": uuid.uuid4().hex[:8], "text": fact, "category": cat,
            "added": datetime.now().strftime("%Y-%m-%d")}
    if cat == "birthday":
        item["person"] = (person or _guess_person(fact)).strip()[:40]
        d = parse_date(when) or parse_date(fact)
        if d:
            item["date"] = d
    with _LOCK:
        facts = load()
        # same fact said again -> replace, not duplicate
        facts = [f for f in facts if f.get("text", "").lower() != fact.lower()
                 and not (cat == "birthday" and item.get("person") and
                          f.get("category") == "birthday" and
                          f.get("person", "").lower() == item["person"].lower())]
        facts.append(item)
        _save(facts)
    return item


def find(query: str) -> list:
    q = (query or "").lower()
    words = [w for w in re.findall(r"[a-z0-9\u0900-\u097f]+", q)
             if w not in ("ye", "yeh", "wo", "woh", "bhool", "jao", "bhul", "forget", "wali",
                          "wala", "baat", "mera", "meri", "mere", "ka", "ki", "ke", "hai",
                          "about", "that", "the", "mujhe", "jo", "maine", "bola", "tha")]
    facts = load()
    if not words:
        return []
    scored = []
    for f in facts:
        hay = (f.get("text", "") + " " + f.get("person", "")).lower()
        s = sum(1 for w in words if w in hay)
        if s:
            scored.append((s, f))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        return []
    top = scored[0][0]
    return [f for s, f in scored if s == top]


def last() -> Optional[dict]:
    facts = load()
    return facts[-1] if facts else None


def forget(ids) -> int:
    ids = set(ids)
    with _LOCK:
        facts = load()
        keep = [f for f in facts if f.get("id") not in ids]
        _save(keep)
        return len(facts) - len(keep)


def _bday_text(f: dict) -> str:
    d = f.get("date")
    if not d:
        return f.get("text", "")
    dd, mm = d.split("-")
    return f"{f.get('person') or 'kisi'} ka birthday {int(dd)} {_MONTHS[int(mm) - 1].title()}"


def summary_lines() -> list:
    out = []
    for f in load():
        out.append(_bday_text(f) if f.get("category") == "birthday" else f.get("text", ""))
    return [l for l in out if l]


def prompt_block() -> str:
    """Compact block for the session instructions ('' when nothing saved)."""
    lines = summary_lines()
    if not lines:
        return ""
    body, used = [], 0
    for l in reversed(lines):            # newest first
        if used + len(l) + 3 > PROMPT_CHARS:
            break
        body.append("- " + l)
        used += len(l) + 3
    up = upcoming_birthdays(7)
    extra = ""
    if up:
        extra = "\nCOMING UP: " + "; ".join(f"{p} ka birthday {d}" for p, d, _n in up) + \
                " (mention once if relevant; offer a reminder the day before - do not create it yourself)"
    return ("[ABHINABA KI BAATEIN - kaka_memory se; naturally use karo, list mat padho]\n"
            + "\n".join(body) + extra + "\n")


def upcoming_birthdays(days: int = 7, today: Optional[date] = None) -> list:
    today = today or date.today()
    out = []
    for f in load():
        if f.get("category") != "birthday" or not f.get("date"):
            continue
        try:
            dd, mm = (int(x) for x in f["date"].split("-"))
            nxt = date(today.year, mm, dd)
            if nxt < today:
                nxt = date(today.year + 1, mm, dd)
        except ValueError:
            continue
        n = (nxt - today).days
        if n <= days:
            when = "aaj" if n == 0 else "kal" if n == 1 else f"{n} din mein ({nxt.strftime('%d %b')})"
            out.append((f.get("person") or "kisi", when, n))
    return sorted(out, key=lambda t: t[2])


def birthday_hint(text: str) -> str:
    """Used by the reminder tool: if a reminder names someone whose birthday is
    saved, add the date as a hint."""
    low = (text or "").lower()
    for f in load():
        p = (f.get("person") or "").lower()
        if f.get("category") == "birthday" and p and p in low and f.get("date"):
            return f" ({_bday_text(f)} - yaad hai.)"
    return ""
