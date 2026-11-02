r"""
core/kaka_files.py - Kaka update 18: find his own files by voice.

  parse("kal wali pdf kholo")  -> Query(words=[], types={'.pdf'}, folder=False, when=(t0,t1))
  search(q)                    -> ranked [(score, path, mtime, is_dir)]

Where it looks: Desktop, Documents, Downloads, Pictures, Videos, Music,
OneDrive (all of it, so a redirected Desktop/Documents is covered) and F:\ -
never Windows, Program Files, ProgramData, AppData, $Recycle.Bin, System
Volume Information, .git, node_modules, venvs, site-packages, __pycache__,
Kaka's own app folder or its cache.

Speed: an index (F:\Kaka\Data\Cache\file_index.json) is built in a
background thread 2 s after Kaka starts (lowest priority, capped at 300k
entries / 4 min) and refreshed every 30 min; the last index is loaded at
start so a search is a pure in-memory scan (~0.1-0.3 s).  Before the first
index exists: Windows Search (the system index) through PowerShell/ADODB,
2.5 s budget, then a 2.5 s live walk of the user folders.
Top level is stdlib only; never raises out of search/parse.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

MAX_ENTRIES = 300_000
WALK_CAP_S = 240.0
REFRESH_S = 30 * 60
INDEX_NAME = "file_index.json"

SKIP_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "appdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "msocache", "$windows.~bt", "$windows.~ws", "$winreagent", "windowsapps",
    "xboxgames", "steamapps", "node_modules", "venv", ".venv", "env",
    "site-packages", "dist-packages", "__pycache__", ".git", "config.msi",
    "anaconda3", "miniconda3",
}

TYPES = {
    "pdf": {".pdf"},
    "image": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".raw", ".cr2", ".nef"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".3gp", ".flv"},
    "excel": {".xlsx", ".xls", ".xlsm", ".csv", ".ods"},
    "word": {".docx", ".doc", ".rtf", ".odt"},
    "ppt": {".pptx", ".ppt", ".odp", ".key"},
    "audio": {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".opus"},
    "zip": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "text": {".txt", ".md", ".log"},
    "code": {".py", ".js", ".ts", ".c", ".cpp", ".h", ".java", ".html", ".css", ".ipynb", ".json"},
    "exe": {".exe", ".msi"},
}
TYPE_WORDS = {
    "pdf": "pdf", "pdfs": "pdf",
    "photo": "image", "photos": "image", "foto": "image", "image": "image", "images": "image",
    "pic": "image", "pics": "image", "picture": "image", "pictures": "image", "tasveer": "image",
    "tasvir": "image", "jpg": "image", "jpeg": "image", "png": "image", "screenshot": "image",
    "screenshots": "image",
    "video": "video", "videos": "video", "mp4": "video", "movie": "video", "film": "video",
    "excel": "excel", "xlsx": "excel", "xls": "excel", "sheet": "excel", "spreadsheet": "excel", "csv": "excel",
    "word": "word", "doc": "word", "docx": "word", "document": "word",
    "ppt": "ppt", "pptx": "ppt", "powerpoint": "ppt", "presentation": "ppt", "slides": "ppt",
    "song": "audio", "songs": "audio", "gaana": "audio", "gana": "audio", "mp3": "audio", "audio": "audio",
    "zip": "zip", "rar": "zip",
    "txt": "text", "notepad": "text",
    "python": "code", "code": "code",
    "setup": "exe", "installer": "exe",
}
FOLDER_WORDS = {"folder", "folders", "directory", "dir", "fold"}
FILLER = {
    "meri", "mera", "mere", "wali", "wala", "wale", "file", "files", "kholo", "khol",
    "kholna", "kholdo", "do", "dikhao", "dikha", "open", "karo", "kar", "kardo", "ki", "ka", "ke",
    "my", "the", "a", "an", "please", "plz", "zara", "jo", "thi", "tha", "hai", "ho", "se",
    "me", "mein", "main", "wo", "woh", "vo", "ek", "de", "dena", "chahiye", "show", "find",
    "dhundo", "dhundho", "dhoondo", "search", "naam", "name", "named", "called", "pe", "par",
    "wali", "jisme", "us", "uss", "is", "iss", "of", "to", "for", "and", "aur", "kaha", "kahan",
    "hai", "yaar", "bhai", "kaka", "computer", "pc", "laptop", "latest", "nayi", "naya",
    "new", "newest", "recent", "last", "abhi", "wali", "vali", "vala", "kholiye", "dekho",
}
TIME_WORDS = {"kal", "aaj", "aj", "parso", "parson", "yesterday", "today", "pichhle",
              "pichle", "pichla", "pichhla", "hafte", "hafta", "week", "mahine", "month",
              "is", "this", "kal ki", "subah", "raat"}
KNOWN_FOLDERS = {
    "desktop": "Desktop", "documents": "Documents", "document": "Documents", "docs": "Documents",
    "downloads": "Downloads", "download": "Downloads", "pictures": "Pictures",
    "photos": "Pictures", "photo": "Pictures", "tasveer": "Pictures", "pics": "Pictures",
    "videos": "Videos", "video": "Videos", "music": "Music", "gaane": "Music", "songs": "Music",
}

_IDX = {"paths": None, "mtimes": None, "dirs": None, "names": None, "built": 0.0,
        "complete": False, "building": False}
_IDX_LOCK = threading.Lock()
_LAST = {"choices": [], "at": 0.0}


class Query:
    def __init__(self, words, types, folder, when, raw, newest=False):
        self.words, self.types, self.folder, self.when, self.raw = words, types, folder, when, raw
        self.newest = newest
        self.file_hint = False

    def __repr__(self):
        return f"Query(words={self.words}, types={sorted(self.types or [])}, folder={self.folder}, when={self.when})"


# ---------------------------------------------------------------- OS seams
def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def roots() -> list:
    r"""Folders to search, existing only, de-duplicated (OneDrive may contain
    the redirected Desktop/Documents; F:\ may contain OneDrive)."""
    h = _home()
    cand = [h / n for n in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(env)
        if v:
            cand.append(Path(v))
    cand.append(h / "OneDrive")
    if sys.platform == "win32":
        cand.append(Path("F:/"))
    out, seen = [], set()
    for c in cand:
        try:
            if c.is_dir():
                k = os.path.normcase(os.path.abspath(str(c)))
                if k not in seen:
                    seen.add(k)
                    out.append(str(c))
        except Exception:
            pass
    return out


def skip_paths() -> set:
    s = set()
    try:
        app = Path(__file__).resolve().parent.parent          # ...\MarkLVI\app
        if app.parent.name.lower() == "marklvi":
            s.add(os.path.normcase(str(app.parent)))
        else:
            s.add(os.path.normcase(str(app)))
    except Exception:
        pass
    try:
        from core import kaka_services as ks
        s.add(os.path.normcase(str(ks.cache_dir())))
    except Exception:
        pass
    if sys.platform == "win32":
        s.add(os.path.normcase("F:\\Kaka\\MarkLVI"))
        s.add(os.path.normcase("F:\\Kaka\\Data\\Cache"))
    return s


def _open(path: str) -> bool:
    if sys.platform != "win32":
        return False
    os.startfile(path)          # opening is safe - no question (Update-13 rule)
    return True


def _now() -> float:
    return time.time()


def _cache_file() -> Path:
    try:
        from core import kaka_services as ks
        return ks.cache_dir() / INDEX_NAME
    except Exception:
        return Path.home() / "Kaka" / "Data" / "Cache" / INDEX_NAME


def _low_priority():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000)   # THREAD_MODE_BACKGROUND_BEGIN (CPU+IO)
    except Exception:
        pass


# ---------------------------------------------------------------- parsing
def _day_range(days_ago: int, span_days: int = 1, now=None):
    now = _dt.datetime.fromtimestamp(now if now is not None else _now())
    start = (now - _dt.timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=span_days)
    return (start.timestamp(), end.timestamp())


def parse(text: str, now=None) -> Query:
    raw = (text or "").strip()
    t = raw.lower()
    t = re.sub(r"[\"'`,!?()\[\]]", " ", t)
    when = None
    if re.search(r"\bparso|\bparson|day before yesterday", t):
        when = _day_range(2, now=now)
    elif re.search(r"\bkal\b|\bkalki\b|yesterday", t):
        when = _day_range(1, now=now)
    elif re.search(r"\baaj\b|\baj\b|today", t):
        when = _day_range(0, now=now)
    elif re.search(r"(pichh?le|pichh?la|last|previous) (hafte|hafta|week)", t):
        when = _day_range(7, span_days=8, now=now)
    elif re.search(r"(is|iss|this) (hafte|hafta|week)", t):
        when = _day_range(6, span_days=7, now=now)
    elif re.search(r"(pichh?le|pichh?la|last|is|this) (mahine|month)", t):
        when = _day_range(30, span_days=31, now=now)
    t = re.sub(r"(day before yesterday|(pichh?le|pichh?la|last|previous|is|iss|this) (hafte|hafta|week|mahine|month))", " ", t)
    toks = re.findall(r"[a-z0-9\u0900-\u09ff&+#]+", t)
    folder = any(w in FOLDER_WORDS for w in toks)
    file_hint = (not folder) and any(w in ("file", "files") for w in toks)
    newest = any(w in ("latest", "nayi", "naya", "new", "newest", "recent", "last") for w in toks)
    types, words = set(), []
    for w in toks:
        if w in FOLDER_WORDS or w in FILLER:
            continue
        if w in ("kal", "kalki", "aaj", "aj", "parso", "parson", "yesterday", "today", "subah", "raat", "sham", "shaam"):
            continue
        if w in TYPE_WORDS and not folder:
            types |= TYPES[TYPE_WORDS[w]]
            continue
        if re.fullmatch(r"\.?[a-z0-9]{2,5}", w) and ("." + w.lstrip(".")) in {e for s in TYPES.values() for e in s} and not folder:
            types.add("." + w.lstrip("."))
            continue
        words.append(w)
    qq = Query(words, types, folder, when, raw, newest)
    qq.file_hint = file_hint
    return qq


# ---------------------------------------------------------------- scoring
_SPLIT = re.compile(r"[\s_\-.()\[\]{}+,;]+")


def _stem_tokens(name: str):
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    s = stem.lower()
    # split camelCase and letter/digit boundaries: "Resume2026" -> resume 2026
    s2 = re.sub(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])", " ", s)
    return s, [x for x in _SPLIT.split(s2) if x]


def score(q: Query, name: str, is_dir: bool):
    """0..1.1 or None when it does not match at all."""
    nl = name.lower()
    if q.folder and not is_dir:
        return None
    if q.types and (is_dir or os.path.splitext(nl)[1] not in q.types):
        return None
    stem, toks = _stem_tokens(name)
    if not q.words:
        return 0.5                         # type/time-only query: rank by time
    cov = _coverage(q, toks)
    if cov <= 0.0:
        return None
    joined = " ".join(q.words)
    flat = " ".join(toks)
    s = cov * 0.85
    if joined in flat:
        s += 0.1
    s += 0.05 * difflib.SequenceMatcher(None, joined, flat).ratio()
    if not q.types and not q.folder and is_dir:
        s -= 0.12 if q.file_hint else 0.03  # a file is the usual ask
    return s


def _quick_hit(q: Query, nl: str) -> bool:
    if not q.words:
        return True
    for w in q.words:
        if (w[:4] if len(w) > 4 else w) in nl:
            return True
    return False


def _coverage(q: Query, toks) -> float:
    cov = 0.0
    for w in q.words:
        best = 0.0
        for tk in toks:
            if tk == w:
                best = 1.0
                break
            if tk.startswith(w) or (len(tk) >= 3 and w.startswith(tk) and len(tk) >= len(w) - 2):
                best = max(best, 0.9)
            elif len(w) >= 3 and w in tk:
                best = max(best, 0.75)
        cov += best
    return cov / max(1, len(q.words))


def rank(q: Query, entries, limit: int = 10, shortlist: int = 300):
    """entries: iterable of (path, mtime, is_dir).  -> [(score, path, mtime, is_dir)]
    best first: score, then newest.  Cheap token coverage on everything, the
    full (difflib) score only on the best `shortlist`."""
    pre = []
    lo, hi = q.when if q.when else (None, None)
    for path, mt, isd in entries:
        if lo is not None and not (lo <= mt < hi):
            continue
        name = os.path.basename(path.rstrip("\\/")) or path
        nl = name.lower()
        if not _quick_hit(q, nl):
            continue
        if q.folder and not isd:
            continue
        if q.types and (isd or os.path.splitext(nl)[1] not in q.types):
            continue
        c = _coverage(q, _stem_tokens(name)[1]) if q.words else 1.0
        if c * 0.85 + 0.15 < 0.4:
            continue
        pre.append((c, mt, path, isd, name))
    if len(pre) > shortlist:
        pre.sort(key=lambda r: (-r[0], -r[1]))
        pre = pre[:shortlist]
    out = []
    for c, mt, path, isd, name in pre:
        sc = score(q, name, bool(isd))
        if sc is None or sc < 0.4:
            continue
        out.append((round(sc, 3), path, mt, bool(isd)))
    out.sort(key=lambda r: (-r[0], -r[2]))
    return out[:limit]


_VOCAB = {"key": None, "set": None}


def _fuzzy_words(q: Query, names_iter):
    """Second pass for typos ('invoce', 'resum'): map each query word to the
    closest real name token in the index (vocabulary cached per index)."""
    key = id(names_iter)
    if _VOCAB["key"] == key and _VOCAB["set"] is not None:
        vocab = _VOCAB["set"]
    else:
        vocab = set()
        for nl in names_iter:
            vocab.update(_stem_tokens(nl)[1])
            if len(vocab) > 400_000:
                break
        _VOCAB.update(key=key, set=vocab)
    new = []
    changed = False
    for w in q.words:
        if w in vocab:
            new.append(w)
            continue
        m = difflib.get_close_matches(w, vocab, n=1, cutoff=0.75) if len(w) >= 3 else []
        if m:
            new.append(m[0])
            changed = True
        else:
            new.append(w)
    return (Query(new, q.types, q.folder, q.when, q.raw, q.newest) if changed else None)


# ---------------------------------------------------------------- index
def _walk(root_list, cap=MAX_ENTRIES, time_cap=WALK_CAP_S, skips=None, yield_every=2000):
    """Bounded os.scandir walk -> (paths, mtimes, dirs, complete)."""
    skips = skips if skips is not None else skip_paths()
    paths, mtimes, dirs = [], [], []
    seen = set()
    t0 = time.monotonic()
    stack = list(reversed(root_list))
    complete = True
    n = 0
    while stack:
        d = stack.pop()
        nd = os.path.normcase(os.path.abspath(d))
        if nd in seen or nd in skips:
            continue
        seen.add(nd)
        try:
            it = os.scandir(d)
        except Exception:
            continue
        with it:
            for e in it:
                n += 1
                if yield_every and n % yield_every == 0:
                    time.sleep(0.001)
                    if time.monotonic() - t0 > time_cap:
                        complete = False
                        stack.clear()
                        break
                if len(paths) >= cap:
                    complete = False
                    stack.clear()
                    break
                name = e.name
                try:
                    if e.is_symlink() or (getattr(e, "is_junction", None) and e.is_junction()):
                        continue
                    isd = e.is_dir(follow_symlinks=False)
                except Exception:
                    continue
                if isd:
                    ln = name.lower()
                    if ln in SKIP_NAMES or ln.startswith(".") or ln.startswith("$"):
                        continue
                    stack.append(e.path)
                try:
                    mt = int(e.stat(follow_symlinks=False).st_mtime)
                except Exception:
                    mt = 0
                paths.append(e.path)
                mtimes.append(mt)
                dirs.append(1 if isd else 0)
    return paths, mtimes, dirs, complete


def _set_index(paths, mtimes, dirs, built, complete):
    names = [os.path.basename(p).lower() for p in paths]
    with _IDX_LOCK:
        _IDX.update(paths=paths, mtimes=mtimes, dirs=dirs, names=names,
                    built=built, complete=complete)


def build_index(root_list=None, save=True) -> int:
    """Walk + save.  Returns entry count.  Never raises."""
    if _IDX["building"]:
        return -1
    _IDX["building"] = True
    t0 = time.monotonic()
    try:
        _low_priority()
        r = root_list if root_list is not None else roots()
        paths, mtimes, dirs, complete = _walk(r)
        built = _now()
        _set_index(paths, mtimes, dirs, built, complete)
        if save:
            try:
                f = _cache_file()
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"v": 1, "built": built, "complete": complete, "roots": r,
                               "paths": paths, "mtimes": mtimes,
                               "dirs": "".join("1" if x else "0" for x in dirs)},
                              fh, ensure_ascii=False, separators=(",", ":"))
                tmp.replace(f)
            except Exception as e:
                _log(f"SYS: file index save failed: {e}")
        _timing("file_index build", time.monotonic() - t0, f"{len(paths)} items{'' if complete else ', capped'}")
        return len(paths)
    except Exception as e:
        _log(f"SYS: file index build failed: {e}")
        return 0
    finally:
        _IDX["building"] = False


def load_index() -> bool:
    try:
        f = _cache_file()
        if not f.exists():
            return False
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        paths, mtimes, dirs = d["paths"], d["mtimes"], [c == "1" for c in d["dirs"]]
        if not (len(paths) == len(mtimes) == len(dirs)):
            return False
        _set_index(paths, mtimes, dirs, float(d.get("built") or 0), bool(d.get("complete")))
        return True
    except Exception:
        return False


def index_ready() -> bool:
    return _IDX["paths"] is not None


def _index_loop(delay=20.0):
    try:
        # json.load of a big index holds the GIL ~0.5 s - keep it away from
        # Kaka's greeting audio right after start.
        time.sleep(delay)
        if not index_ready():
            load_index()
        _warm_vocab()
        while True:
            age = _now() - (_IDX["built"] or 0)
            if not index_ready() or age >= REFRESH_S:
                build_index()
                _warm_vocab()
                time.sleep(REFRESH_S)
            else:
                time.sleep(max(60.0, REFRESH_S - age))
    except Exception as e:
        _log(f"SYS: file index loop stopped: {e}")


def _warm_vocab():
    """Build the typo vocabulary now (background) so a first miss stays fast."""
    try:
        names = _IDX["names"]
        if names is not None:
            _fuzzy_words(Query(["zzzz"], set(), False, None, ""), names)
    except Exception:
        pass


def start_background(delay: float = 20.0):
    threading.Thread(target=_index_loop, args=(delay,), name="kaka-u18-fileindex", daemon=True).start()


def ensure_building():
    if not _IDX["building"]:
        threading.Thread(target=build_index, name="kaka-u18-fileindex-now", daemon=True).start()


# ---------------------------------------------------------------- fallbacks
def _ps_escape(s: str) -> str:
    return s.replace("'", "''")


def windows_search_script(q: Query, root_list, top: int = 200) -> str:
    """ADODB query against the Windows Search index (SystemIndex)."""
    conds = []
    for w in q.words:
        w2 = re.sub(r"[^\w\- ]", "", w)
        if w2:
            conds.append(f"System.FileName LIKE '%{_ps_escape(w2)}%'")
    if q.folder:
        conds.append("System.ItemType = 'Directory'")
    elif q.types:
        ors = " OR ".join(f"System.FileExtension = '{_ps_escape(e)}'" for e in sorted(q.types))
        conds.append(f"({ors})")
    if q.when:
        _u = _dt.timezone.utc
        a = _dt.datetime.fromtimestamp(q.when[0], _u).strftime("%Y-%m-%d %H:%M:%S")
        b = _dt.datetime.fromtimestamp(q.when[1], _u).strftime("%Y-%m-%d %H:%M:%S")
        conds.append(f"System.DateModified >= '{a}' AND System.DateModified < '{b}'")
    scopes = " OR ".join(f"SCOPE='file:{_ps_escape(r.replace(chr(92), '/'))}'" for r in root_list)
    if scopes:
        conds.append(f"({scopes})")
    where = " AND ".join(conds) or "1=1"
    sql = (f"SELECT TOP {int(top)} System.ItemPathDisplay, System.DateModified, System.ItemType "
           f"FROM SystemIndex WHERE {where} ORDER BY System.DateModified DESC")
    return "\n".join([
        "$ErrorActionPreference='Stop'",
        "try {",
        "  $c = New-Object -ComObject ADODB.Connection",
        "  $c.Open(\"Provider=Search.CollatorDSO;Extended Properties='Application=Windows';\")",
        "  $rs = New-Object -ComObject ADODB.Recordset",
        f"  $rs.Open(\"{sql.replace(chr(34), chr(96) + chr(34))}\", $c)",
        "  $e = [datetime]'1970-01-01'   # Windows Search dates are UTC already",
        "  while (-not $rs.EOF) {",
        "    $p = $rs.Fields.Item(0).Value; $d = $rs.Fields.Item(1).Value; $t = $rs.Fields.Item(2).Value",
        "    $s = 0; if ($d) { $s = [int64]([datetime]$d - $e).TotalSeconds }",
        "    Write-Output ('R|' + $s + '|' + $t + '|' + $p)",
        "    $rs.MoveNext()",
        "  }",
        "  $rs.Close(); $c.Close(); Write-Output 'END'",
        "} catch { Write-Output ('ERR|' + $_.Exception.Message) }",
    ])


def parse_windows_search(out: str):
    rows, ok = [], False
    for ln in (out or "").splitlines():
        ln = ln.rstrip("\r")
        if ln == "END":
            ok = True
        elif ln.startswith("R|"):
            parts = ln.split("|", 3)
            if len(parts) == 4 and parts[3]:
                try:
                    mt = int(parts[1])
                except Exception:
                    mt = 0
                rows.append((parts[3], mt, parts[2].strip().lower() in ("directory", "file folder", "folder")))
    return rows, ok


def windows_search(q: Query, root_list, timeout=2.5):
    if sys.platform != "win32":
        return None
    try:
        from core.kaka_pc import _run_ps
    except Exception:
        return None
    rc, out, err = _run_ps(windows_search_script(q, root_list), timeout=timeout)
    rows, ok = parse_windows_search(out)
    return rows if ok else None


def live_walk(root_list, time_cap=2.5):
    user = [r for r in root_list if not re.fullmatch(r"[A-Za-z]:[\\/]?", r)]
    paths, mtimes, dirs, _c = _walk(user, cap=150_000, time_cap=time_cap, yield_every=500)
    return list(zip(paths, mtimes, dirs))


# ---------------------------------------------------------------- top level
def _known_folder(q: Query):
    if q.when:
        return None
    if not q.words and q.types:
        # "photos kholo" / "videos kholo": the folder, not the newest photo
        raw = re.findall(r"[a-z]+", q.raw.lower())
        hits = [w for w in raw if w in KNOWN_FOLDERS]
        if len(hits) != 1:
            return None
        w = hits[0]
        q = Query([w], set(), True, None, q.raw)
    if len(q.words) != 1 or q.types:
        return None
    w = q.words[0]
    if w not in KNOWN_FOLDERS:
        return None
    if not q.folder and w not in ("desktop", "downloads", "documents", "pictures"):
        return None
    name = KNOWN_FOLDERS[w]
    for env in ("OneDrive", "OneDriveConsumer"):
        v = os.environ.get(env)
        if v and name in ("Desktop", "Documents", "Pictures") and os.path.isdir(os.path.join(v, name)):
            h = os.path.join(str(_home()), name)
            if not os.path.isdir(h) or not os.listdir(h):
                return os.path.join(v, name)
    p = os.path.join(str(_home()), name)
    return p if os.path.isdir(p) else None


def search(q: Query, limit: int = 10, wait_s: float = 2.5):
    """-> (results, source).  source: index | windows-search | live-walk | none"""
    if index_ready():
        with _IDX_LOCK:
            paths, mtimes, dirs, names = _IDX["paths"], _IDX["mtimes"], _IDX["dirs"], _IDX["names"]
        res = rank(q, zip(paths, mtimes, dirs), limit)
        if not res and q.words:
            q2 = _fuzzy_words(q, names)
            if q2 is not None:
                res = rank(q2, zip(paths, mtimes, dirs), limit)
        return res, "index"
    ensure_building()
    r = roots()
    rows = windows_search(q, r, timeout=wait_s)
    if rows:
        res = rank(q, rows, limit)
        if res:
            return res, "windows-search"
    rows = live_walk(r, time_cap=wait_s)
    res = rank(q, rows, limit)
    return res, ("live-walk" if res else "none")


def remember(choices):
    _LAST["choices"] = list(choices)
    _LAST["at"] = _now()


def last_choices():
    return list(_LAST["choices"])


def _log(m):
    try:
        from core import kaka_services as ks
        ks.log(m)
    except Exception:
        pass


def _timing(label, secs, extra=""):
    try:
        from core import kaka_services as ks
        ks.timing(label, secs, extra)
    except Exception:
        pass




def type_label(q: Query) -> str:
    if q.folder:
        return "folder"
    if not q.types:
        return "file"
    names = {"pdf": "pdf", "image": "photo", "video": "video", "excel": "excel file",
             "word": "word file", "ppt": "ppt", "audio": "gaana/audio", "zip": "zip",
             "text": "text file", "code": "code file", "exe": "setup file"}
    for k, v in TYPES.items():
        if q.types == v:
            return names.get(k, "file")
    return "file"
