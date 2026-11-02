r"""
actions/vscode_helper.py - Kaka update 18: code help in VS Code.

  "ye file run karo"      action=run_file
      The file open in VS Code (read from the VS Code window title) is saved
      (Ctrl+S, only when VS Code is really in front) and run in a NEW visible
      terminal window. Output + errors are captured at the same time (Tee into
      a log under Data\Cache\vscode) so Kaka can say "chal gaya" / "error line
      12: NameError ..." and remember it for "error fix karo".
      Programs that read input (input(), scanf, cin, readline) run in a plain
      terminal so he can type; their output is not captured (Kaka says so).
      C/C++: gcc/g++ compile first (captured); no gcc -> "gcc install nahi hai".
  "error fix karo"        action=fix_error
      File + last error -> Gemini -> full fixed file -> unified diff shown in
      the HUD. NOTHING is written. Kaka asks once: "Likh doon?"
  "haan" (after the diff) action=apply_fix
      Only now: <file>.bak backup, then the fix is written. Refused when no
      fix is pending, it is >30 min old, or the file changed after the diff.
  "naya project banao X"  action=new_project
      F:\Kaka\Data\Projects\X with a starter file, opened in VS Code.

Never raises; every failure comes back as an honest sentence.
"""
import os
import re
import sys
import time

_IS_WIN = sys.platform == "win32"
_LAST = {}            # last run: file, exit, output, error, ts, captured
_PENDING = {}         # pending fix: path, sha, new_text, diff, ts
PENDING_MAX_AGE = 30 * 60
RUN_WAIT_S = 15.0
_SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "env", "__pycache__", "windows",
              "program files", "program files (x86)", "programdata", "$recycle.bin",
              "system volume information", "appdata", "site-packages", ".vscode"}


def _ks():
    from core import kaka_services as ks
    return ks


def _log_time(label, t0, extra=""):
    try:
        _ks().timing(label, time.monotonic() - t0, extra)
    except Exception:
        pass


# ------------------------------------------------------------------ title
_DIRTY = ("\u25cf", "\u2022", "*")
_NOT_FILES = {"welcome", "settings", "keyboard shortcuts", "release notes", "get started",
              "untitled", "output", "problems", "terminal", "extensions"}


def parse_title(title: str) -> tuple:
    """VS Code title -> (file_name, folder_name, dirty). ("", "", False) when it
    is not a VS Code window. Handles '● ' dirty marker, profile segment,
    '(Workspace)', '[Administrator]', '[Restricted Mode]', Insiders."""
    t = (title or "").strip()
    if "Visual Studio Code" not in t:
        return "", "", False
    dirty = False
    while t[:1] in _DIRTY:
        dirty = True
        t = t[1:].lstrip()
    segs = [s.strip() for s in re.split(r"\s[-\u2014]\s", t)]
    idx = next((i for i, s in enumerate(segs) if s.startswith("Visual Studio Code")), len(segs))
    segs = [s for s in segs[:idx] if s]

    def _clean(s):
        s = re.sub(r"\s*\[[^\]]*\]\s*$", "", s)
        s = re.sub(r"\s*\((Workspace|Working Tree|Index|Read-only|Preview|Deleted)\)\s*$", "", s, flags=re.I)
        return s.strip()

    segs = [_clean(s) for s in segs]
    looks_file = lambda s: bool(re.search(r"\.[A-Za-z0-9_+]{1,6}$", s)) and \
        s.lower() not in _NOT_FILES
    if not segs:
        return "", "", dirty
    if len(segs) == 1:
        return (segs[0], "", dirty) if looks_file(segs[0]) else ("", segs[0], dirty)
    f = segs[0] if looks_file(segs[0]) else ""
    return f, segs[1], dirty


# ------------------------------------------------------------------ OS hooks
def _windows():
    try:
        from core import winfocus
        return winfocus.windows()
    except Exception:
        return []


def _foreground_hwnd():
    try:
        from core import winfocus
        return winfocus.foreground()[0]
    except Exception:
        return 0


def _bring_front(hwnd) -> bool:
    try:
        from core import winfocus
        return bool(winfocus.force_foreground(hwnd))
    except Exception:
        return False


