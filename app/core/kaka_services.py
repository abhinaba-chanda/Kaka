r"""
core/kaka_services.py - Kaka update 18: one small "service desk" every new
feature uses, so no feature has to know how main.py works.

    from core import kaka_services as ks
    ks.log("SYS: ...")                 # activity log (+ kaka-timing.txt for [time] lines)
    ks.timing("reminder", 0.42)        # "[time] reminder: 0.42 s"
    ks.popup("Reminder", "Chai!")      # HUD panel under the face (thread-safe)
    ks.beep()                          # short beep, never raises
    ks.say("Reminder: chai peeni hai", urgent=False)
        # Kaka speaks it in his own words. Asleep -> wakes briefly, speaks,
        # goes back to sleep. Game mode -> queued until the game closes,
        # unless urgent=True.  Returns "spoken" | "queued" | "no-session".
    ks.data_path("reminders.json")     # F:\Kaka\Data\reminders.json
    ks.in_game_mode() -> bool
    ks.gemini_key() -> str             # READ ONLY from config/api_keys.json

bind(jarvis) is called once from main.py (JarvisLive.__init__); everything
else works (and does nothing visible) when it was never bound - that is how
the unit tests run.  Nothing here ever raises.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_J = None                      # the JarvisLive instance (main.py)
_LOCK = threading.Lock()
_QUEUE: list = []              # (instruction, urgent) waiting for game mode to end
_GAME = {"on": False, "manual": None, "why": ""}
_SAY_LOCK = threading.Lock()
_STARTERS: list = []           # callables run in a background thread after bind
_APP = Path(__file__).resolve().parent.parent

# test hooks
_speak_impl: Optional[Callable[[str], None]] = None


# Modules that register background workers with on_start(). Imported in the
# background thread (not at startup), so Kaka opens exactly as fast as before.
SERVICE_MODULES = ["core.kaka_reminders", "core.kaka_game", "core.kaka_feeds"]


def bind(jarvis) -> None:
    """main.py: give the services the running Kaka. Starts the background
    workers (reminders, morning update, game watcher) 2 s later in a daemon
    thread, so startup is not slowed."""
    global _J
    _J = jarvis
    def _later():
        time.sleep(2.0)
        import importlib
        for name in SERVICE_MODULES:
            try:
                importlib.import_module(name)
            except Exception as e:
                _print(f"[services] {name} failed to load: {e}")
        for fn in list(_STARTERS):
            try:
                fn()
            except Exception as e:
                _print(f"[services] start failed {getattr(fn, '__name__', fn)}: {e}")
    threading.Thread(target=_later, name="kaka-u18-services", daemon=True).start()


def on_start(fn: Callable[[], None]) -> Callable[[], None]:
    if fn not in _STARTERS:
        _STARTERS.append(fn)
    return fn


def jarvis():
    return _J


def _print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        pass


def log(text: str) -> None:
    try:
        if _J is not None:
            _J.ui.write_log(str(text))
            return
    except Exception:
        pass
    try:
        from core import timing_log as _tl
        _tl.maybe(str(text))
    except Exception:
        pass
    _print(str(text))


def timing(label: str, seconds: float, extra: str = "") -> None:
    try:
        log(f"[time] {label}: {float(seconds):.2f} s" + (f" ({extra})" if extra else ""))
    except Exception:
        pass


def popup(title: str, text: str) -> None:
    try:
        if _J is not None:
            _J.ui.show_content(str(title)[:48], str(text)[:4000])
    except Exception as e:
        _print(f"[services] popup failed: {e}")


def beep(kind: str = "reminder") -> None:
    try:
        import winsound
        if kind == "reminder":
            for f in (880, 1175, 880):
                winsound.Beep(f, 180)
        else:
            winsound.MessageBeep()
    except Exception:
        pass


def data_path(*parts: str) -> Path:
    try:
        import kaka_paths
        base = kaka_paths.DATA_ROOT
    except Exception:
        base = Path.home() / "Kaka" / "Data"
    p = Path(base).joinpath(*parts)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def cache_dir() -> Path:
    p = data_path("Cache", "x").parent
    return p


def read_json(p: Path, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p: Path, data) -> bool:
    """Atomic write (tmp + replace). False on any error."""
    try:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        _print(f"[services] write {p} failed: {e}")
        return False


def gemini_key() -> str:
    """READ ONLY. Never writes config/api_keys.json."""
    try:
        data = json.loads((_APP / "config" / "api_keys.json").read_text(encoding="utf-8"))
        k = str(data.get("gemini_api_key") or "").strip()
        if k:
            return k
        for s in (data.get("gemini_api_keys") or []):
            if str(s).strip():
                return str(s).strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- Qt thread
_INVOKER = {"obj": None}


def run_in_ui(fn: Callable[[], None]) -> bool:
    """Run fn on the Qt (HUD) thread. Safe from any thread. False if no Qt app."""
    try:
        from PyQt6.QtCore import QObject, QCoreApplication, pyqtSignal, pyqtSlot, Qt
        app = QCoreApplication.instance()
        if app is None:
            return False
        if _INVOKER["obj"] is None:
            class _Inv(QObject):
                sig = pyqtSignal(object)

                @pyqtSlot(object)
                def run(self, f):
                    try:
                        f()
                    except Exception as e:
                        _print(f"[services] ui fn failed: {e}")
            inv = _Inv()
            inv.moveToThread(app.thread())
            # the receiver (inv) lives in the Qt thread -> queued into it
            inv.sig.connect(inv.run, Qt.ConnectionType.QueuedConnection)
            _INVOKER["obj"] = inv
        _INVOKER["obj"].sig.emit(fn)
        return True
    except Exception as e:
        _print(f"[services] ui call failed: {e}")
        return False


def set_mic_muted(muted: bool) -> bool:
    """Mute/unmute Kaka's mic like the HUD mic button (thread-safe)."""
    try:
        ui = _J.ui
    except Exception:
        return False
    def _do():
        try:
            ui.muted = bool(muted)
        except Exception as e:
            _print(f"[services] mute failed: {e}")
    return run_in_ui(_do)


