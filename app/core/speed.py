"""
core/speed.py - Kaka update 14: speed helpers.

  poll(cond, timeout)  - check cond every `interval` s and return the moment it
                         is true (instead of a fixed sleep). Same ceiling as the
                         old sleep, so nothing waits less than it needs to.
  Timer / lap / done   - one "[time] ..." line per action in the activity log,
                         so a screenshot shows where the seconds went:
                         "[time] whatsapp_call: total 4.1s (foreground 0.0s, search 2.4s, ...)"
  load / save          - tiny JSON cache in F:\\Kaka\\Data\\Cache (startup data
                         that used to be re-read on every launch).
  prewarm()            - background thread at startup: imports and Windows
                         app lists are ready before the first command.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_TL = threading.local()


# ------------------------------------------------------------------ polling
def poll(cond: Callable[[], object], timeout: float, interval: float = 0.1,
         clock: Optional[Callable[[], float]] = None,
         sleep: Optional[Callable[[float], None]] = None) -> bool:
    """True as soon as cond() is truthy; False after `timeout` seconds.
    cond is checked once right away, then every `interval` s. clock/sleep are
    injectable (the callers pass their own module's time so tests can mock it)."""
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    end = clock() + max(0.0, float(timeout))
    while True:
        try:
            if cond():
                return True
        except Exception:
            pass
        now = clock()
        if now >= end:
            return False
        sleep(min(interval, max(0.0, end - now)) or interval)


# ------------------------------------------------------------------ timing
class Timer:
    """t = Timer("whatsapp_call", log); ...; t.lap("search"); ...; t.done()"""

    def __init__(self, action: str, log: Optional[Callable[[str], None]] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.action = action
        self.log = log
        self.clock = clock or time.perf_counter
        self.t0 = self.clock()
        self.last = self.t0
        self.steps: list = []
        self.finished = False
        _TL.timer = self

    def lap(self, step: str) -> float:
        now = self.clock()
        d = now - self.last
        self.last = now
        for i, (s, v) in enumerate(self.steps):
            if s == step:                       # same step twice -> add up
                self.steps[i] = (s, v + d)
                return d
        self.steps.append((step, d))
        return d

    def line(self, note: str = "") -> str:
        total = self.clock() - self.t0
        parts = ", ".join(f"{s} {v:.1f}s" for s, v in self.steps)
        return (f"[time] {self.action}: total {total:.1f}s"
                + (f" ({parts})" if parts else "") + (f" - {note}" if note else ""))

    def done(self, note: str = "") -> str:
        if self.finished:
            return ""
        self.finished = True
        if getattr(_TL, "timer", None) is self:
            _TL.timer = None
        text = self.line(note)
        print(f"[speed] {text}")
        try:                                   # update 16: kept in kaka-timing.txt
            from core import timing_log as _tl
            _tl.maybe(text)
        except Exception:
            pass
        if self.log:
            try:
                self.log(text)
            except Exception:
                pass
        return text


def current() -> Optional[Timer]:
    return getattr(_TL, "timer", None)


def lap(step: str) -> None:
    """Mark the end of a step on this thread's running Timer (no-op without one)."""
    t = current()
    if t is not None and not t.finished:
        try:
            t.lap(step)
        except Exception:
            pass


# ------------------------------------------------------------------ disk cache
def cache_dir() -> Optional[Path]:
    try:
        import kaka_paths
        d = kaka_paths.cache()
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


def load(name: str, max_age: float) -> Optional[dict]:
    """The cached JSON {"at": epoch, "data": ...} if younger than max_age s."""
    d = cache_dir()
    if d is None:
        return None
    try:
        obj = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
        if time.time() - float(obj.get("at", 0)) <= max_age:
            return obj
    except Exception:
        pass
    return None


def save(name: str, data) -> bool:
    d = cache_dir()
    if d is None:
        return False
    try:
        p = d / f"{name}.json"
        tmp = d / f"{name}.json.tmp"
        tmp.write_text(json.dumps({"at": time.time(), "data": data}), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False


def uia_fast_ok() -> bool:
    """Does this PC's UI Automation take a cache request (the fast window read)?"""
    try:
        from core import uia
        a, _m = uia._comtypes()
        return a is not None and a.CreateCacheRequest() is not None
    except Exception:
        return False


# ------------------------------------------------------------------ startup
_WARM = {"started": False}


def prewarm(log: Optional[Callable[[str], None]] = None) -> bool:
    """Warm the slow first-use things in the background (once per launch):
    pyautogui / pyperclip / psutil imports, the UI Automation type library,
    and Windows' installed-app list (from the disk cache, refreshed quietly)."""
    if _WARM["started"]:
        return False
    _WARM["started"] = True

    def _run():
        t0 = time.perf_counter()
        done = []
        for mod in ("psutil", "pyperclip", "pyautogui"):
            try:
                __import__(mod)
                done.append(mod)
            except Exception:
                pass
        try:
            import sys
            if sys.platform.startswith("win"):
                import comtypes.client  # type: ignore
                comtypes.client.GetModule("UIAutomationCore.dll")
                done.append("uia")
        except Exception:
            pass
        try:
            from core import winapps
            winapps.start_apps()
            winapps.shortcuts()
            done.append("apps")
        except Exception:
            pass
        print(f"[speed] prewarm {', '.join(done)} in {time.perf_counter() - t0:.1f}s")

    threading.Thread(target=_run, daemon=True, name="kaka-prewarm").start()
    return True