def _press_ctrl_s() -> None:
    if not _IS_WIN:
        return
    import ctypes
    u = ctypes.WinDLL("user32")
    u.keybd_event(0x11, 0, 0, 0)
    u.keybd_event(0x53, 0, 0, 0)
    u.keybd_event(0x53, 0, 2, 0)
    u.keybd_event(0x11, 0, 2, 0)


def _title_of(hwnd) -> str:
    for h, _pid, t in _windows():
        if h == hwnd:
            return t
    return ""


def vscode_window():
    """(hwnd, title) of the VS Code window he is using: the foreground one if it
    is VS Code, else the topmost VS Code window. (0, '') if none."""
    wins = [(h, t) for h, _p, t in _windows() if "Visual Studio Code" in (t or "")]
    if not wins:
        return 0, ""
    fg = _foreground_hwnd()
    for h, t in wins:
        if h == fg:
            return h, t
    return wins[0]


def _appdata() -> str:
    return os.environ.get("APPDATA", "")


def _home():
    from pathlib import Path
    return Path.home()


# ------------------------------------------------------------------ resolve
def uri_to_path(uri: str) -> str:
    from urllib.parse import unquote
    if not uri.startswith("file://"):
        return ""
    rest = unquote(uri[len("file://"):])
    if rest.startswith("/"):
        if re.match(r"^/[A-Za-z]:", rest):
            rest = rest[1:]
        return rest.replace("/", os.sep) if _IS_WIN else rest
    return "\\\\" + rest.replace("/", "\\")          # UNC


