"""KAKA v1.1: Kaka remembers what you talked about ("kal kya bola tha?").

  * Every finished exchange (your words + Kaka's answer, the same lines the
    HUD log shows) is appended to Data/Conversations/YYYY-MM-DD.jsonl.
  * Once a day (at the first start, in the background) each earlier day that
    has a log but no summary gets a short summary from Gemini (your existing
    key) -> Data/Conversations/summaries.json. If Gemini is not reachable a
    plain local summary (first lines of the day) is kept instead.
  * The last few day-summaries go into Kaka's instructions at session start,
    so "kal kya baat hui thi?" just works; kaka_memory action=day reads them.
  * Raw logs older than KEEP_DAYS are deleted; summaries are kept (max 400).

Everything stays on this PC under KAKA_DATA. Nothing here raises.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

KEEP_DAYS = 30
MAX_SUMMARIES = 400
PROMPT_DAYS = 3
PROMPT_CHARS = 1200
_LOCK = threading.RLock()


def _ks():
    from core import kaka_services as ks
    return ks


def _dir() -> Path:
    try:
        p = _ks().data_path("Conversations", "x").parent
    except Exception:
        p = Path.home() / "Kaka" / "Data" / "Conversations"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def enabled() -> bool:
    try:
        import kaka_user
        return bool(kaka_user.get_setting("daylog_enabled", True))
    except Exception:
        return True


def _day_file(d: date) -> Path:
    return _dir() / f"{d.isoformat()}.jsonl"


def add(who: str, text: str, now: Optional[datetime] = None) -> bool:
    """Append one line (who = 'user' | 'kaka'). Cheap; never raises."""
    try:
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        if not t or not enabled():
            return False
        now = now or datetime.now()
        rec = {"t": now.strftime("%H:%M"), "who": "user" if who == "user" else "kaka",
               "text": t[:1500]}
        with _LOCK:
            with open(_day_file(now.date()), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_day(d: date) -> list:
    out = []
    try:
        with _LOCK:
            lines = _day_file(d).read_text(encoding="utf-8").splitlines()
        for ln in lines:
            try:
                r = json.loads(ln)
                if isinstance(r, dict) and r.get("text"):
                    out.append(r)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _sum_path() -> Path:
    return _dir() / "summaries.json"


def load_summaries() -> dict:
    try:
        with _LOCK:
            d = json.loads(_sum_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_summaries(d: dict) -> bool:
    try:
        keys = sorted(d)[-MAX_SUMMARIES:]
        d = {k: d[k] for k in keys}
        with _LOCK:
            p = _sum_path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        return True
    except Exception:
        return False


def _transcript(recs: list, limit: int = 12000) -> str:
    lines = [f"{r.get('t', '')} {'User' if r.get('who') == 'user' else 'Kaka'}: {r['text']}"
             for r in recs]
    s = "\n".join(lines)
    return s[-limit:]


def local_summary(recs: list) -> str:
    """No-network fallback: what he asked, in a few words each."""
    asks = [r["text"] for r in recs if r.get("who") == "user"]
    if not asks:
        return ""
    picks = asks[:6]
    s = "; ".join(a[:80] for a in picks)
    more = len(asks) - len(picks)
    return f"Baatein: {s}" + (f" (+{more} aur)" if more > 0 else "")


SUMMARY_SYSTEM = (
    "Summarise this day's conversation between a user and his PC assistant Kaka in "
    "3-5 short Roman-Hinglish sentences: what he asked or talked about, decisions, "
    "names, dates, plans, anything he may ask about later ('kal kya bola tha'). "
    "No markdown, no lists. Never invent anything."
)


def summarise_day(d: date, ai=None) -> str:
    recs = read_day(d)
    if not recs:
        return ""
    txt = ""
    try:
        if ai is None:
            from core import kaka_ai as ai
        ok, out = ai.text(_transcript(recs), timeout=25.0, system=SUMMARY_SYSTEM)
        if ok and out and len(out.strip()) > 10:
            txt = re.sub(r"\s+", " ", out).strip()[:800]
    except Exception:
        txt = ""
    if not txt:
        txt = local_summary(recs)
    if txt:
        s = load_summaries()
        s[d.isoformat()] = {"summary": txt, "lines": len(recs),
                            "made": datetime.now().isoformat(timespec="seconds")}
        _save_summaries(s)
    return txt


def pending_days(today: Optional[date] = None) -> list:
    today = today or date.today()
    have = load_summaries()
    out = []
    try:
        for p in sorted(_dir().glob("????-??-??.jsonl")):
            try:
                d = date.fromisoformat(p.stem)
            except Exception:
                continue
            if d < today and d.isoformat() not in have:
                out.append(d)
    except Exception:
        pass
    return out


def prune(today: Optional[date] = None) -> int:
    today = today or date.today()
    n = 0
    try:
        for p in _dir().glob("????-??-??.jsonl"):
            try:
                d = date.fromisoformat(p.stem)
            except Exception:
                continue
            if (today - d).days > KEEP_DAYS and d.isoformat() in load_summaries():
                p.unlink()
                n += 1
    except Exception:
        pass
    return n


def run_daily(today: Optional[date] = None, ai=None) -> int:
    """Summarise every finished day that has none yet (newest 7). -> count."""
    n = 0
    for d in pending_days(today)[-7:]:
        try:
            if summarise_day(d, ai=ai):
                n += 1
        except Exception:
            pass
    prune(today)
    return n


def _parse_when(when: str, today: date) -> Optional[date]:
    w = (when or "").strip().lower()
    if w in ("", "aaj", "today", "abhi"):
        return today
    if w in ("kal", "yesterday", "kal ka", "kal ki", "pichle din"):
        return today - timedelta(days=1)
    if w in ("parso", "parson", "day before yesterday"):
        return today - timedelta(days=2)
    m = re.search(r"(\d+)\s*(din|days?)\s*(pehle|ago|purani)?", w)
    if m:
        return today - timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(w[:10])
    except Exception:
        pass
    try:
        from core import kaka_facts as F
        dm = F.parse_date(w)
        if dm:
            dd, mm = int(dm[:2]), int(dm[3:5])
            cand = date(today.year, mm, dd)
            if cand > today:
                cand = date(today.year - 1, mm, dd)
            return cand
    except Exception:
        pass
    return None


def recall(when: str = "kal", today: Optional[date] = None) -> str:
    """Text for 'kal kya bola tha' (summary, or today's lines)."""
    today = today or date.today()
    d = _parse_when(when, today)
    if d is None:
        return "Kaunsa din, wo samajh nahi aaya (kal / parso / 5 November bolo)."
    label = "Aaj" if d == today else ("Kal" if d == today - timedelta(days=1) else d.strftime("%d %b"))
    if d == today:
        recs = read_day(d)
        if not recs:
            return "Aaj abhi tak koi baat log nahi hui."
        return f"{label} ab tak ki baatein: " + local_summary(recs)
    s = load_summaries().get(d.isoformat())
    if not s and read_day(d):
        txt = summarise_day(d)
        return f"{label} ki baatein: {txt}" if txt else f"{label} ki baatein mil nahi payi."
    if not s:
        return f"{label} ki koi baat save nahi hai (us din Kaka se baat nahi hui, ya memory clear thi)."
    return f"{label} ki baatein: {s['summary']}"


def prompt_block(today: Optional[date] = None) -> str:
    today = today or date.today()
    s = load_summaries()
    keys = [k for k in sorted(s) if k < today.isoformat()][-PROMPT_DAYS:]
    if not keys:
        return ""
    lines = []
    for k in reversed(keys):
        d = date.fromisoformat(k)
        tag = "Kal" if d == today - timedelta(days=1) else d.strftime("%d %b")
        lines.append(f"- {tag} ({k}): {s[k].get('summary', '')}")
    body = "\n".join(lines)[:PROMPT_CHARS]
    return ("[RECENT DAYS - what you and the user talked about; use it when he asks "
            "'kal kya bola tha' or refers back]\n" + body + "\n")


def clear_all() -> int:
    """Delete every log and summary (Settings > CLEAR). -> files removed."""
    n = 0
    with _LOCK:
        for p in list(_dir().glob("????-??-??.jsonl")) + [_sum_path()]:
            try:
                if p.exists():
                    p.unlink()
                    n += 1
            except Exception:
                pass
    return n
