"""
Kaka.exe — a small launcher for Kaka by Abhinaba Chanda.

Why a launcher and not the whole app frozen into one exe:
  The app loads dozens of files at runtime (face model, prompt, actions,
  plugins, providers, dashboard, config) relative to its own folder, installs
  optional packages with pip while running, and depends on PyQt6, onnxruntime,
  Playwright and more. Freezing all of that with PyInstaller is fragile and every
  update would need a rebuild. This exe only starts the app's own Python
  (pythonw = no black window) with main.py in the app folder — the app stays
  exactly the one that already works, and the exe is tiny.

  Kaka.exe                 start Kaka (or do nothing if it is already running)
  Kaka.exe --startup       same, after a short delay (used at Windows login)
  Kaka.exe --voice-toggle  switch Brian <-> Gemini voice, then restart Kaka
  Kaka.exe --restart       restart Kaka
  Kaka.exe --stop          stop Kaka
"""
import os
import subprocess
import sys
import time

APP_NAME = "Kaka"


def _here():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _find_root():
    """The install folder: the one that holds app\\main.py and .venv."""
    cands = []
    env = os.environ.get("KAKA_ROOT", "").strip()
    if env:
        cands.append(env)
    h = _here()
    cands += [os.path.dirname(h), h, r"C:\Kaka",
              os.path.join(os.path.expanduser("~"), "Kaka")]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "app", "main.py")):
            return c
    return None


def _state_dir():
    d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Kaka")
    os.makedirs(d, exist_ok=True)
    return d


def _logs_dir(root):
    """Kaka's log lives with the rest of its data: <install folder>\\Data\\Logs
    (inside the install folder; an older layout keeps it one level up).
    Falls back to %LOCALAPPDATA%\\Kaka."""
    try:
        d = os.environ.get("KAKA_DATA", "").strip()
        if d:
            d = os.path.join(d, "Logs")
        elif os.path.isdir(os.path.join(root, "Data")):
            d = os.path.join(root, "Data", "Logs")
        elif os.path.isdir(os.path.join(os.path.dirname(os.path.normpath(root)), "Data")):
            d = os.path.join(os.path.dirname(os.path.normpath(root)), "Data", "Logs")
        else:
            d = ""
        if d:
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    return _state_dir()


def _msg(text, title=APP_NAME, icon=0x10):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, icon)
    except Exception:
        print(text)


def _pid_file():
    return os.path.join(_state_dir(), "kaka.pid")


def _alive(pid):
    """True if `pid` is a running python process (Windows), never raises."""
    if not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
                return False
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return "python" in os.path.basename(buf.value).lower()
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def _read_pid():
    try:
        with open(_pid_file(), "r") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _stop():
    pid = _read_pid()
    if _alive(pid):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        for _ in range(40):
            if not _alive(pid):
                break
            time.sleep(0.25)
    try:
        os.remove(_pid_file())
    except Exception:
        pass


def _pythonw(root):
    for name in ("pythonw.exe", "python.exe"):
        p = os.path.join(root, ".venv", "Scripts", name)
        if os.path.isfile(p):
            return p
    return None


def _focus_existing():
    """Bring the running Kaka window to the front, if it can be found.
    Kaka update 8: the window is called "KAKA";
    every top-level window is checked against that name."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        names = {"KAKA"}
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            n = u32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value in names:
                    found.append(hwnd)
            return True

        u32.EnumWindows(_cb, 0)
        for h in found:
            u32.ShowWindow(h, 9)          # SW_RESTORE
            u32.SetForegroundWindow(h)
            return True
    except Exception:
        pass
    return False


def _start(root):
    if _alive(_read_pid()):
        _focus_existing()
        return 0            # already running — one Kaka at a time
    py = _pythonw(root)
    if not py:
        _msg("Kaka could not find its Python (.venv) in\n" + root +
             "\n\nRun Kaka-Upgrade.bat again or send Tab a screenshot.")
        return 2
    app = os.path.join(root, "app")
    logs = _logs_dir(root)
    log_path = os.path.join(logs, "kaka-log.txt")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            old = os.path.join(logs, "kaka-log-previous.txt")
            if os.path.exists(old):
                os.remove(old)
            os.replace(log_path, old)
    except Exception:
        pass
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    flags = 0
    for n in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, n, 0)
    log = open(log_path, "ab")
    proc = subprocess.Popen([py, os.path.join(app, "main.py")], cwd=app, env=env,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=flags, close_fds=True)
    with open(_pid_file(), "w") as f:
        f.write(str(proc.pid))
    return 0


def _voice_toggle(root):
    py = _pythonw(root)
    if not py:
        return 2
    app = os.path.join(root, "app")
    subprocess.run([py, os.path.join(app, "kaka_tools.py"), "voice", "toggle", "--popup"],
                   cwd=app, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _stop()
    time.sleep(1.0)
    return _start(root)


def main(argv):
    root = _find_root()
    if not root:
        _msg("Kaka could not find its app folder (app\\main.py).\n"
             "Run Kaka-Upgrade.bat again or send Tab a screenshot.")
        return 2
    args = [a.lower() for a in argv[1:]]
    if "--stop" in args:
        _stop()
        return 0
    if "--voice-toggle" in args:
        return _voice_toggle(root)
    if "--restart" in args:
        _stop()
        time.sleep(1.0)
        return _start(root)
    if "--startup" in args:
        if _alive(_read_pid()):
            return 0        # Kaka update 8: already running - never a second copy
        time.sleep(15)      # let Wi-Fi and audio come up after login
    return _start(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
