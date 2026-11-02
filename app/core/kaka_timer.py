r"""
core/kaka_timer.py - Kaka update 19: timers, stopwatch, pomodoro.

Timers live in memory (a reminder is the thing for "kal subah"; a timer is
"25 minute"). One daemon thread sleeps on an Event until the next timer is
due - nothing runs while idle. At the end: beep + HUD popup + Kaka speaks it,
also from sleep (core.kaka_services.say: wake -> speak -> sleep, the
Update-18 reminder way). The HUD shows what is left in a small line above the
Sleep button while anything runs. Nothing here raises.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Optional

from core import kaka_services as ks

_LOCK = threading.RLock()
_WAKE = threading.Event()
_TIMERS: list = []      # {"id","label","end"(monotonic),"total","kind": timer|pomodoro}
_SW = {"start": None, "acc": 0.0}            # stopwatch
_POMO = {"on": False, "phase": "", "cycle": 0, "cycles": 4, "work": 25, "brk": 5}
_THREAD = {"t": None}
_HUD = {"label": None, "qt": None}

# test hooks
_clock = time.monotonic
_fired: list = []

# ------------------------------------------------------------------ parsing
_NUM = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
    "paanch": 5, "panch": 5, "five": 5, "chhe": 6, "che": 6, "six": 6, "saat": 7, "seven": 7,
    "aath": 8, "eight": 8, "nau": 9, "nine": 9, "das": 10, "dus": 10, "ten": 10,
    "gyarah": 11, "barah": 12, "pandrah": 15, "pandra": 15, "fifteen": 15, "bees": 20,
    "twenty": 20, "pachees": 25, "pachchees": 25, "pachis": 25, "tees": 30, "thirty": 30,
    "chalis": 40, "chaalis": 40, "forty": 40, "pachas": 50, "pachaas": 50, "fifty": 50,
    "saath": 60, "sixty": 60, "nabbe": 90, "ninety": 90, "a": 1, "an": 1,
}
_WHOLE = {"dedh": 1.5, "derh": 1.5, "dhai": 2.5, "dhaai": 2.5, "adhai": 2.5,
          "aadha": 0.5, "aadhe": 0.5, "adha": 0.5, "adhe": 0.5, "half": 0.5}
_PREFIX = {"saade": 0.5, "sade": 0.5, "sawa": 0.25, "sava": 0.25, "savva": 0.25,
           "paune": -0.25, "pone": -0.25}
_UNITS = [
    (r"(ghante|ghanta|ghanton|ghanto|hours|hour|hrs|hr|h)", 3600),
    (r"(minutes|minute|minat|mins|min|m)", 60),
    (r"(seconds|second|secs|sec|s|second)", 1),
]


def _unit(tok: str) -> Optional[int]:
    for rx, mul in _UNITS:
        if re.fullmatch(rx, tok):
            return mul
    return None


def parse_duration(text: str) -> Optional[int]:
    """'25 minute' -> 1500, 'dedh ghanta' -> 5400, '2 min 30 sec' -> 150,
    'paune do ghante' -> 6300, '1:30' -> 90 (min:sec), '25' -> 1500. None if no time."""
    t = (text or "").lower()
    t = re.sub(r"(\d)\s*([a-z])", r"\1 \2", t)
    t = re.sub(r"[,!?]", " ", t)
    m = re.search(r"\b(\d{1,3}):(\d{2})(?::(\d{2}))?\b", t)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        if c is not None:
            return a * 3600 + b * 60 + int(c)
        return (a * 3600 + b * 60) if re.search(r"ghant|hour|hr", t) else a * 60 + b
    toks = t.split()
    total = 0.0
    pend: Optional[float] = None
    frac = 0.0
    seen = False
    weak = False
    for tok in toks:
        if tok in _PREFIX:
            frac = _PREFIX[tok]
            continue
        n = None
        try:
            n = float(tok)
        except ValueError:
            n = _NUM.get(tok)
        if n is not None and tok not in ("a", "an", "m", "s", "h"):
            pend = n + frac
            frac = 0.0
            weak = False
            continue
        if tok in _WHOLE:
            pend = _WHOLE[tok]
            continue
        if tok in ("a", "an") and pend is None:
            pend = 1
            weak = True
            continue
        mul = _unit(tok)
        if mul is not None and (pend is not None or tok in ("ghanta", "ghante", "hour", "minute", "min")):
            if pend is None:
                if tok in ("m", "s", "h"):
                    continue
                pend = 1.0 + frac
                frac = 0.0
            total += pend * mul
            pend = None
            seen = True
    if not seen and pend is not None and not weak:
        total = pend * 60           # bare "25" = minutes
        seen = True
    if not seen or total <= 0:
        return None
    return int(round(total))


def fmt(secs: float) -> str:
    """Spoken: '24 minute 10 second', '1 ghanta 5 minute'."""
    s = max(0, int(round(secs)))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    parts = []
    if h:
        parts.append(f"{h} ghanta")
    if m:
        parts.append(f"{m} minute")
    if s and not h:
        parts.append(f"{s} second")
    return " ".join(parts) or "0 second"


def clock(secs: float) -> str:
    s = max(0, int(secs + 0.999))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ------------------------------------------------------------------ timers
def add(seconds: int, label: str = "", kind: str = "timer") -> dict:
    with _LOCK:
        t = {"id": uuid.uuid4().hex[:8], "label": (label or "").strip()[:60],
             "end": _clock() + float(seconds), "total": int(seconds), "kind": kind}
        _TIMERS.append(t)
    _ensure_thread()
    _WAKE.set()
    hud_refresh()
    return dict(t)


def running() -> list:
    now = _clock()
    with _LOCK:
        return sorted(({**t, "left": max(0.0, t["end"] - now)} for t in _TIMERS),
                      key=lambda x: x["left"])


def cancel(label: str = "", all_: bool = False, kind: str = "timer") -> list:
    with _LOCK:
        if all_:
            gone = [t for t in _TIMERS if t["kind"] == kind]
        elif label:
            gone = [t for t in _TIMERS if t["kind"] == kind and label.lower() in t["label"].lower()]
        else:
            ts = [t for t in _TIMERS if t["kind"] == kind]
            gone = sorted(ts, key=lambda t: t["end"])[:1]
        ids = {t["id"] for t in gone}
        _TIMERS[:] = [t for t in _TIMERS if t["id"] not in ids]
    _WAKE.set()
    hud_refresh()
    return gone


def _fire(t: dict) -> None:
    _fired.append(t)
    if t["kind"] == "pomodoro":
        _pomo_next(t)
        return
    what = f"{fmt(t['total'])} ka timer khatam" + (f" - {t['label']}" if t["label"] else "")
    ks.beep("reminder")
    ks.popup("Timer \u23f0", what)
    ks.log(f"SYS: Timer: {what}")
    ks.say_async(f"Timer khatam: {what}", urgent=True)
    ks.timing("timer fire", 0.0, what)


def tick() -> Optional[float]:
    """Fire what is due. -> seconds to the next one (None = nothing left)."""
    now = _clock()
    with _LOCK:
        due = [t for t in _TIMERS if t["end"] <= now]
        ids = {t["id"] for t in due}
        _TIMERS[:] = [t for t in _TIMERS if t["id"] not in ids]
        nxt = min((t["end"] for t in _TIMERS), default=None)
    for t in due:
        try:
            _fire(t)
        except Exception as e:
            ks._print(f"[timer] fire failed: {e}")
    if due:
        hud_refresh()
        with _LOCK:
            nxt = min((t["end"] for t in _TIMERS), default=None)
    return None if nxt is None else max(0.0, nxt - _clock())


def _loop() -> None:
    while True:
        try:
            wait = tick()
        except Exception as e:
            ks._print(f"[timer] tick failed: {e}")
            wait = 1.0
        _WAKE.clear()
        _WAKE.wait(timeout=(3600.0 if wait is None else min(wait + 0.02, 3600.0)))


def _ensure_thread() -> None:
    if _THREAD["t"] is None or not _THREAD["t"].is_alive():
        th = threading.Thread(target=_loop, name="kaka-u19-timer", daemon=True)
        _THREAD["t"] = th
        th.start()


# ------------------------------------------------------------------ stopwatch
def sw_start() -> str:
    if _SW["start"] is not None:
        return f"Stopwatch pehle se chal raha hai: {fmt(sw_value())}"
    _SW["start"] = _clock()
    hud_refresh()
    return "Stopwatch shuru." if not _SW["acc"] else f"Stopwatch phir se chalu ({fmt(_SW['acc'])} se)."


def sw_value() -> float:
    return _SW["acc"] + ((_clock() - _SW["start"]) if _SW["start"] is not None else 0.0)


def sw_stop() -> str:
    if _SW["start"] is None:
        return ("Stopwatch chal nahi raha." if not _SW["acc"]
                else f"Stopwatch ruka hua hai: {fmt(_SW['acc'])}")
    _SW["acc"] = sw_value()
    _SW["start"] = None
    hud_refresh()
    return f"Stopwatch roka: {fmt(_SW['acc'])}."


def sw_reset() -> str:
    _SW["start"], _SW["acc"] = None, 0.0
    hud_refresh()
    return "Stopwatch reset."


# ------------------------------------------------------------------ pomodoro
def pomo_start(work: int = 25, brk: int = 5, cycles: int = 4) -> str:
    cancel(all_=True, kind="pomodoro")
    _POMO.update(on=True, phase="work", cycle=1, cycles=max(1, min(12, cycles)),
                 work=max(1, work), brk=max(1, brk))
    add(work * 60, f"pomodoro padhai 1/{_POMO['cycles']}", kind="pomodoro")
    return (f"Pomodoro shuru: {work} minute padhai, phir {brk} minute break, "
            f"{_POMO['cycles']} round.")


def pomo_stop() -> str:
    was = _POMO["on"]
    _POMO.update(on=False, phase="", cycle=0)
    cancel(all_=True, kind="pomodoro")
    return "Pomodoro band kiya." if was else "Pomodoro chal nahi raha tha."


def _pomo_next(t: dict) -> None:
    if not _POMO["on"]:
        return
    ks.beep("reminder")
    if _POMO["phase"] == "work":
        if _POMO["cycle"] >= _POMO["cycles"]:
            _POMO.update(on=False, phase="")
            msg = f"Pomodoro poora: {_POMO['cycles']} round ho gaye. Shabaash, ab lamba break lo."
        else:
            _POMO["phase"] = "break"
            add(_POMO["brk"] * 60, f"pomodoro break {_POMO['cycle']}/{_POMO['cycles']}", kind="pomodoro")
            msg = f"{_POMO['work']} minute ho gaye - {_POMO['brk']} minute break lo."
    else:
        _POMO["cycle"] += 1
        _POMO["phase"] = "work"
        add(_POMO["work"] * 60, f"pomodoro padhai {_POMO['cycle']}/{_POMO['cycles']}", kind="pomodoro")
        msg = f"Break khatam - round {_POMO['cycle']} shuru, {_POMO['work']} minute padhai."
    ks.popup("Pomodoro \u23f0", msg)
    ks.log(f"SYS: Pomodoro: {msg}")
    ks.say_async(msg, urgent=True)


# ------------------------------------------------------------------ status text
def status_text() -> str:
    parts = []
    for t in running():
        name = t["label"] or f"{fmt(t['total'])} ka timer"
        parts.append(f"{name}: {fmt(t['left'])} bacha")
    if _SW["start"] is not None or _SW["acc"]:
        parts.append(f"stopwatch: {fmt(sw_value())}" + ("" if _SW["start"] is not None else " (ruka hua)"))
    return "; ".join(parts)


def hud_line() -> str:
    bits = []
    for t in running()[:2]:
        lab = (t["label"] or "timer")[:18]
        bits.append(f"\u23f2 {clock(t['left'])} {lab}")
    if _SW["start"] is not None or _SW["acc"]:
        bits.append(f"\u23f1 {clock(sw_value())}" + ("" if _SW["start"] is not None else " ||"))
    return "   ".join(bits)


# ------------------------------------------------------------------ HUD line
def _hud_build():
    """Qt thread: a small label above the Sleep button + a 1 s refresh."""
    if _HUD["label"] is not None:
        return
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QLabel
        j = ks.jarvis()
        win = j.ui._win
        btn = win._sleep_btn
        lay = btn.parentWidget().layout()
        lab = QLabel("")
        lab.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        lab.setStyleSheet("color: #ffd166; background: #101010; border: 1px solid #333;"
                          " border-radius: 3px; padding: 3px 6px;")
        lab.hide()
        idx = lay.indexOf(btn)
        lay.insertWidget(idx if idx >= 0 else lay.count(), lab)
        tm = QTimer(lab)
        tm.setInterval(1000)
        tm.timeout.connect(_hud_update)
        _HUD["label"], _HUD["qt"] = lab, tm
    except Exception as e:
        _HUD["label"] = False          # don't try again
        ks._print(f"[timer] HUD line not added: {e}")


def _hud_update():
    lab = _HUD["label"]
    if not lab:
        return
    try:
        line = hud_line()
        if line:
            lab.setText(line)
            if not lab.isVisible():
                lab.show()
            if not _HUD["qt"].isActive():
                _HUD["qt"].start()
        else:
            lab.hide()
            _HUD["qt"].stop()
    except Exception:
        pass


def hud_refresh() -> None:
    def _do():
        _hud_build()
        _hud_update()
    try:
        if ks.jarvis() is not None:
            ks.run_in_ui(_do)
    except Exception:
        pass