def mic_muted() -> bool:
    try:
        return bool(_J.ui.muted)
    except Exception:
        return False


# ---------------------------------------------------------------- game mode
def in_game_mode() -> bool:
    return bool(_GAME["on"])


def set_game_mode(on: bool, why: str = "") -> None:
    was = _GAME["on"]
    _GAME["on"] = bool(on)
    _GAME["why"] = why
    if was and not on:
        flush_queue()


def flush_queue() -> None:
    with _LOCK:
        items = list(_QUEUE)
        _QUEUE.clear()
    if items:
        text = " | ".join(i for i, _u in items)
        say("Game ke dauraan ye baatein ruki thi, ab batao: " + text, urgent=True)


# ---------------------------------------------------------------- speaking
def _asleep() -> bool:
    try:
        return bool(getattr(_J, "_deep_sleep", False))
    except Exception:
        return False


def _session_ready() -> bool:
    try:
        return _J is not None and _J.session is not None and getattr(_J, "_loop", None) is not None
    except Exception:
        return False


def _speaking() -> bool:
    try:
        with _J._speaking_lock:
            return bool(_J._is_speaking)
    except Exception:
        return False


def _instruction(text: str, brief: bool = True) -> str:
    if brief:
        return ("[KAKA_NOTICE] Abhi turant ye baat Abhinaba ko ek-do chhote Hinglish "
                "vakyon mein bolo, apne shabdon mein, koi tool mat chalao, koi sawaal "
                "mat poocho: " + str(text))
    return ("[KAKA_NOTICE] Abhi ye update Abhinaba ko Hinglish mein sunao - saare "
            "points, chhote vakya, max 8 vakya, koi tool mat chalao: " + str(text))


def say(text: str, urgent: bool = False, wait_s: float = 25.0, brief: bool = True) -> str:
    """Thread-safe. Never raises. Blocks at most ~wait_s (+speech) when it has
    to wake Kaka; call it from a background thread, never the Qt thread."""
    try:
        if _speak_impl is not None:
            _speak_impl(_instruction(text, brief))
            return "spoken"
        if in_game_mode() and not urgent:
            with _LOCK:
                _QUEUE.append((str(text), urgent))
            log(f"SYS: Game mode - baad mein bolunga: {str(text)[:60]}")
            return "queued"
        if _J is None:
            return "no-session"
        with _SAY_LOCK:
            slept = _asleep()
            if slept:
                try:
                    _J.deep_wake(reason="reminder / notice")
                except Exception as e:
                    _print(f"[services] wake failed: {e}")
                    return "no-session"
            t0 = time.monotonic()
            while not _session_ready() and time.monotonic() - t0 < wait_s:
                time.sleep(0.25)
            if not _session_ready():
                log("SYS: Bol nahi paya (session nahi juda) - popup dekh lo.")
                if slept:
                    try:
                        _J.deep_sleep(reason="bol nahi paya, wapas so raha hoon")
                    except Exception:
                        pass
                return "no-session"
            if slept:
                time.sleep(1.0)       # let the fresh session settle
            _J.speak(_instruction(text, brief))
            if slept:
                # wait for the reply to start and finish, then back to sleep
                t1 = time.monotonic()
                while not _speaking() and time.monotonic() - t1 < 8:
                    time.sleep(0.2)
                t2 = time.monotonic()
                while _speaking() and time.monotonic() - t2 < 30:
                    time.sleep(0.25)
                time.sleep(1.2)
                try:
                    _J.deep_sleep(reason="reminder bol diya, wapas so raha hoon")
                except Exception as e:
                    _print(f"[services] back-to-sleep failed: {e}")
            return "spoken"
    except Exception as e:
        _print(f"[services] say failed: {e}")
        return "no-session"


def say_async(text: str, urgent: bool = False, brief: bool = True) -> None:
    threading.Thread(target=say, args=(text, urgent, 25.0, brief), daemon=True,
                     name="kaka-u18-say").start()
