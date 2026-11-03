r"""KAKA v1.1: "junk saaf karo" - measure first, ASK, then clean.

Places (Windows):
  * your TEMP folder (%TEMP%) and C:\Windows\Temp  - files older than 1 day
  * the Recycle Bin                                - emptied through Windows
  * browser caches (Chrome, Edge, Brave, Opera, Firefox) - cache folders only,
    never history, passwords, cookies or bookmarks; skipped while that
    browser is open
Never touched: anything whose path has Python, uv, .venv / venv, pip, Git,
Kaka or conda in it (so your Kaka install and its Python stay safe), and
nothing outside the folders above.

scan() only measures. clean() deletes and is called ONLY after the user
said yes (actions/kaka_pcdoctor.py asks first - his rule).
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

MIN_AGE_S = 24 * 3600
PROTECT_RE = re.compile(r"(python|(^|[\\/._-])uv([\\/._-]|$)|venv|(^|[\\/])pip|git|kaka|conda|"
                        r"site-packages)", re.I)
_LAST = {"scan": None, "at": 0.0}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def protected(path) -> bool:
    return bool(PROTECT_RE.search(str(path)))


def _browser_caches() -> list:
    """[(label, Path, process_names)] that exist."""
    la = Path(_env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    ra = Path(_env("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    out = []
    chromium = [("Chrome", la / "Google" / "Chrome" / "User Data", ("chrome.exe",)),
                ("Edge", la / "Microsoft" / "Edge" / "User Data", ("msedge.exe",)),
                ("Brave", la / "BraveSoftware" / "Brave-Browser" / "User Data", ("brave.exe",)),
                ("Opera", ra / "Opera Software" / "Opera Stable", ("opera.exe",))]
    for label, root, procs in chromium:
        if not root.is_dir():
            continue
        profiles = [root] + [p for p in root.iterdir() if p.is_dir() and
                             (p.name == "Default" or p.name.startswith("Profile "))]
        for prof in profiles:
            for sub in ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage"):
                c = prof / sub
                if c.is_dir():
                    out.append((label, c, procs))
    ff = la / "Mozilla" / "Firefox" / "Profiles"
    if ff.is_dir():
        for prof in ff.iterdir():
            c = prof / "cache2"
            if c.is_dir():
                out.append(("Firefox", c, ("firefox.exe",)))
    return out


def targets() -> list:
    """[{label, path, kind, procs}] - only folders that exist."""
    t = []
    tmp = _env("TEMP") or _env("TMP")
    if tmp and Path(tmp).is_dir():
        t.append({"label": "Temp files (aapka TEMP)", "path": Path(tmp), "kind": "temp", "procs": ()})
    win = Path(_env("SystemRoot", r"C:\Windows")) / "Temp"
    if sys.platform == "win32" and win.is_dir():
        t.append({"label": "Windows Temp", "path": win, "kind": "temp", "procs": ()})
    for label, path, procs in _browser_caches():
        t.append({"label": f"{label} cache", "path": path, "kind": "cache", "procs": procs})
    return t


def _running(procs) -> bool:
    if not procs:
        return False
    try:
        import psutil
        names = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
        return any(n.lower() in names for n in procs)
    except Exception:
        return False


def _rel(root: Path, p) -> str:
    """The part of p below root (the protection check never looks at the root
    itself, so a user name like 'Digital' does not hide the whole TEMP)."""
    try:
        return str(Path(p).relative_to(root))
    except Exception:
        return str(p)


def _walk_files(root: Path, kind: str, now: float):
    """Yield (path, size) of deletable files under root (protected skipped)."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames
                       if not protected(_rel(root, Path(dirpath) / d))
                       and not os.path.islink(os.path.join(dirpath, d))]
        if Path(dirpath) != root and protected(_rel(root, dirpath)):
            continue
        for fn in filenames:
            p = Path(dirpath) / fn
            if protected(_rel(root, p)):
                continue
            try:
                st = p.lstat()
            except Exception:
                continue
            if kind == "temp" and now - st.st_mtime < MIN_AGE_S:
                continue
            yield p, st.st_size


