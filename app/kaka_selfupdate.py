"""KAKA v1.1: auto-update from GitHub (abhinaba-chanda/Kaka, branch main).

  * Kaka checks app/VERSION on GitHub at start (at most once a day, Settings >
    v1.1 FEATURES > AUTO-UPDATE, default on) and when he says "Kaka update
    check karo".
  * If GitHub has a newer version, Kaka ASKS: "Naya version aaya hai, update
    karu?" (spoken + a CONFIRM / CANCEL banner on the HUD). Nothing is
    downloaded or replaced before his yes (his rule: ask before install).
  * On yes: pins the newest commit, reads app/update-manifest.json at that
    commit (sha256 of every app file), downloads only the files that differ,
    checks every sha256, backs up the old files to Data/Backups/update-<time>/,
    replaces them, compiles them with this Python (any failure -> the backup is
    put back), installs any new pip package the manifest lists, and restarts
    Kaka.
  * Never touches config/api_keys.json (your keys, name, city) or Data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

REPO = "abhinaba-chanda/Kaka"
BRANCH = "main"
RAW = "https://raw.githubusercontent.com/" + REPO + "/{ref}/{path}"
API_COMMIT = "https://api.github.com/repos/" + REPO + "/commits/" + BRANCH
APP_DIR = Path(__file__).resolve().parent            # .../app
ROOT_DIR = APP_DIR.parent                            # install folder (has app/)
MANIFEST = "app/update-manifest.json"
NEVER = ("app/config/api_keys.json",)
UA = "Kaka-updater"
TIMEOUT = 20.0
_STATE = {"pending": None, "busy": False}

# Tests replace this with a fake: (url, timeout) -> bytes
def _http_get(url: str, timeout: float = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _log(msg: str) -> None:
    try:
        from core import kaka_services as ks
        ks.log(msg)
    except Exception:
        print(msg)


def local_version() -> str:
    try:
        return (APP_DIR / "VERSION").read_text(encoding="utf-8").strip() or "1.0.0"
    except Exception:
        return "1.0.0"


def vtuple(v: str) -> tuple:
    out = []
    for part in str(v or "0").strip().lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:4])


def newer(remote: str, local: str) -> bool:
    return vtuple(remote) > vtuple(local)


def enabled() -> bool:
    try:
        import kaka_user
        return bool(kaka_user.get_setting("auto_update_enabled", True))
    except Exception:
        return True


def _state_path() -> Path:
    try:
        from core import kaka_services as ks
        return ks.data_path("selfupdate.json")
    except Exception:
        return Path.home() / "Kaka" / "Data" / "selfupdate.json"


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


def remote_version(ref: str = BRANCH) -> Optional[str]:
    try:
        v = _http_get(RAW.format(ref=ref, path="app/VERSION"), 10.0).decode("utf-8").strip()
        return v if v and len(v) < 32 else None
    except Exception:
        return None


def latest_commit() -> str:
    """The newest commit sha on main ('' = use the branch name)."""
    try:
        js = json.loads(_http_get(API_COMMIT, 10.0).decode("utf-8"))
        sha = str(js.get("sha") or "")
        return sha if len(sha) == 40 else ""
    except Exception:
        return ""


def check(force: bool = False) -> dict:
    """-> {"status": "newer"|"current"|"offline"|"off"|"skipped", "local", "remote"}"""
    loc = local_version()
    if not force and not enabled():
        return {"status": "off", "local": loc, "remote": None}
    st = _load_state()
    today = date.today().isoformat()
    if not force and st.get("last_check") == today:
        return {"status": "skipped", "local": loc, "remote": st.get("last_remote")}
    rem = remote_version()
    if rem is None:
        return {"status": "offline", "local": loc, "remote": None}
    st.update({"last_check": today, "last_remote": rem})
    _save_state(st)
    if newer(rem, loc):
        _STATE["pending"] = rem
        return {"status": "newer", "local": loc, "remote": rem}
    return {"status": "current", "local": loc, "remote": rem}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(manifest: dict, root: Path = ROOT_DIR) -> list:
    """Files (repo paths) whose local copy is missing or differs."""
    todo = []
    for rel, want in sorted((manifest.get("files") or {}).items()):
        rel = rel.replace("\\", "/")
        if rel in NEVER or not rel.startswith("app/") or ".." in rel.split("/"):
            continue
        p = root / rel
        try:
            if p.is_file() and _sha(p) == want:
                continue
        except Exception:
            pass
        todo.append(rel)
    return todo


def _missing_packages(pkgs: list) -> list:
    import importlib.util
    out = []
    for spec in pkgs or []:
        pip_name, _, mod = str(spec).partition("=")
        mod = mod or pip_name.replace("-", "_")
        try:
            if importlib.util.find_spec(mod) is None:
                out.append(pip_name)
        except Exception:
            out.append(pip_name)
    return out


def apply(ref: str = "", root: Path = ROOT_DIR, progress: Callable[[str], None] = _log,
          compile_check: bool = True, pip_install: bool = True) -> dict:
    """Download + verify + back up + replace. Call ONLY after the user's yes.
    -> {"ok", "changed": [...], "backup", "version", "error"}"""
    ref = ref or latest_commit() or BRANCH
    try:
        manifest = json.loads(_http_get(RAW.format(ref=ref, path=MANIFEST)).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"manifest nahi mila ({type(e).__name__})", "changed": []}
    todo = plan(manifest, root)
    if not todo:
        return {"ok": True, "changed": [], "version": manifest.get("version"), "backup": ""}
    progress(f"SYS: Update: {len(todo)} files download ho rahi hain...")
    staged = {}
    for i, rel in enumerate(todo, 1):
        try:
            data = _http_get(RAW.format(ref=ref, path=rel), 60.0)
        except Exception as e:
            return {"ok": False, "error": f"{rel} download nahi hua ({type(e).__name__})", "changed": []}
        if hashlib.sha256(data).hexdigest() != manifest["files"][rel]:
            return {"ok": False, "error": f"{rel} ka sha256 match nahi hua - kuch nahi badla",
                    "changed": []}
        staged[rel] = data
        if i % 5 == 0 or i == len(todo):
            progress(f"SYS: Update: {i}/{len(todo)} files verified")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        from core import kaka_services as ks
        backup = ks.data_path("Backups", f"update-{stamp}", "x").parent
    except Exception:
        backup = root / f"kaka-update-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    existed = {}
    for rel in staged:
        src = root / rel
        existed[rel] = src.is_file()
        if existed[rel]:
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (backup / "README.txt").write_text(
        "Kaka auto-update backup. To undo: copy these files back over the install folder\n"
        f"({root}). New files that did not exist before: "
        + ", ".join(r for r, e in existed.items() if not e) + "\n", encoding="utf-8")

    def _restore():
        for rel, was in existed.items():
            dst = root / rel
            try:
                if was:
                    shutil.copy2(backup / rel, dst)
                elif dst.exists():
                    dst.unlink()
            except Exception:
                pass
    try:
        for rel, data in staged.items():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".kaka-new")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        if compile_check:
            import py_compile
            for rel in staged:
                if rel.endswith(".py"):
                    py_compile.compile(str(root / rel), doraise=True,
                                       cfile=str(backup / "_compile_check.pyc"))
    except Exception as e:
        _restore()
        return {"ok": False, "error": f"replace/compile fail ({type(e).__name__}: {e}) - "
                                      f"purani files wapas laga di", "changed": [],
                "backup": str(backup)}
    pip_msg = ""
    if pip_install:
        miss = _missing_packages(manifest.get("pip") or [])
        if miss:
            progress("SYS: Update: naye packages install ho rahe hain: " + ", ".join(miss))
            try:
                py = sys.executable.replace("pythonw.exe", "python.exe")
                r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check"] + miss,
                                   capture_output=True, text=True, timeout=900,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                pip_msg = "packages ok" if r.returncode == 0 else "kuch packages install nahi hue"
            except Exception as e:
                pip_msg = f"pip fail: {type(e).__name__}"
    return {"ok": True, "changed": list(staged), "backup": str(backup),
            "version": manifest.get("version"), "pip": pip_msg}


def restart_kaka() -> bool:
    """Start a tiny helper that waits for this Kaka to close, starts it again,
    then closes this one."""
    exe = ROOT_DIR / "Kaka" / "Kaka.exe"
    if exe.is_file():
        cmd = [str(exe)]
    else:
        py = sys.executable
        if sys.platform == "win32" and py.lower().endswith("python.exe"):
            pw = py[:-10] + "pythonw.exe"
            py = pw if os.path.exists(pw) else py
        cmd = [py, str(APP_DIR / "main.py")]
    helper = ("import os,sys,time,subprocess\n"
              f"pid={os.getpid()}\n"
              "for _ in range(120):\n"
              "    try:\n"
              "        import psutil\n"
              "        if not psutil.pid_exists(pid): break\n"
              "    except Exception:\n"
              "        time.sleep(4); break\n"
              "    time.sleep(0.5)\n"
              "time.sleep(1)\n"
              f"subprocess.Popen({cmd!r}, cwd={str(APP_DIR)!r})\n")
    try:
        flags = 0
        if sys.platform == "win32":
            flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen([sys.executable, "-c", helper], cwd=str(APP_DIR),
                         creationflags=flags, close_fds=True)
    except Exception as e:
        _log(f"SYS: Restart helper fail: {e} - Kaka khud band karke dobara kholo.")
        return False
    try:
        from core import kaka_services as ks

        def _quit():
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.quit()
            except Exception:
                os._exit(0)
        if not ks.run_in_ui(_quit):
            os._exit(0)
    except Exception:
        os._exit(0)
    return True


def apply_and_restart() -> str:
    if _STATE["busy"]:
        return "Update pehle se chal raha hai."
    _STATE["busy"] = True
    try:
        res = apply()
    finally:
        _STATE["busy"] = False
    if not res.get("ok"):
        msg = "Update nahi ho paya: " + str(res.get("error"))
        _log("SYS: " + msg)
        return msg
    if not res.get("changed"):
        return "Kaka pehle se latest hai, kuch badalna nahi pada."
    msg = (f"Update ho gaya (v{res.get('version')}, {len(res['changed'])} files). "
           f"Backup: {res.get('backup')}. Kaka restart ho raha hai.")
    _log("SYS: " + msg)
    try:
        from core import kaka_services as ks
        ks.popup("Kaka update", msg)
    except Exception:
        pass
    time.sleep(2.5)
    restart_kaka()
    return msg


def ask_banner(remote: str) -> str:
    """Show the HUD CONFIRM / CANCEL banner for the update. -> tool text."""
    try:
        from core import confirm
        return confirm.request("kaka-update", f"Kaka v{remote} update",
                               f"Naya version v{remote} (abhi v{local_version()}). CONFIRM = "
                               f"download, verify, backup, replace, restart.",
                               apply_and_restart)
    except Exception as e:
        return f"banner nahi dikha: {e}"


def check_on_start() -> None:
    """Background, once a day: if newer, ASK (voice + banner). Never installs alone."""
    res = check(force=False)
    if res["status"] != "newer":
        if res["status"] in ("current",):
            _log(f"SYS: Kaka v{res['local']} latest hai.")
        return
    rem = res["remote"]
    try:
        ask_banner(rem)
    except Exception:
        pass
    try:
        from core import kaka_services as ks
        ks.say(f"Naya Kaka version {rem} aaya hai (abhi {res['local']} hai). Ek baar poocho: "
               f"'Naya version aaya hai, update karu?' Haan bole toh kaka_selfupdate tool "
               f"action=apply go_ahead=true chalana, ya woh HUD pe CONFIRM dabaye.")
    except Exception:
        pass
