r"""
core/kaka_alarm.py - Kaka update 21 (feature 33): chor alarm + the "he came
back" watcher for face greet (30).

"chor alarm on / off" (default OFF). When it is on and the PC is
    * unlocked (after being locked >= 15 s),
    * woken from sleep straight to the desktop (with a lock screen: on unlock),
    * logged on (Kaka's first start in a fresh Windows session),
Kaka takes a webcam look of at most 3 s. HIS face -> nothing. No face / an
unknown face -> the photo + time go to his Discord DM ("Koi PC khol raha
hai!") through the running Kaka bot (queued and sent later if the bot is
offline) and are saved in F:\Kaka\Data\Alarm. Max 1 Discord alert per 2 min.
Windows login / passwords are never touched; no admin, no service.

The same watcher greets him ("Welcome back Abhinaba") when he returns after
>= 10 min idle/locked (face greet on, not asleep, not in game mode). One
camera look serves both.

Detection is polling (every 1 s, two cheap user32 calls; no window, no hook):
lock = the input desktop cannot be switched to (OpenInputDesktop/SwitchDesktop),
sleep = the wall clock jumped while this thread was frozen, idle =
GetLastInputInfo.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

POLL = 1.0
LOCK_MIN = 15.0             # a lock shorter than this (UAC prompt) is not an "unlock"
SLEEP_GAP = 30.0            # wall clock jumped this much more than POLL -> was asleep
AWAY_GREET = 600.0          # >= 10 min away -> welcome back
RATE_LIMIT = 120.0          # max 1 Discord alert per 2 min
DEDUPE = 60.0               # events this close together are one event
FLUSH_EVERY = 20.0
QUEUE_MAX = 20
ALERT_TEXT = "Koi PC khol raha hai!"

KIND_HI = {"unlock": "PC unlock hua", "resume": "PC sleep se jaga", "logon": "Windows login hua",
           "test": "test"}
WHY_HI = {"unknown": "anjaan chehra", "noface": "koi chehra nahi dikha",
          "noenrol": "chehra yaad nahi hai isliye pehchan nahi sakta - 'mera chehra yaad karo' bolo",
          "nomodel": "face model nahi hai, pehchan nahi sakta",
          "nocam": "camera nahi khula", "busy": "camera busy tha"}

# test hooks
_TEST: dict = {}
_wall: Callable[[], float] = time.time
_sleep: Callable[[float], None] = time.sleep
_W: dict = {"thread": None, "stop": threading.Event(), "flusher": None}
_EV_LOCK = threading.Lock()
_Q_LOCK = threading.Lock()
STATE = {"last_event": None, "events": 0, "alerts": 0, "queued": 0, "watch": "off"}


def _ks():
    from core import kaka_services as ks
    return ks


def _face():
    from core import kaka_face as F
    return F


def _log(text: str) -> None:
    try:
        _ks().log(text)
    except Exception:
        pass


def alarm_on() -> bool:
    try:
        return bool(_face().settings().get("alarm", False))
    except Exception:
        return False


def _state_path():
    return _ks().data_path("Alarm", "alarm_state.json")


def _queue_path():
    return _ks().data_path("Alarm", "queue.json")


def _read(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(p, data) -> bool:
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ Windows sensors
def _win_idle() -> Optional[float]:
    if _TEST.get("idle") is not None:
        return _TEST["idle"]()
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return None
        k32 = ctypes.windll.kernel32
        k32.GetTickCount.restype = wintypes.DWORD
        now = int(k32.GetTickCount())
        return ((now - int(li.dwTime)) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _win_locked() -> Optional[bool]:
    if _TEST.get("locked") is not None:
        return _TEST["locked"]()
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.OpenInputDesktop.restype = wintypes.HANDLE
        u32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        u32.SwitchDesktop.argtypes = [wintypes.HANDLE]
        u32.SwitchDesktop.restype = wintypes.BOOL
        u32.CloseDesktop.argtypes = [wintypes.HANDLE]
        u32.CloseDesktop.restype = wintypes.BOOL
        h = u32.OpenInputDesktop(0, False, 0x0100)        # DESKTOP_SWITCHDESKTOP
        if not h:
            return True                                     # Winlogon desktop = locked
        try:
            return not bool(u32.SwitchDesktop(h))
        finally:
            u32.CloseDesktop(h)
    except Exception:
        return None


# ------------------------------------------------------------------ the watcher
class Watcher:
    """Pure state machine fed one poll at a time (unit-testable)."""

    def __init__(self, now: float):
        self.last_wall = now
        self.locked_since: Optional[float] = None
        self.idle_prev = 0.0
        self.pending_resume = False
        self.last_handled = -1e18

    def poll(self, now: float, locked: Optional[bool], idle: Optional[float]):
        """-> (kind, away_s) or None."""
        ev = None
        gap = now - self.last_wall
        self.last_wall = now
        locked = bool(locked)
        idle = float(idle) if idle is not None else 0.0
        if gap > POLL + SLEEP_GAP:                       # we were frozen = PC slept
            if locked:
                self.pending_resume = True
                if self.locked_since is None:
                    self.locked_since = now - gap
            else:
                ev = ("resume", gap)
        if locked:
            if self.locked_since is None:
                self.locked_since = now
        elif self.locked_since is not None:
            dur = now - self.locked_since
            self.locked_since = None
            if dur >= LOCK_MIN or self.pending_resume:
                ev = ("unlock", max(dur, self.idle_prev))
            self.pending_resume = False
        if ev is None and not locked and self.idle_prev >= AWAY_GREET and idle < 10.0:
            ev = ("idle_return", self.idle_prev)
        self.idle_prev = idle
        if ev is not None:
            if now - self.last_handled < DEDUPE:
                return None
            self.last_handled = now
        return ev


def _watch_loop(stop: threading.Event) -> None:
    w = Watcher(_wall())
    STATE["watch"] = "on"
    while not stop.is_set():
        _sleep(POLL)
        try:
            ev = w.poll(_wall(), _win_locked(), _win_idle())
        except Exception:
            ev = None
        if ev is not None:
            threading.Thread(target=handle_event, args=ev, daemon=True, name="kaka-u21-event").start()
    STATE["watch"] = "off"


def start_watch() -> bool:
    if os.name != "nt" and not _TEST.get("force_watch"):
        return False
    t = _W.get("thread")
    if t is not None and t.is_alive():
        return False
    _W["stop"] = threading.Event()
    t = threading.Thread(target=_watch_loop, args=(_W["stop"],), daemon=True, name="kaka-u21-watch")
    _W["thread"] = t
    t.start()
    return True


def stop_watch() -> None:
    _W["stop"].set()


# ------------------------------------------------------------------ events
def handle_event(kind: str, away: float = 0.0, result: Optional[dict] = None) -> str:
    """One event -> at most one 3 s camera look -> greet / alarm / nothing."""
    with _EV_LOCK:
        F = _face()
        STATE["events"] += 1
        STATE["last_event"] = {"kind": kind, "away": round(away, 1), "at": datetime.now().isoformat(timespec="seconds")}
        need_alarm = alarm_on() and kind in ("unlock", "resume", "logon", "test")
        need_greet = False
        try:
            need_greet = (away >= AWAY_GREET and F.greet_on() and F.enrolled()
                          and not F._blocked_reason())
        except Exception:
            need_greet = False
        if not (need_alarm or need_greet):
            return "nothing"
        r = result if result is not None else F.check(F.CHECK_SECONDS, f"{KIND_HI.get(kind, kind)}: chehra check")
        res = r.get("result")
        if res == "him":
            if need_greet:
                return F.greet_now(r)
            return "him"
        if need_alarm:
            return alarm(r, kind)
        return res or "nothing"          # unknown face + no alarm -> nothing spoken


def alarm(r: dict, kind: str) -> str:
    F = _face()
    ts = datetime.now()
    why = WHY_HI.get(r.get("result"), r.get("result") or "")
    path = None
    if r.get("frame") is not None:
        p = _ks().data_path("Alarm", f"alarm-{ts.strftime('%Y%m%d-%H%M%S')}.jpg")
        if F.save_jpg(r["frame"], p):
            path = str(p)
    text = f"{ALERT_TEXT} {ts.strftime('%d %b %I:%M:%S %p')} - {KIND_HI.get(kind, kind)}, {why}."
    if not path:
        extra = r.get("why") or ""
        text += " (Photo nahi aayi" + (f": {extra}" if extra else "") + ")"
    _log(f"SYS: Chor alarm: {text}" + (f" Photo: {path}" if path else ""))
    st = _read(_state_path(), {})
    now = _wall()
    last = float(st.get("last_alert") or 0)
    if now - last < RATE_LIMIT and now >= last:
        _log("SYS: Chor alarm: 2 min ke andar doosra alert - Discord pe nahi bheja (photo save hai).")
        return "rate_limited"
    st["last_alert"] = now
    st["count"] = int(st.get("count") or 0) + 1
    _write(_state_path(), st)
    STATE["alerts"] += 1
    if send_now(text, path):
        return "sent"
    enqueue(text, path)
    return "queued"


# ------------------------------------------------------------------ Discord delivery + queue
def send_now(text: str, path: Optional[str]) -> bool:
    if _TEST.get("send") is not None:
        return bool(_TEST["send"](text, path))
    try:
        from core import kaka_discord as D
        return bool(D.send_owner(text, path))
    except Exception as e:
        _log(f"ERR: Chor alarm Discord pe nahi gaya: {e}")
        return False


def load_queue() -> list:
    q = _read(_queue_path(), [])
    return q if isinstance(q, list) else []


def enqueue(text: str, path: Optional[str]) -> None:
    with _Q_LOCK:
        q = load_queue()
        q.append({"text": text, "file": path, "t": _wall()})
        q = q[-QUEUE_MAX:]
        _write(_queue_path(), q)
        STATE["queued"] = len(q)
    _log("SYS: Chor alarm: Discord bot abhi offline hai - alert queue mein, bot online hote hi jayega.")
    start_flusher()


def flush_once() -> int:
    """Send what is queued (oldest first). -> how many went out."""
    sent = 0
    with _Q_LOCK:
        q = load_queue()
        left = []
        for i, it in enumerate(q):
            f = it.get("file")
            if f and not os.path.isfile(f):
                f = None
            when = datetime.fromtimestamp(float(it.get("t") or 0)).strftime("%I:%M %p")
            txt = f"(Purana alert, {when} ka - tab bot offline tha)\n{it.get('text', ALERT_TEXT)}"
            if not left and send_now(txt, f):
                sent += 1
            else:
                left.append(it)
        _write(_queue_path(), left)
        STATE["queued"] = len(left)
    return sent


def _flush_loop() -> None:
    while load_queue():
        try:
            flush_once()
        except Exception:
            pass
        if not load_queue():
            break
        _sleep(FLUSH_EVERY)


def start_flusher() -> bool:
    t = _W.get("flusher")
    if t is not None and t.is_alive():
        return False
    t = threading.Thread(target=_flush_loop, daemon=True, name="kaka-u21-alarmq")
    _W["flusher"] = t
    t.start()
    return True


# ------------------------------------------------------------------ toggles (tool)
def set_alarm(on: bool) -> str:
    F = _face()
    F.save_settings(alarm=bool(on))
    if not on:
        _log("SYS: Chor alarm off.")
        return "Chor alarm off kar diya."
    start_watch()
    notes = []
    if not F.enrolled():
        notes.append("Abhi tumhara chehra yaad nahi hai, isliye har unlock pe photo aayegi - "
                     "'mera chehra yaad karo' bol do")
    ok, why = F.models_ready()
    if not ok:
        notes.append(why)
    try:
        from core import kaka_discord as D
        if not D.token():
            notes.append("Discord bot token nahi hai, photo sirf F:\\Kaka\\Data\\Alarm mein save hogi")
    except Exception:
        pass
    _log("SYS: Chor alarm on.")
    msg = ("Chor alarm on hai. Ab koi PC unlock kare ya sleep se jagaye aur chehra tumhara na ho, "
           "toh photo aur time tumhare Discord pe aayega.")
    if notes:
        msg += " Dhyan do: " + "; ".join(notes) + "."
    return msg


def status() -> str:
    q = len(load_queue())
    s = "Chor alarm " + ("on" if alarm_on() else "off")
    st = _read(_state_path(), {})
    if st.get("count"):
        s += f", ab tak {st['count']} alert"
    if q:
        s += f", {q} alert Discord pe jaane baaki (bot offline)"
    return s + "."


# ------------------------------------------------------------------ Kaka start (logon)
def _session_epoch() -> Optional[float]:
    if _TEST.get("logon_age") is not None:
        age = _TEST["logon_age"]()
    else:
        try:
            from core import kaka_bootintro as B
            ages = [a for a in (B.logon_age_s(), B.uptime_s()) if a is not None]
            age = min(ages) if ages else None
        except Exception:
            age = None
    if age is None or age > AWAY_GREET:
        return None
    return _wall() - age


def _intro_playing() -> bool:
    try:
        import sys
        B = sys.modules.get("core.kaka_bootintro")
        d = (B._STATE.get("decision") or {}) if B is not None else {}
        return bool(d.get("play"))
    except Exception:
        return False


def on_kaka_start() -> str:
    """ks.on_start (2 s after Kaka opens, background thread)."""
    start_watch()
    if load_queue():
        start_flusher()
    sess = _session_epoch()
    if sess is None:
        return "not a fresh login"
    F = _face()
    s = F.settings()
    last = s.get("logon_session")
    if isinstance(last, (int, float)) and abs(float(last) - sess) < 120:
        return "already handled this login"
    F.save_settings(logon_session=sess)
    intro = _intro_playing()
    greet = F.greet_on() and F.enrolled() and not F._blocked_reason()
    need_alarm = alarm_on()
    if not (need_alarm or greet):
        return "nothing"
    F.start_intro_check()                  # shared with the boot-intro greeting
    F._INTRO["done"].wait(8.0)
    r = F._INTRO["result"] or {"result": "nocam", "why": "check did not finish"}
    if r.get("result") == "him":
        if greet and not intro:
            return F.greet_now(r, "login")
        return "him"
    if need_alarm:
        return alarm(r, "logon")
    return r.get("result") or "nothing"


def _auto_start() -> None:
    """ks.on_start: own thread, so the 3 s camera look never delays the other starters."""
    def _run():
        try:
            on_kaka_start()
        except Exception as e:
            _log(f"ERR: chor alarm start: {e}")
    threading.Thread(target=_run, daemon=True, name="kaka-u21-start").start()


try:
    from core import kaka_services as _ks_mod
    _ks_mod.on_start(_auto_start)
except Exception:
    pass


def selfcheck() -> tuple:
    if os.name == "nt":
        lk = _win_locked()
        if lk is None:
            return False, "lock detect nahi chala"
    return True, ("on" if alarm_on() else "off (bolo 'chor alarm on')")