def recent_paths() -> list:
    """File/folder paths VS Code opened recently, most recent first."""
    out, seen = [], set()
    base = os.path.join(_appdata(), "Code", "User", "globalStorage")
    texts = []
    try:
        with open(os.path.join(base, "storage.json"), encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
    except Exception:
        pass
    db = os.path.join(base, "state.vscdb")
    if os.path.isfile(db):
        try:
            import sqlite3
            from pathlib import Path
            con = sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                row = con.execute("SELECT value FROM ItemTable WHERE key="
                                  "'history.recentlyOpenedPathsList'").fetchone()
                if row and row[0]:
                    v = row[0]
                    texts.insert(0, v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
            finally:
                con.close()
        except Exception:
            pass
    for txt in texts:
        for m in re.finditer(r'file:///?[^"\s]+', txt):
            p = uri_to_path(m.group(0))
            if p and p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return out


def _walk_find(root, name: str, max_depth: int = 6, dirs: bool = False, cap: int = 30000) -> list:
    hits, n = [], 0
    name_l = name.lower()
    root = str(root)
    if not os.path.isdir(root):
        return hits
    base_depth = root.rstrip("\\/").count(os.sep)
    for cur, dnames, fnames in os.walk(root):
        n += len(dnames) + len(fnames)
        if n > cap:
            break
        depth = cur.count(os.sep) - base_depth
        dnames[:] = [d for d in dnames if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
        if depth >= max_depth:
            dnames[:] = []
        pool = dnames if dirs else fnames
        for x in pool:
            if x.lower() == name_l:
                hits.append(os.path.join(cur, x))
    return hits


def _fallback_roots() -> list:
    h = _home()
    roots = [h / "Desktop", h / "Documents", h / "OneDrive" / "Desktop",
             h / "OneDrive" / "Documents", h / "Downloads"]
    try:
        import kaka_paths
        roots = [kaka_paths.projects(), kaka_paths.saved()] + roots
    except Exception:
        pass
    if _IS_WIN:
        roots.append("F:\\")
    return roots


def resolve_file(fname: str, folder: str = "") -> str:
    """Full path of the file shown in VS Code's title, newest if several."""
    if not fname:
        return ""
    if os.path.isabs(fname) and os.path.isfile(fname):
        return fname
    cands = []
    recents = recent_paths()
    fl = fname.lower()
    for p in recents:
        if os.path.basename(p).lower() == fl and os.path.isfile(p):
            if not folder or folder.lower() in [s.lower() for s in re.split(r"[\\/]", p)]:
                cands.append(p)
    if not cands and folder:
        dirs = [p for p in recents if os.path.basename(p.rstrip("\\/")).lower() == folder.lower()
                and os.path.isdir(p)]
        for r in _fallback_roots():
            if not dirs:
                dirs += _walk_find(r, folder, max_depth=3 if str(r) in ("F:\\",) else 4, dirs=True)
        for d in dirs:
            cands += _walk_find(d, fname)
            if cands:
                break
    if not cands and not folder:
        for r in _fallback_roots()[:-1] if _IS_WIN else _fallback_roots():
            cands += _walk_find(r, fname, max_depth=3)
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        return ""
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


# ------------------------------------------------------------------ commands
_INPUT_RE = {
    ".py": r"\binput\s*\(|sys\.stdin",
    ".js": r"readline|process\.stdin|\bprompt\s*\(",
    ".c": r"\bscanf\s*\(|\bgetchar\s*\(|\bgets\s*\(|fgets\s*\([^)]*stdin",
    ".cpp": r"\bcin\s*>>|getline\s*\(\s*(std::)?cin|\bscanf\s*\(|\bgetchar\s*\(",
}
_INPUT_RE[".cc"] = _INPUT_RE[".cpp"]


def needs_input(path: str, text: str = None) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if text is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(200000)
        except Exception:
            return False
    rx = _INPUT_RE.get(ext)
    return bool(rx and re.search(rx, text))


def _find_python() -> list:
    try:
        from actions import code_helper as ch
        r = ch._find_python()
        if r:
            return list(r)
    except Exception:
        pass
    import shutil
    for n in ("py", "python", "python3"):
        h = shutil.which(n)
        if h:
            return [h, "-3"] if n == "py" else [h]
    return [sys.executable]


def _find_node() -> str:
    try:
        from actions import code_helper as ch
        r = ch._find_node()
        if r:
            return r
    except Exception:
        pass
    import shutil
    return shutil.which("node") or ""


def _find_cc(cpp: bool) -> str:
    try:
        from actions import code_helper as ch
        r = ch._find_cc(cpp)
        if r:
            return r
    except Exception:
        pass
    import shutil
    for n in (("g++", "clang++") if cpp else ("gcc", "clang")):
        h = shutil.which(n)
        if h:
            return h
    return ""


def build_command(path: str) -> dict:
    """-> {ok, lang, compile: [..]|None, run: [..], missing: '', reason: ''}"""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    if ext == ".py":
        py = _find_python()
        if not py:
            return {"ok": False, "missing": "python", "reason": "Python install nahi hai"}
        return {"ok": True, "lang": "python", "compile": None, "run": py + ["-u", name]}
    if ext in (".js", ".mjs", ".cjs"):
        node = _find_node()
        if not node:
            return {"ok": False, "missing": "node", "reason": "Node.js install nahi hai"}
        return {"ok": True, "lang": "node", "compile": None, "run": [node, name]}
    if ext in (".c", ".cpp", ".cc", ".cxx"):
        cpp = ext != ".c"
        cc = _find_cc(cpp)
        if not cc:
            return {"ok": False, "missing": "gcc",
                    "reason": ("g++" if cpp else "gcc") + " install nahi hai (MinGW nahi mila)"}
        exe = os.path.splitext(name)[0] + (".exe" if _IS_WIN else "")
        return {"ok": True, "lang": "cpp" if cpp else "c",
                "compile": [cc, name, "-o", exe],
                "run": [(".\\" if _IS_WIN else "./") + exe]}
    return {"ok": False, "missing": "", "reason": f"{ext or 'ye'} file main chala nahi sakta (python, js, c, cpp chala sakta hoon)"}


def _cmdline(args: list) -> str:
    out = []
    for a in args:
        a = str(a)
        out.append('"' + a.replace('"', '') + '"' if (" " in a or not a) else a)
    return " ".join(out)


# ------------------------------------------------------------------ run plumbing
def _run_captured(args, cwd, timeout=60):
    import subprocess
    flags = 0x08000000 if _IS_WIN else 0
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace",
                       stdin=subprocess.DEVNULL, timeout=timeout, creationflags=flags)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _popen(args, cwd):
    import subprocess
    flags = 0x00000010 if _IS_WIN else 0           # CREATE_NEW_CONSOLE
    return subprocess.Popen(args, cwd=cwd, creationflags=flags)


def _run_dir():
    d = _ks().cache_dir() / "vscode"
    d.mkdir(parents=True, exist_ok=True)
    try:                                   # old run scripts/logs: keep a day
        cut = time.time() - 86400
        for f in d.glob("run_*"):
            if f.stat().st_mtime < cut:
                f.unlink()
    except Exception:
        pass
    return d


def _ps_q(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def launch_visible(path: str, run_args: list, capture: bool) -> dict:
    """Open a new terminal window running the program. capture=True tees the
    output into a log Kaka reads; returns paths."""
    cwd = os.path.dirname(path)
    name = os.path.basename(path)
    d = _run_dir()
    stamp = str(int(time.time() * 1000))
    cmdf = d / f"run_{stamp}.cmd"
    log = d / f"run_{stamp}.log"
    done = d / f"run_{stamp}.done"
    cmd_lines = ["@echo off", "chcp 65001 >nul", "set PYTHONIOENCODING=utf-8",
                 "set PYTHONUTF8=1", f'cd /d "{cwd}"']
    if capture:
        cmd_lines += [_cmdline(run_args) + " 2>&1", "exit /b %errorlevel%"]
        with open(cmdf, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(cmd_lines) + "\n")
        ps1 = d / f"run_{stamp}.ps1"
        ps = "\n".join([
            f"$Host.UI.RawUI.WindowTitle = {_ps_q('Kaka run: ' + name)}",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            f"Write-Host {_ps_q('===== Kaka: running ' + name + ' =====')} -ForegroundColor Cyan",
            f"& cmd.exe /d /c {_ps_q(str(cmdf))} | Tee-Object -FilePath {_ps_q(str(log))}",
            "$code = $LASTEXITCODE",
            f"Set-Content -LiteralPath {_ps_q(str(done))} -Value $code",
            "Write-Host ''",
            "Write-Host \"===== khatam (exit $code) - ye window band kar sakte ho =====\" -ForegroundColor Cyan",
        ]) + "\n"
        with open(ps1, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(ps)
        _popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit",
                "-File", str(ps1)], cwd)
    else:
        cmd_lines += [f"title Kaka run: {name}", f"echo ===== Kaka: running {name} =====",
                      _cmdline(run_args), "echo.", "echo ===== khatam - ye window band kar sakte ho ====="]
        with open(cmdf, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(cmd_lines) + "\n")
        _popen(["cmd.exe", "/d", "/k", str(cmdf)], cwd)
    return {"log": str(log), "done": str(done), "cmd": str(cmdf)}


def read_log(p: str) -> str:
    try:
        with open(p, "rb") as f:
            b = f.read()
    except Exception:
        return ""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    if b[:3] == b"\xef\xbb\xbf":
        b = b[3:]
    return b.decode("utf-8", "replace")


def summarize(output: str, code, fname: str) -> dict:
    """-> {ok, line, error, short}"""
    out = (output or "").replace("\r\n", "\n").strip()
    lines = [l for l in out.split("\n") if l.strip()]
    err, line = "", ""
    # gcc / g++
    m = re.search(r"^(?:.*[\\/])?" + re.escape(fname) + r":(\d+):\d+:\s*(?:fatal\s+)?error:\s*(.+)$",
                  out, re.M)
    if m:
        line, err = m.group(1), m.group(2).strip()
    elif "Traceback (most recent call last)" in out or re.search(r"^\s*File \".*\", line \d+", out, re.M):
        fl = re.findall(r'File "([^"]+)", line (\d+)', out)
        mine = [n for f, n in fl if os.path.basename(f).lower() == fname.lower()]
        line = (mine or [n for _f, n in fl] or [""])[-1]
        cand = [l for l in lines if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Interrupt|Exit)\b", l.strip())]
        err = (cand[-1] if cand else lines[-1]).strip()
    elif re.search(r"^\w*Error: ", out, re.M):
        m2 = re.search(re.escape(fname) + r":(\d+)", out)
        line = m2.group(1) if m2 else ""
        err = re.findall(r"^(\w*Error: .*)$", out, re.M)[0].strip()
    elif code not in (0, "0", None):
        err = (lines[-1] if lines else f"exit code {code}").strip()
    ok = not err and code in (0, "0", None)
    if ok:
        tail = out[-300:] if out else "(kuch print nahi hua)"
        short = f"chal gaya. Output: {tail}"
    else:
        short = f"error aaya{(' line ' + line) if line else ''}: {err[:200]}"
    return {"ok": ok, "line": line, "error": err, "short": short}


def _remember(rec: dict) -> None:
    _LAST.clear()
    _LAST.update(rec)
    try:
        _ks().write_json(_ks().cache_dir() / "vscode_last_run.json", rec)
    except Exception:
        pass


def last_run() -> dict:
    if _LAST:
        return dict(_LAST)
    try:
        return _ks().read_json(_ks().cache_dir() / "vscode_last_run.json", {}) or {}
    except Exception:
        return {}


def _watch_late(paths: dict, path: str, fname: str, limit_s: float = 900.0):
    import threading

    def _w():
        end = time.monotonic() + limit_s
        while time.monotonic() < end:
            if os.path.exists(paths["done"]):
                time.sleep(0.3)
                code = read_log(paths["done"]).strip()
                outp = read_log(paths["log"])
                s = summarize(outp, int(code) if code.lstrip("-").isdigit() else code, fname)
                _remember({"file": path, "exit": code, "output": outp[-6000:],
                           "error": s["error"], "line": s["line"], "ts": time.time(),
                           "captured": True})
                return
            time.sleep(1.0)

    threading.Thread(target=_w, daemon=True, name="kaka-vscode-watch").start()


# ------------------------------------------------------------------ actions
def _active_file(p: dict) -> tuple:
    """-> (path, hwnd, title, why_not)"""
    fp = str(p.get("file_path") or "").strip().strip('"')
    if fp and os.path.isfile(fp):
        return fp, 0, "", ""
    hwnd, title = vscode_window()
    if not hwnd:
        return "", 0, "", "VS Code khula nahi dikh raha"
    fname, folder, _dirty = parse_title(title)
    if not fname:
        return "", hwnd, title, "VS Code mein koi file khuli nahi dikh rahi (title: " + title[:60] + ")"
    path = resolve_file(fname, folder)
    if not path:
        return "", hwnd, title, f"{fname} disk pe dhoond nahi paaya" + (f" (folder {folder})" if folder else "")
    return path, hwnd, title, ""


def run_file(p: dict) -> str:
    t0 = time.monotonic()
    path, hwnd, title, why = _active_file(p)
    if not path:
        return f"status=failed. {why}. Tell him that in one short Hinglish line."
    fname = os.path.basename(path)
    saved_note = ""
    if hwnd and parse_title(title)[2]:                      # unsaved changes
        if _bring_front(hwnd):
            _press_ctrl_s()
            time.sleep(0.5)
            if parse_title(_title_of(hwnd))[2]:
                saved_note = " (Ctrl+S ke baad bhi unsaved dikha - last saved version chala)"
        else:
            saved_note = " (VS Code aage nahi aaya, save nahi kar paaya - last saved version chala)"
    cmd = build_command(path)
    if not cmd.get("ok"):
        _log_time("vscode run", t0, "missing " + (cmd.get("missing") or "-"))
        extra = ""
        if cmd.get("missing"):
            extra = (f" If he wants it installed, ask ONCE; only after his yes call "
                     f"code_helper action=install_tool tool={cmd['missing']}.")
        return f"status=failed. {fname} nahi chala: {cmd['reason']}.{extra}"
    cwd = os.path.dirname(path)
    if cmd.get("compile"):
        try:
            code, outp = _run_captured(cmd["compile"], cwd, timeout=90)
        except Exception as e:
            return f"status=failed. Compile nahi ho paaya: {type(e).__name__}: {str(e)[:120]}"
        if code != 0:
            s = summarize(outp, code, fname)
            _remember({"file": path, "exit": code, "output": outp[-6000:], "error": s["error"] or outp[-400:],
                       "line": s["line"], "ts": time.time(), "captured": True, "stage": "compile"})
            try:
                _ks().popup("Compile error: " + fname, outp[-3500:])
            except Exception:
                pass
            _log_time("vscode run", t0, "compile error")
            return (f"status=error. {fname} compile nahi hua{saved_note}: {s['short']}. Errors screen pe hain. "
                    f"Tell him briefly and offer: 'error fix karun?'")
    interactive = needs_input(path)
    try:
        paths = launch_visible(path, cmd["run"], capture=not interactive)
    except Exception as e:
        _log_time("vscode run", t0, "launch failed")
        return f"status=failed. Terminal nahi khul paaya: {type(e).__name__}: {str(e)[:120]}"
    if interactive:
        _remember({"file": path, "exit": None, "output": "", "error": "", "line": "",
                   "ts": time.time(), "captured": False})
        _log_time("vscode run", t0, "interactive")
        return (f"status=started. {fname} terminal window mein chal raha hai{saved_note}. Ye input "
                f"maangta hai, isliye wahin type karna hoga; iska output main nahi padh sakta. Tell him that briefly.")
    end = time.monotonic() + RUN_WAIT_S
    while time.monotonic() < end and not os.path.exists(paths["done"]):
        time.sleep(0.25)
    if not os.path.exists(paths["done"]):
        _watch_late(paths, path, fname)
        _log_time("vscode run", t0, "still running")
        return (f"status=started. {fname} terminal mein abhi bhi chal raha hai{saved_note} "
                f"(15 s se zyada). Khatam hone pe output yaad rahega - error ho toh 'error fix karo' bolna.")
    time.sleep(0.2)
    code_s = read_log(paths["done"]).strip()
    code = int(code_s) if code_s.lstrip("-").isdigit() else code_s
    outp = read_log(paths["log"])
    s = summarize(outp, code, fname)
    _remember({"file": path, "exit": code, "output": outp[-6000:], "error": s["error"],
               "line": s["line"], "ts": time.time(), "captured": True})
    _log_time("vscode run", t0, "ok" if s["ok"] else "error")
    if s["ok"]:
        return f"status=ok. {fname} {s['short']}{saved_note}. Say it briefly in Hinglish."
    return (f"status=error. {fname} chala, {s['short']}{saved_note}. Say it briefly and offer: "
            f"'error fix karun?' (that is vscode_helper action=fix_error).")


def _sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _strip_fences(t: str) -> str:
    t = (t or "").strip("\n")
    m = re.match(r"^\s*```[\w+.-]*\n(.*?)\n```\s*$", t, re.S)
    return m.group(1) if m else t


def _decode(b: bytes) -> tuple:
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8"), "utf-8-sig"
    return b.decode("utf-8"), "utf-8"


def fix_error(p: dict) -> str:
    t0 = time.monotonic()
    lr = last_run()
    path = str(p.get("file_path") or "").strip().strip('"')
    if not (path and os.path.isfile(path)):
        path = ""
        ap, _h, _t, why = _active_file({})
        path = ap or (lr.get("file") if lr.get("file") and os.path.isfile(lr.get("file")) else "")
        if not path:
            return f"status=failed. Kaunsi file fix karun, pata nahi chala: {why}."
    problem = str(p.get("problem") or "").strip()
    err = ""
    if lr.get("file") and os.path.normcase(lr["file"]) == os.path.normcase(path):
        err = (lr.get("output") or "")[-3000:] or lr.get("error") or ""
    if not err and not problem:
        return ("status=failed. Is file ka koi error mere paas nahi hai. Pehle 'ye file run karo' bolo "
                "ya batao kya galat ho raha hai.")
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > 120000:
            return "status=failed. File bahut badi hai (120 KB+), poori file Gemini ko nahi bhej sakta."
        old, enc = _decode(raw)
    except UnicodeDecodeError:
        return "status=failed. File UTF-8 text nahi hai, isko fix nahi kar sakta."
    except Exception as e:
        return f"status=failed. File padh nahi paaya: {type(e).__name__}."
    crlf = "\r\n" in old
    old_n = old.replace("\r\n", "\n")
    fname = os.path.basename(path)
    prompt = (f"File: {fname}\n"
              + (f"Error when run:\n{err}\n" if err else "")
              + (f"User says: {problem}\n" if problem else "")
              + "Fix the bug with the smallest change. Keep everything else byte-identical "
                "(names, comments, formatting). Return ONLY the complete corrected file content, "
                "no markdown fences, no explanation.\n\n----- FILE START -----\n"
              + old_n + "\n----- FILE END -----")
    from core import kaka_ai
    ok, out = kaka_ai.text(prompt, timeout=45,
                           system="You are a precise code fixer. Output only the full fixed file.")
    if not ok:
        _log_time("vscode fix", t0, "ai fail")
        return f"status=failed. Fix nahi bana: {out}."
    new_n = _strip_fences(out).replace("\r\n", "\n")
    if old_n.endswith("\n") and not new_n.endswith("\n"):
        new_n += "\n"
    if new_n.strip() == old_n.strip():
        _log_time("vscode fix", t0, "no change")
        return "status=failed. Gemini ko is file mein koi badlav nahi mila. Error screen pe dikha ke 'ye error kya hai' pooch sakte ho."
    import difflib
    diff = "".join(difflib.unified_diff(old_n.splitlines(True), new_n.splitlines(True),
                                        fromfile=fname, tofile=fname + " (fix)", n=2))
    changed = sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    new_text = new_n.replace("\n", "\r\n") if crlf else new_n
    _PENDING.clear()
    _PENDING.update({"path": path, "sha": _sha(raw), "new_text": new_text, "enc": enc,
                     "diff": diff, "ts": time.time()})
    try:
        _ks().write_json(_ks().cache_dir() / "vscode_pending_fix.json", dict(_PENDING))
    except Exception:
        pass
    try:
        _ks().popup("Fix: " + fname, diff[:4000])
    except Exception:
        pass
    _log_time("vscode fix", t0, f"{changed} lines")
    return (f"status=needs_confirmation. Fix ka diff HUD pe hai ({changed} lines badlengi in {fname}). "
            f"NOTHING is written yet. Ask him ONCE: '{fname} mein fix likh doon?'. Only if he says "
            f"haan/yes call vscode_helper action=apply_fix; if nahi, action=cancel_fix.")


def _pending() -> dict:
    if _PENDING:
        return dict(_PENDING)
    try:
        return _ks().read_json(_ks().cache_dir() / "vscode_pending_fix.json", {}) or {}
    except Exception:
        return {}


def _clear_pending():
    _PENDING.clear()
    try:
        os.remove(str(_ks().cache_dir() / "vscode_pending_fix.json"))
    except Exception:
        pass


def apply_fix(p: dict) -> str:
    t0 = time.monotonic()
    pend = _pending()
    if not pend.get("path"):
        return "status=failed. Koi fix pending nahi hai. Pehle 'error fix karo' bolo, diff dekh ke haan bolna."
    path = pend["path"]
    if time.time() - float(pend.get("ts") or 0) > PENDING_MAX_AGE:
        _clear_pending()
        return "status=failed. Woh fix 30 min se purana hai, likha nahi. Dobara 'error fix karo' bolo."
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        _clear_pending()
        return f"status=failed. {os.path.basename(path)} ab mil nahi rahi, kuch nahi likha."
    if _sha(raw) != pend.get("sha"):
        _clear_pending()
        return ("status=failed. Diff ke baad file badal gayi, isliye purana fix nahi likha. "
                "Dobara 'error fix karo' bolo.")
    import shutil
    bak = path + ".bak"
    i = 2
    while os.path.exists(bak) and i < 50:
        bak = f"{path}.bak{i}"
        i += 1
    try:
        shutil.copy2(path, bak)
    except Exception as e:
        return f"status=failed. Backup nahi ban paaya ({type(e).__name__}), isliye file nahi badli."
    try:
        data = pend["new_text"].encode("utf-8")
        if pend.get("enc") == "utf-8-sig":
            data = b"\xef\xbb\xbf" + data
        tmp = path + ".kaka_tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception as e:
        return f"status=failed. Likh nahi paaya ({type(e).__name__}); original file same hai, backup {os.path.basename(bak)}."
    _clear_pending()
    _log_time("vscode apply fix", t0)
    return (f"status=ok. Fix likh diya {os.path.basename(path)} mein; purani file "
            f"{os.path.basename(bak)} mein backup hai. Dobara chalana ho toh 'ye file run karo'.")


def cancel_fix(p: dict) -> str:
    had = bool(_pending().get("path"))
    _clear_pending()
    return "status=ok. Theek hai, fix nahi likha." if had else "status=ok. Koi fix pending nahi tha."


_STARTERS = {
    "python": ("main.py", 'print("Hello from {name}!")\n'),
    "javascript": ("index.js", 'console.log("Hello from {name}!");\n'),
    "c": ("main.c", '#include <stdio.h>\n\nint main(void) {{\n    printf("Hello from {name}!\\n");\n    return 0;\n}}\n'),
    "cpp": ("main.cpp", '#include <iostream>\n\nint main() {{\n    std::cout << "Hello from {name}!" << std::endl;\n    return 0;\n}}\n'),
}
_LANG_ALIASES = {"py": "python", "js": "javascript", "node": "javascript", "c++": "cpp",
                 "javascript": "javascript", "python": "python", "c": "c", "cpp": "cpp"}


def safe_name(name: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip().strip(".")
    n = re.sub(r"\s+", " ", n)[:60]
    if n.upper() in {"CON", "PRN", "AUX", "NUL"} or re.match(r"^(COM|LPT)\d$", n.upper()):
        n = "_" + n
    return n or "MyProject"


def _code_exe() -> list:
    la = os.environ.get("LOCALAPPDATA", "")
    exe = os.path.join(la, "Programs", "Microsoft VS Code", "Code.exe") if la else ""
    if exe and os.path.isfile(exe):
        return [exe]
    import shutil
    c = shutil.which("code")
    if c:
        return ["cmd.exe", "/c", c] if c.lower().endswith((".cmd", ".bat")) else [c]
    return []


def _open_detached(args):
    import subprocess
    flags = 0x08000000 if _IS_WIN else 0
    subprocess.Popen(args, creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _startfile(p):
    if hasattr(os, "startfile"):
        os.startfile(p)


def _projects_root():
    try:
        import kaka_paths
        return str(kaka_paths.projects())
    except Exception:
        return str(_ks().data_path("Projects", "x").parent)


def new_project(p: dict) -> str:
    t0 = time.monotonic()
    name = safe_name(p.get("name"))
    lang = _LANG_ALIASES.get(str(p.get("language") or "python").strip().lower(), "python")
    folder = os.path.join(_projects_root(), name)
    existed = os.path.isdir(folder) and bool(os.listdir(folder))
    starter = ""
    try:
        os.makedirs(folder, exist_ok=True)
        if not existed:
            fn, body = _STARTERS[lang]
            starter = os.path.join(folder, fn)
            with open(starter, "w", encoding="utf-8") as f:
                f.write(body.format(name=name))
    except Exception as e:
        return f"status=failed. Folder nahi ban paaya ({type(e).__name__}: {str(e)[:100]})."
    opened = "VS Code"
    try:
        code = _code_exe()
        if code:
            _open_detached(code + [folder] + ([starter] if starter else []))
        else:
            _startfile(folder)
            opened = "Explorer (VS Code nahi mila)"
    except Exception as e:
        opened = f"kahin nahi ({type(e).__name__})"
    _log_time("vscode new project", t0, lang)
    if existed:
        return f"status=ok. Project {name} pehle se tha ({folder}), use {opened} mein khol diya. Kuch overwrite nahi kiya."
    return f"status=ok. Project {name} bana diya: {folder}, {os.path.basename(starter)} ke saath, {opened} mein khola."


def vscode_helper(parameters=None, player=None):
    p = parameters or {}
    action = str(p.get("action") or "run_file").strip().lower()
    try:
        if action in ("run_file", "run"):
            return run_file(p)
        if action in ("fix_error", "fix"):
            return fix_error(p)
        if action in ("apply_fix", "apply"):
            return apply_fix(p)
        if action in ("cancel_fix", "cancel"):
            return cancel_fix(p)
        if action in ("new_project", "project"):
            return new_project(p)
        return f"status=failed. Unknown action {action}."
    except Exception as e:
        return f"status=failed. VS Code helper mein dikkat: {type(e).__name__}: {str(e)[:120]}"


TOOL = {
    "name": "vscode_helper",
    "description": (
        "Works on the file OPEN IN VS CODE right now. run_file: 'ye file run karo', "
        "'isko chalao', 'code run karo' (saves, runs in a terminal, tells the output/error). "
        "fix_error: 'error fix karo', 'ye theek karo' (shows a diff, writes nothing). "
        "apply_fix: ONLY after he said haan to that diff. cancel_fix: he said nahi. "
        "new_project: 'naya project banao <name>' (folder + opens VS Code). No question "
        "before run/new_project."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["run_file", "fix_error", "apply_fix", "cancel_fix", "new_project"],
                       "description": "What to do"},
            "name": {"type": "STRING", "description": "new_project: project name"},
            "language": {"type": "STRING", "description": "new_project: python | javascript | c | cpp (default python)"},
            "problem": {"type": "STRING", "description": "fix_error: what is wrong, in his words (optional)"},
            "file_path": {"type": "STRING", "description": "Optional full path; default = the file open in VS Code"},
        },
        "required": ["action"],
    },
    "handler": vscode_helper,
}