def recycle_bin_size() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                        ("i64NumItems", ctypes.c_longlong)]
        info = SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return int(info.i64Size)
    except Exception:
        pass
    return None


def empty_recycle_bin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
        return r in (0, -2147418113)        # S_OK, or E_UNEXPECTED when already empty
    except Exception:
        return False


def gb(b) -> str:
    b = float(b or 0)
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    return f"{b / 1024 ** 2:.0f} MB"


def scan(time_budget: float = 25.0) -> dict:
    """Measure only. -> {items:[{label,path,bytes,files,skipped}], total, recycle}"""
    t0, now = time.monotonic(), time.time()
    items = []
    for t in targets():
        if time.monotonic() - t0 > time_budget:
            break
        it = {"label": t["label"], "path": str(t["path"]), "kind": t["kind"],
              "bytes": 0, "files": 0, "skipped": ""}
        if _running(t["procs"]):
            it["skipped"] = "browser khula hai"
        else:
            for _p, size in _walk_files(t["path"], t["kind"], now):
                it["bytes"] += size
                it["files"] += 1
                if time.monotonic() - t0 > time_budget:
                    break
        items.append(it)
    rb = recycle_bin_size()
    total = sum(i["bytes"] for i in items if not i["skipped"]) + (rb or 0)
    res = {"items": items, "recycle": rb, "total": total, "secs": round(time.monotonic() - t0, 1)}
    _LAST["scan"], _LAST["at"] = res, time.monotonic()
    return res


def last_scan(max_age_s: float = 900.0) -> Optional[dict]:
    if _LAST["scan"] and time.monotonic() - _LAST["at"] <= max_age_s:
        return _LAST["scan"]
    return None


def scan_text(res: dict) -> tuple:
    """-> (spoken summary, popup detail)."""
    lines, groups = [], {}
    for it in res["items"]:
        if it["skipped"]:
            lines.append(f"{it['label']}: skip ({it['skipped']})")
            continue
        lines.append(f"{it['label']}: {gb(it['bytes'])} ({it['files']} files)")
        key = it["label"].split(" cache")[0] + " cache" if it["kind"] == "cache" else it["label"]
        groups[key] = groups.get(key, 0) + it["bytes"]
    if res.get("recycle") is not None:
        lines.append(f"Recycle Bin: {gb(res['recycle'])}")
        groups["Recycle Bin"] = res["recycle"]
    big = sorted(((k, v) for k, v in groups.items() if v >= 1024 ** 2), key=lambda kv: -kv[1])
    say = f"Kul {gb(res['total'])} junk mila"
    if big:
        say += ": " + ", ".join(f"{k} {gb(v)}" for k, v in big[:5])
    lines.append(f"KUL: {gb(res['total'])}")
    lines.append("Python / uv / .venv / Git / Kaka folders ko haath nahi lagaya jayega.")
    return say, "\n".join(lines)


def clean(res: Optional[dict] = None, include_recycle: bool = True) -> dict:
    """Delete what scan() measured. Call ONLY after the user said yes."""
    res = res or last_scan() or scan()
    now = time.time()
    freed, removed, failed = 0, 0, 0
    for it in res["items"]:
        if it["skipped"]:
            continue
        root = Path(it["path"])
        kind = it["kind"]
        procs = next((t["procs"] for t in targets() if str(t["path"]) == str(root)), ())
        if _running(procs):
            continue
        for p, size in list(_walk_files(root, kind, now)):
            if protected(_rel(root, p)):
                continue
            try:
                p.unlink()
                freed += size
                removed += 1
            except Exception:
                failed += 1
        # remove now-empty sub folders (never the root itself, never protected)
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, onerror=lambda e: None):
            if Path(dirpath) == root or protected(_rel(root, dirpath)):
                continue
            try:
                os.rmdir(dirpath)
            except Exception:
                pass
    rb_ok = None
    if include_recycle and res.get("recycle"):
        rb_ok = empty_recycle_bin()
        if rb_ok:
            freed += res["recycle"]
    _LAST["scan"] = None
    return {"freed": freed, "removed": removed, "failed": failed, "recycle_emptied": rb_ok}
