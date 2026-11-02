r"""
actions/find_file.py - Kaka update 18: "meri resume file kholo", "kal wali
pdf kholo", "photos folder kholo", "budget excel dikhao", "2 number kholo".
Searches HIS files (Desktop, Documents, Downloads, Pictures, Videos, Music,
OneDrive, F:\) - core/kaka_files.py does the index and ranking.
Opening is safe, so no question (Update-13); when several files match about
equally it reads the top 3 and he picks a number.  Never raises.
"""
from __future__ import annotations

import os
import time

from core import kaka_services as ks


def _start_index():
    from core import kaka_files as kf
    kf.start_background()


ks.on_start(_start_index)


def _when(mt: float) -> str:
    import datetime as dt
    try:
        d = dt.datetime.fromtimestamp(mt).date()
        today = dt.date.today()
        if d == today:
            return "aaj"
        if d == today - dt.timedelta(days=1):
            return "kal"
        return d.strftime("%d %b %Y").lstrip("0")
    except Exception:
        return ""


def _label(path: str, mt: float) -> str:
    name = os.path.basename(path.rstrip("\\/")) or path
    parent = os.path.basename(os.path.dirname(path.rstrip("\\/"))) or os.path.dirname(path)
    w = _when(mt)
    return f"{name} ({parent}{', ' + w if w else ''})"


def _open(kf, path: str):
    try:
        if kf._open(path):
            return True, ""
        return False, "ye sirf Windows pe khulta hai"
    except Exception as e:
        return False, str(e)[:100]


def find_file(parameters: dict = None, player=None) -> str:
    t0 = time.monotonic()
    out, src = "", ""
    try:
        out, src = _run(parameters or {})
    except Exception as e:
        out = f"File dhundhne mein dikkat: {str(e)[:120]}"
    ks.timing("find_file", time.monotonic() - t0, src)
    return out


def _run(p: dict):
    from core import kaka_files as kf
    action = str(p.get("action") or "open").strip().lower()

    if action in ("open_choice", "choice", "pick"):
        ch = kf.last_choices()
        try:
            i = int(str(p.get("index") or p.get("query") or "0").strip().split()[0])
        except Exception:
            i = 0
        if not ch:
            return "Abhi koi list nahi hai - pehle file ka naam bolo.", "choice"
        if not (1 <= i <= len(ch)):
            return f"1 se {len(ch)} ke beech number bolo.", "choice"
        path, mt = ch[i - 1]
        ok, why = _open(kf, path)
        name = os.path.basename(path.rstrip("\\/"))
        return (f"Khol diya: {name}." if ok else f"{name} khul nahi payi: {why}"), "choice"

    if action == "refresh":
        kf.ensure_building()
        return "File list background mein dobara bana raha hoon (1-2 min).", "refresh"

    text = " ".join(str(p.get(k) or "") for k in ("query", "type", "when")).strip()
    if not text:
        return "Kaunsi file? Naam ya type bolo (jaise 'resume pdf').", "none"
    q = kf.parse(text)

    kfold = kf._known_folder(q)
    if kfold and action != "list":
        ok, why = _open(kf, kfold)
        kf.remember([(kfold, 0)])
        nm = os.path.basename(kfold)
        return (f"{nm} folder khol diya." if ok else f"{nm} folder khul nahi paya: {why}"), "known-folder"

    if not q.words and not q.types and not q.when and not q.folder:
        return "Kaunsi file? Naam ya type bolo (jaise 'resume pdf').", "none"

    res, src = kf.search(q, limit=10)
    if not res:
        what = " ".join(q.words)
        kind = kf.type_label(q)
        day = ""
        if q.when:
            import datetime as dt
            ago = (dt.date.today() - dt.date.fromtimestamp(q.when[0] + 1)).days
            day = {0: "aaj ki ", 1: "kal ki ", 2: "parso ki "}.get(ago, "us waqt ki ")
            # did something match on another day?
            q2 = kf.Query(q.words, q.types, q.folder, None, q.raw)
            other, _s = kf.search(q2, limit=3)
            if other:
                kf.remember([(r[1], r[2]) for r in other[:3]])
                return (f"{day}koi {(what + ' ') if what else ''}{kind} nahi mili. Sabse nayi: {_label(other[0][1], other[0][2])}"
                        " - kholni ho toh '1 number kholo' bolo."), src
        where = "Desktop, Documents, Downloads, Pictures, OneDrive aur F: drive"
        note = "" if kf.index_ready() else " (file list abhi ban rahi hai, 1-2 min mein dobara try karna)"
        return f"{day}{(repr(what) + ' ') if what else ''}{kind} nahi mili - {where} mein dekha{note}.", src

    top = res[0][0]
    band = [r for r in res if r[0] >= top - 0.05]
    if action == "list" or (len(band) > 1 and q.words):
        pick = sorted(band if len(band) > 1 else res, key=lambda r: -r[2])[:3]
        kf.remember([(r[1], r[2]) for r in pick])
        lines = " ".join(f"{i}) {_label(r[1], r[2])}" for i, r in enumerate(pick, 1))
        if len(pick) == 1:
            return f"Mili: {lines}. 'Kholo' bolo toh khol doon.", src
        return f"{len(pick)} milti-julti mili: {lines}. Kaunsi kholun? Number bolo.", src

    # one clear winner, or a type/time-only ask ("kal wali pdf") -> newest
    if not q.words:
        res = sorted(res, key=lambda r: -r[2])
    best = res[0]
    kf.remember([(r[1], r[2]) for r in res[:3]])
    ok, why = _open(kf, best[1])
    if not ok:
        return f"Mili {_label(best[1], best[2])}, par khul nahi payi: {why}", src
    more = ""
    if len(res) > 1 and not q.words:
        more = f" Aur {min(len(res), 10) - 1} aur bhi hain - '2 number kholo' bolo toh agli."
    return f"Khol diya: {_label(best[1], best[2])}.{more}", src


TOOL = {
    "name": "find_file",
    "description": (
        "Find and OPEN the user's own file or folder by name/type/day: 'meri resume file kholo', "
        "'kal wali pdf kholo', 'photos folder kholo', 'budget excel dikhao', 'Didi ki shaadi wali "
        "photo'. Searches Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive and F:. "
        "Opens the best match without asking; if several match it lists 3 - then when he says "
        "'2 number kholo' call action=open_choice index=2. Use this (not file_controller, not "
        "open_app) whenever he wants to open/find one of HIS files or folders."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["open", "list", "open_choice", "refresh"],
                       "description": "open (default) = find + open best; list = only tell matches; "
                                      "open_choice = open number from the last list; refresh = rebuild file list."},
            "query": {"type": "STRING",
                      "description": "His words about the file, as said (e.g. 'kal wali resume pdf', 'photos folder')."},
            "index": {"type": "INTEGER", "description": "For open_choice: the number he picked (1-3)."},
        },
        "required": [],
    },
    "handler": find_file,
}
