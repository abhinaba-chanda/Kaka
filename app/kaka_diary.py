"""KAKA v1.1: diary - "diary mein likho ...", "kal kya likha tha?",
"diary padho", "diary mein biryani dhoondo", "kal wali entry delete karo".

Dated entries in Data/diary.json (local only). His quick to-do notes stay in
the notes panel (core/kaka_notes.py); this is the dated journal. Search by
date (aaj / kal / parso / 5 November / 2026-11-03) or keyword. Deleting
always asks first (actions/kaka_diary_tool.py). Nothing here raises.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

MAX_ENTRIES = 5000
_LOCK = threading.RLock()


def _path() -> Path:
    try:
        from core import kaka_services as ks
        return ks.data_path("diary.json")
    except Exception:
        p = Path.home() / "Kaka" / "Data" / "diary.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def load() -> list:
    with _LOCK:
        try:
            d = json.loads(_path().read_text(encoding="utf-8"))
            e = d.get("entries") if isinstance(d, dict) else None
            return e if isinstance(e, list) else []
        except Exception:
            return []


def _save(entries: list) -> bool:
    with _LOCK:
        try:
            p = _path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "entries": entries[-MAX_ENTRIES:]},
                                      indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
            return True
        except Exception:
            return False


def add(text: str, now: Optional[datetime] = None) -> Optional[dict]:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return None
    now = now or datetime.now()
    e = {"id": uuid.uuid4().hex[:8], "date": now.date().isoformat(),
         "time": now.strftime("%H:%M"), "text": t[:4000]}
    with _LOCK:
        entries = load()
        entries.append(e)
        if not _save(entries):
            return None
    return e


def parse_when(when: str, today: Optional[date] = None) -> Optional[date]:
    today = today or date.today()
    w = (when or "").strip().lower()
    if not w:
        return None
    if w in ("aaj", "today", "abhi", "aaj ka", "aaj ki"):
        return today
    if w in ("kal", "yesterday", "kal ka", "kal ki", "kal wali", "kal wala"):
        return today - timedelta(days=1)
    if w in ("parso", "parson", "day before yesterday"):
        return today - timedelta(days=2)
    m = re.search(r"(\d+)\s*(din|days?)", w)
    if m:
        return today - timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(w[:10])
    except Exception:
        pass
    try:
        from core import kaka_facts as F
        dm = F.parse_date(w)
    except Exception:
        dm = None
    if dm:
        dd, mm = int(dm[:2]), int(dm[3:5])
        try:
            cand = date(today.year, mm, dd)
        except ValueError:
            return None
        if cand > today:
            cand = date(today.year - 1, mm, dd)
        return cand
    return None


def on_date(d: date) -> list:
    k = d.isoformat()
    return [e for e in load() if e.get("date") == k]


def search(query: str = "", when: str = "", today: Optional[date] = None) -> list:
    """Entries matching a date and/or keywords (all words), newest last."""
    entries = load()
    d = parse_when(when, today) if when else None
    if d is not None:
        entries = [e for e in entries if e.get("date") == d.isoformat()]
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    if words:
        entries = [e for e in entries if all(w in e.get("text", "").lower() for w in words)]
    return entries


def recent(n: int = 5) -> list:
    return load()[-n:]


def remove(ids) -> int:
    ids = set(ids or [])
    if not ids:
        return 0
    with _LOCK:
        entries = load()
        keep = [e for e in entries if e.get("id") not in ids]
        n = len(entries) - len(keep)
        if n and not _save(keep):
            return 0
    return n


def fmt(e: dict, today: Optional[date] = None) -> str:
    today = today or date.today()
    try:
        d = date.fromisoformat(e["date"])
        tag = ("aaj" if d == today else "kal" if d == today - timedelta(days=1)
               else d.strftime("%d %b"))
    except Exception:
        tag = e.get("date", "")
    return f"{tag} {e.get('time', '')}: {e.get('text', '')}".strip()


def clear_all() -> bool:
    return _save([])
