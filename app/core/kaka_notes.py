r"""
core/kaka_notes.py - Kaka update 19: his notes (to-do / jot list).

Stored in F:\Kaka\Data\notes.json (ks.data_path("notes.json")). Display order
is newest first; note number n = position n in items() (1-based). Thread-safe,
never raises: a bad or missing file is an empty list (an unreadable file is
kept as notes.json.bak). Listeners registered with on_change() are called
after every change, outside the lock, exceptions swallowed.
"""
from __future__ import annotations

import difflib
import shutil
import threading
import uuid
from datetime import datetime

MAX_NOTES = 200
MAX_TEXT = 500

_LOCK = threading.RLock()
_STATE = {"items": None}          # list, oldest first, loaded lazily
_LISTENERS: list = []


def _ks():
    from core import kaka_services as ks
    return ks


def _path():
    return _ks().data_path("notes.json")


def _load() -> list:
    """Caller holds the lock."""
    if _STATE["items"] is not None:
        return _STATE["items"]
    items: list = []
    try:
        p = _path()
        if p.exists():
            import json
            raw = None
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                try:
                    shutil.copyfile(str(p), str(p) + ".bak")
                except Exception:
                    pass
            if isinstance(raw, dict):
                raw = raw.get("notes")
            if isinstance(raw, list):
                for it in raw:
                    if isinstance(it, dict) and str(it.get("text") or "").strip():
                        items.append({
                            "id": str(it.get("id") or uuid.uuid4().hex[:10]),
                            "text": str(it["text"]).strip()[:MAX_TEXT],
                            "done": bool(it.get("done")),
                            "at": str(it.get("at") or ""),
                        })
    except Exception:
        items = []
    _STATE["items"] = items[-MAX_NOTES:]
    return _STATE["items"]


def _save() -> None:
    """Caller holds the lock."""
    try:
        _ks().write_json(_path(), {"version": 1, "notes": _STATE["items"] or []})
    except Exception:
        pass


def _notify() -> None:
    for cb in list(_LISTENERS):
        try:
            cb()
        except Exception:
            pass


def reload() -> None:
    """Forget the in-memory copy (tests / file changed outside)."""
    with _LOCK:
        _STATE["items"] = None


def on_change(cb) -> None:
    if cb is not None and cb not in _LISTENERS:
        _LISTENERS.append(cb)


def off_change(cb) -> None:
    try:
        _LISTENERS.remove(cb)
    except ValueError:
        pass


def items() -> list:
    """Newest first (display order). Copies."""
    try:
        with _LOCK:
            return [dict(i) for i in reversed(_load())]
    except Exception:
        return []


def count() -> int:
    return len(items())


def add(text: str) -> dict:
    text = " ".join(str(text or "").split())[:MAX_TEXT]
    if not text:
        return {}
    item = {"id": uuid.uuid4().hex[:10], "text": text, "done": False,
            "at": datetime.now().isoformat(timespec="seconds")}
    try:
        with _LOCK:
            lst = _load()
            lst.append(item)
            if len(lst) > MAX_NOTES:
                # drop the oldest done notes first, then the oldest
                extra = len(lst) - MAX_NOTES
                done = [i for i in lst if i.get("done")][:extra]
                for d in done:
                    lst.remove(d)
                del lst[:max(0, len(lst) - MAX_NOTES)]
            _save()
    except Exception:
        pass
    _notify()
    return dict(item)


def _norm(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in str(s or "")).split())


def find(index: int = 0, query: str = "") -> list:
    """Matches (copies) without removing: by display number, else by words."""
    lst = items()
    try:
        index = int(index or 0)
    except Exception:
        index = 0
    if index:
        return [lst[index - 1]] if 1 <= index <= len(lst) else []
    q = _norm(query)
    if not q:
        return []
    exact = [i for i in lst if _norm(i["text"]) == q]
    if exact:
        return exact
    sub = [i for i in lst if q in _norm(i["text"])]
    if sub:
        return sub
    words = [w for w in q.split() if len(w) > 2]
    if words:
        hits = [i for i in lst if all(w in _norm(i["text"]) for w in words)]
        if hits:
            return hits
    scored = []
    for i in lst:
        r = difflib.SequenceMatcher(None, q, _norm(i["text"])).ratio()
        if r >= 0.6:
            scored.append((r, i))
    scored.sort(key=lambda t: -t[0])
    return [i for _, i in scored]


def _resolve(key) -> str:
    """id string or display number -> id ('' if none). Caller may hold the lock."""
    if isinstance(key, bool):
        return ""
    if isinstance(key, int) or (isinstance(key, str) and key.strip().isdigit() and len(key.strip()) < 5):
        lst = items()
        n = int(key)
        return lst[n - 1]["id"] if 1 <= n <= len(lst) else ""
    return str(key or "")


def remove(index: int = 0, query: str = "", ids=None) -> list:
    """Remove by display number, words, or explicit ids. Returns removed dicts."""
    try:
        if ids:
            want = set(str(i) for i in ids)
        else:
            want = set(i["id"] for i in find(index, query))
        if not want:
            return []
        with _LOCK:
            lst = _load()
            gone = [dict(i) for i in lst if i["id"] in want]
            lst[:] = [i for i in lst if i["id"] not in want]
            if gone:
                _save()
    except Exception:
        return []
    if gone:
        _notify()
    return gone


def set_done(key, done: bool = True) -> dict:
    """key = note id or display number. Returns the changed note ({} if none)."""
    try:
        nid = _resolve(key)
        out = {}
        with _LOCK:
            for i in _load():
                if i["id"] == nid:
                    i["done"] = bool(done)
                    out = dict(i)
                    _save()
                    break
    except Exception:
        return {}
    if out:
        _notify()
    return out


def toggle_done(key) -> dict:
    nid = _resolve(key)
    cur = [i for i in items() if i["id"] == nid]
    if not cur:
        return {}
    return set_done(nid, not cur[0]["done"])


def clear_done() -> int:
    try:
        with _LOCK:
            lst = _load()
            n = len([i for i in lst if i.get("done")])
            if n:
                lst[:] = [i for i in lst if not i.get("done")]
                _save()
    except Exception:
        return 0
    if n:
        _notify()
    return n
