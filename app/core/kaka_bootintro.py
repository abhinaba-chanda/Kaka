r"""
core/kaka_bootintro.py - Kaka update 20, feature 27: Iron Man boot intro.

On Kaka's FIRST start after Windows starts / he logs in (not on a Kaka
restart): a 3.5 s arc-reactor HUD boot animation (click / Esc skips it) + a
synth "power-up" sound made right here with stdlib wave (no copyrighted
audio) + Kaka says "Good morning/afternoon/evening Abhinaba. Systems online."
and a short status: time, battery, weather, pending reminders, notes.

Boot detection (Windows 10 Fast Startup keeps the uptime counter running
across "shutdowns", so uptime alone is not enough): the session start =
explorer.exe's start time; also the system uptime. If either is < 10 min and
this session has not had its intro yet (F:\Kaka\Data\boot_intro.json), the
intro plays - once per boot/login.

Merging: if the U18 morning update is due on this start, the intro claims it
(so the 8 AM loop does not run it again) and says one combined message:
intro + status + the morning update (weather only once). The old startup
briefing is suppressed on intro starts (JarvisLive._briefing_sent = True).
Sleep mode (Ctrl+1): nothing plays, nothing is said. Wake-word mode (not
awake): animation + sound + popup, no speech. Toggle: "boot intro band karo /
chalu karo" (actions/boot_intro.py).
"""
from __future__ import annotations

import math
import os
import struct
import threading
import time
import wave
from datetime import datetime
from typing import Optional

BOOT_WINDOW_S = 600.0
SAME_SESSION_TOL_S = 120.0
ANIM_MS = 3600

_STATE = {"decision": None, "j": None}

# test hooks
_now = datetime.now
_uptime_fn = None
_logon_age_fn = None


def _ks():
    from core import kaka_services as ks
    return ks


def _state_path():
    return _ks().data_path("boot_intro.json")


def load() -> dict:
    try:
        s = _ks().read_json(_state_path(), {})
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save(s: dict) -> bool:
    try:
        return _ks().write_json(_state_path(), s)
    except Exception:
        return False


def is_enabled(s: Optional[dict] = None) -> bool:
    s = load() if s is None else s
    return bool(s.get("enabled", True))


def set_enabled(on: bool) -> bool:
    s = load()
    s["enabled"] = bool(on)
    return save(s)


# ------------------------------------------------------------------ boot facts
def uptime_s() -> Optional[float]:
    if _uptime_fn is not None:
        return _uptime_fn()
    try:
        import ctypes
        f = ctypes.windll.kernel32.GetTickCount64
        f.restype = ctypes.c_ulonglong
        return f() / 1000.0
    except Exception:
        return None


def logon_age_s() -> Optional[float]:
    """Seconds since explorer.exe (the desktop shell) started = since he logged
    in. Survives Fast Startup, unlike the uptime counter."""
    if _logon_age_fn is not None:
        return _logon_age_fn()
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = u32.GetShellWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)      # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                return None
            ft = (c.dwHighDateTime << 32) | c.dwLowDateTime
            created = ft / 1e7 - 11644473600.0               # FILETIME -> unix
            return max(0.0, time.time() - created)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def decide(now_epoch: Optional[float] = None, state: Optional[dict] = None) -> dict:
    """-> {"play": bool, "why": str, "session": epoch of this boot/login}"""
    now_epoch = time.time() if now_epoch is None else now_epoch
    s = load() if state is None else state
    up, lg = uptime_s(), logon_age_s()
    ages = [a for a in (up, lg) if a is not None]
    if not ages:
        return {"play": False, "why": "boot time unknown", "session": None}
    age = min(ages)
    session = now_epoch - (lg if lg is not None else up)
    if not is_enabled(s):
        return {"play": False, "why": "boot intro band hai", "session": session}
    if age > BOOT_WINDOW_S:
        return {"play": False, "why": f"not a fresh boot ({int(age)} s since boot/login)", "session": session}
    last = s.get("last_session")
    if isinstance(last, (int, float)) and abs(float(last) - session) < SAME_SESSION_TOL_S:
        return {"play": False, "why": "already played this boot", "session": session}
    return {"play": True, "why": f"fresh boot ({int(age)} s)", "session": session}


def _morning_due(now: datetime) -> bool:
    try:
        from core import kaka_feeds as kf
        return bool(kf.due(now, kf.load_state()))
    except Exception:
        return False


def _claim_morning(now: datetime) -> bool:
    """Mark today's morning update as taken by the intro, so the U18 8 AM
    loop (first check ~22 s after start) does not say it a second time."""
    try:
        from core import kaka_feeds as kf
        s = kf.load_state()
        s["last_run"] = now.strftime("%Y-%m-%d")
        return kf.save_state(s)
    except Exception:
        return False


def bind(jarvis) -> dict:
    """main.py (JarvisLive.__init__, right after the update-18 services). Fast
    (a few ms): decides, marks the boot, suppresses the old briefing and
    starts the intro thread. Never raises."""
    t0 = time.perf_counter()
    try:
        d = decide()
        now = _now()
        d["merge_morning"] = False
        if d["play"]:
            s = load()
            s["last_session"] = d["session"]
            s["last_played"] = now.isoformat(timespec="seconds")
            save(s)
            try:
                jarvis._briefing_sent = True          # old startup briefing: not on intro starts
            except Exception:
                pass
            if _morning_due(now):
                d["merge_morning"] = _claim_morning(now)
            _STATE["j"] = jarvis
            threading.Thread(target=_run_intro, args=(d,), daemon=True,
                             name="kaka-u20-bootintro").start()
        d["bind_ms"] = (time.perf_counter() - t0) * 1000.0
        _STATE["decision"] = d
        try:
            _ks().log(f"[time] boot intro check: {d['bind_ms'] / 1000:.3f} s ({d['why']})")
        except Exception:
            pass
        return d
    except Exception as e:
        try:
            print(f"[KAKA] boot intro check failed: {e}")
        except Exception:
            pass
        return {"play": False, "why": f"error {e}"}


# ------------------------------------------------------------------ sound
def make_sound(path, rate: int = 22050) -> bool:
    """~1.6 s synth power-up: rising sweep + noise whoosh, then two beeps.
    Pure stdlib (math + wave). Original sound, no samples."""
    try:
        n = int(rate * 1.6)
        frames = bytearray()
        seed = 12345
        ph = 0.0
        for i in range(n):
            t = i / rate
            v = 0.0
            if t < 1.0:                               # sweep 120 -> 900 Hz + whoosh
                f = 120.0 + 780.0 * (t ** 1.7)
                ph += 2 * math.pi * f / rate
                env = min(1.0, t / 0.08) * (1.0 - max(0.0, t - 0.75) / 0.25)
                seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
                noise = (seed / 0x3FFFFFFF - 1.0)
                v = env * (0.45 * math.sin(ph) + 0.18 * math.sin(2 * ph) +
                           0.22 * noise * math.sin(math.pi * t))
            for (b0, f) in ((1.05, 1318.5), (1.28, 1760.0)):   # two HUD beeps
                if b0 <= t < b0 + 0.14:
                    e = 1.0 - (t - b0) / 0.14
                    v += 0.5 * e * math.sin(2 * math.pi * f * (t - b0))
            v = max(-1.0, min(1.0, v * 0.8))
            frames += struct.pack("<h", int(v * 32000))
        tmp = str(path) + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def sound_path():
    return _ks().data_path("Cache", "kaka_boot_v1.wav")


def play_sound() -> bool:
    try:
        p = sound_path()
        if not os.path.exists(p) and not make_sound(p):
            return False
        import winsound
        winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ status
def _battery_line() -> str:
    try:
        from core import kaka_health as kh               # update 19 PC health
        b = kh.battery()
        if not b.get("has"):
            return ""
        if b.get("pct") is None:
            return ""
        return f"battery {b['pct']}%" + (" (charging)" if b.get("charging") else "")
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class SPS(ctypes.Structure):
            _fields_ = [("ac", wintypes.BYTE), ("flag", wintypes.BYTE), ("pct", wintypes.BYTE),
                        ("sf", wintypes.BYTE), ("t", wintypes.DWORD), ("ft", wintypes.DWORD)]
        s = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            pct, flag = s.pct & 0xFF, s.flag & 0xFF
            if flag != 128 and pct != 255:
                return f"battery {pct}%" + (" (charging)" if (s.ac & 0xFF) == 1 else "")
    except Exception:
        pass
    return ""


def _reminders_line() -> str:
    try:
        from core import kaka_reminders as kr
        n = len(kr.pending())
        return f"{n} reminder baaki" if n else "koi reminder baaki nahi"
    except Exception:
        return ""


def _notes_line() -> str:
    n = None
    try:
        from core import kaka_notes as kn                 # update 19 notes
        n = int(kn.count())
    except Exception:
        try:
            data = _ks().read_json(_ks().data_path("notes.json"), None)
            if isinstance(data, dict):
                data = data.get("notes")
            if isinstance(data, list):
                n = sum(1 for x in data if not (isinstance(x, dict) and x.get("done")))
        except Exception:
            n = None
    if n is None:
        return ""
    return f"{n} notes" if n else "koi note nahi"


def _weather_line() -> str:
    try:
        from core import kaka_feeds as kf
        w = kf.weather("")
        return kf.weather_text(w, "today") if w else ""
    except Exception:
        return ""


def _kaka_user_name() -> str:          # KAKA: user name (app/kaka_user.py)
    try:
        import kaka_user
    except Exception:
        try:
            import os as _o, sys as _s
            _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
            import kaka_user
        except Exception:
            return "dost"
    try:
        return kaka_user.user_name()
    except Exception:
        return "dost"


def greeting(now: datetime) -> str:
    h = now.hour
    part = "morning" if 4 <= h < 12 else ("afternoon" if 12 <= h < 17 else "evening")
    g = f"Good {part} {_kaka_user_name()}. Systems online."
    try:                                   # update 21: "Welcome back" when the camera saw HIM
        from core import kaka_face as _f21
        return _f21.intro_greeting(g)
    except Exception:
        return g


def compose(now: datetime, merge_morning: bool, parts: dict) -> str:
    """parts: battery / reminders / notes / weather / morning (strings, may be '')."""
    status = [f"Time {now.strftime('%I:%M %p').lstrip('0')}"]
    for k in ("battery", "reminders", "notes"):
        if parts.get(k):
            status.append(parts[k])
    text = greeting(now) + " " + ", ".join(status) + "."
    if merge_morning and parts.get("morning"):
        m = parts["morning"]
        try:
            from core import kaka_feeds as kf
            g = kf.greeting(now)
            if m.startswith(g):
                m = m[len(g):].strip()
        except Exception:
            pass
        text += " Aaj ka update: " + m
    elif parts.get("weather"):
        text += " " + parts["weather"]
    return text


def gather(now: datetime, merge_morning: bool, budget: float = 6.0) -> dict:
    jobs = {"battery": _battery_line, "reminders": _reminders_line, "notes": _notes_line}
    if merge_morning:
        def _m():
            from core import kaka_feeds as kf
            return kf.compose_morning(now)[0]
        jobs["morning"] = _m
    else:
        jobs["weather"] = _weather_line
    out = {k: "" for k in jobs}

    def run(k, fn):
        try:
            out[k] = fn() or ""
        except Exception:
            out[k] = ""
    ts = [threading.Thread(target=run, args=(k, fn), daemon=True) for k, fn in jobs.items()]
    for t in ts:
        t.start()
    end = time.monotonic() + budget
    for t in ts:
        t.join(max(0.0, end - time.monotonic()))
    return dict(out)


# ------------------------------------------------------------------ HUD animation
def _hud():
    try:
        return _ks().jarvis().ui._win.hud
    except Exception:
        try:
            return _STATE["j"].ui._win.hud
        except Exception:
            return None


def make_overlay(parent, duration_ms: int = ANIM_MS):
    """Arc-reactor boot overlay painted over the HUD canvas. Click / Esc /
    any key skips. Deletes itself when done."""
    from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QElapsedTimer
    from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QRadialGradient, QBrush
    from PyQt6.QtWidgets import QWidget

    class BootOverlay(QWidget):
        def __init__(self, p):
            super().__init__(p)
            self.setObjectName("kakaBootIntro")
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setGeometry(p.rect())
            self.clock = QElapsedTimer()
            self.clock.start()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(33)
            self.done = False
            self.show()
            self.raise_()

        def _tick(self):
            if self.parentWidget() is not None and self.geometry() != self.parentWidget().rect():
                self.setGeometry(self.parentWidget().rect())
            if self.clock.elapsed() >= duration_ms:
                self.finish()
            else:
                self.update()

        def finish(self):
            if self.done:
                return
            self.done = True
            self.timer.stop()
            p = self.parentWidget()
            self.hide()
            if p is not None:
                p.update()
            self.deleteLater()

        def mousePressEvent(self, e):
            self.finish()

        def keyPressEvent(self, e):
            self.finish()

        def paintEvent(self, e):
            t = self.clock.elapsed() / 1000.0
            T = duration_ms / 1000.0
            qp = QPainter(self)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            fade = min(1.0, (T - t) / 0.5) if t > T - 0.5 else 1.0
            qp.fillRect(self.rect(), QColor(2, 8, 14, int(235 * fade)))
            cx, cy = w / 2.0, h / 2.0 - 10
            R = min(w, h) * 0.24
            cyan = QColor(77, 208, 225)
            # core glow
            g = QRadialGradient(QPointF(cx, cy), R * 0.55)
            a = int(200 * fade * min(1.0, t / 0.6))
            g.setColorAt(0.0, QColor(220, 250, 255, a))
            g.setColorAt(0.35, QColor(77, 208, 225, int(a * 0.7)))
            g.setColorAt(1.0, QColor(0, 40, 60, 0))
            qp.setBrush(QBrush(g))
            qp.setPen(Qt.PenStyle.NoPen)
            qp.drawEllipse(QPointF(cx, cy), R * 0.55, R * 0.55)
            # rings drawing in, rotating segments
            for i, (rf, speed, segs, width) in enumerate(((1.0, 40, 12, 3.0), (0.78, -70, 8, 2.0),
                                                           (0.58, 110, 3, 4.0), (1.18, 15, 36, 1.0))):
                prog = max(0.0, min(1.0, (t - 0.15 * i) / 0.9))
                if prog <= 0:
                    continue
                col = QColor(cyan)
                col.setAlpha(int(230 * fade))
                pen = QPen(col, width)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                qp.setPen(pen)
                qp.setBrush(Qt.BrushStyle.NoBrush)
                rr = R * rf
                rect = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
                span = 360.0 / segs
                for s in range(segs):
                    start = s * span + t * speed
                    qp.drawArc(rect, int(start * 16), int(span * 0.62 * prog * 16))
            # sweeping scanline
            sy = (t * 0.9 % 1.0) * h
            sc = QColor(77, 208, 225, int(70 * fade))
            qp.setPen(QPen(sc, 2))
            qp.drawLine(0, int(sy), w, int(sy))
            qp.setPen(QPen(QColor(77, 208, 225, int(18 * fade)), 1))
            for yy in range(0, h, 4):
                qp.drawLine(0, yy, w, yy)
            # text
            qp.setPen(QColor(200, 245, 255, int(255 * fade)))
            f = QFont("Courier New", max(9, int(R / 9)), QFont.Weight.Bold)
            qp.setFont(f)
            title = "KAKA  //  ABHINABA CHANDA"
            n = int(len(title) * min(1.0, t / 1.2))
            qp.drawText(QRectF(0, cy + R * 1.3, w, 30), int(Qt.AlignmentFlag.AlignHCenter), title[:n])
            f2 = QFont("Courier New", max(7, int(R / 14)))
            qp.setFont(f2)
            pct = int(100 * min(1.0, t / (T * 0.8)))
            line = "SYSTEMS ONLINE" if pct >= 100 else f"BOOTING  {pct:3d}%"
            qp.setPen(QColor(77, 208, 225, int(230 * fade)))
            qp.drawText(QRectF(0, cy + R * 1.3 + 26, w, 22), int(Qt.AlignmentFlag.AlignHCenter), line)
            bw = w * 0.42
            qp.drawRect(QRectF(cx - bw / 2, cy + R * 1.3 + 50, bw, 5))
            qp.fillRect(QRectF(cx - bw / 2, cy + R * 1.3 + 50, bw * pct / 100.0, 5), QColor(77, 208, 225, int(200 * fade)))
            qp.setPen(QColor(120, 160, 170, int(160 * fade)))
            qp.drawText(QRectF(0, h - 22, w, 18), int(Qt.AlignmentFlag.AlignHCenter), "click / Esc = skip")
            qp.end()

    return BootOverlay(parent)


def show_animation() -> bool:
    def _do():
        hud = _hud()
        if hud is None:
            return
        try:
            make_overlay(hud)
        except Exception as e:
            _ks()._print(f"[bootintro] animation failed: {e}")
    try:
        return bool(_ks().run_in_ui(_do))
    except Exception:
        return False


# ------------------------------------------------------------------ the run
def _asleep(j) -> bool:
    try:
        return bool(getattr(j, "_deep_sleep", False))
    except Exception:
        return False



# ------------------------------------------------------------------ 21.2 Windows voice fallback
def _win_say(text: str) -> bool:
    """Speak with Windows' own voice (System.Speech) when Kaka's live voice
    is asleep or not connected yet. Hidden window, 40 s cap."""
    if os.name != "nt" or not text:
        return False
    try:
        import subprocess
        t = str(text).replace("'", " ").replace("\r", " ").replace("\n", " ")[:900]
        ps = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.Rate = 0; $s.Speak('" + t + "')")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       timeout=40, creationflags=0x08000000,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def _run_intro(d: dict) -> None:
    ks = _ks()
    j = _STATE.get("j")
    t0 = time.monotonic()
    try:
        time.sleep(0.6)                               # let the HUD paint once
        if _asleep(j):
            ks.log("SYS: Boot intro skipped - sleep mode.")
            if d.get("merge_morning"):
                _release_morning()
            return
        game = ks.in_game_mode()
        if not game:
            play_sound()
            show_animation()
        now = _now()
        parts = gather(now, bool(d.get("merge_morning")))
        text = compose(now, bool(d.get("merge_morning")), parts)
        ks.popup("Systems online", text)
        # let the animation finish before Kaka talks over it
        wait = ANIM_MS / 1000.0 - (time.monotonic() - t0) + 0.2
        if wait > 0 and not game:
            time.sleep(wait)
        if _asleep(j):
            ks.log("SYS: Boot intro: sleep mode on, isliye bola nahi.")
            return
        awake = bool(getattr(j, "_awake", True)) if j is not None else True
        if not awake:
            ks.log("SYS: Boot intro (wake-word mode): Windows voice se bol raha hoon.")
            res = "wake-word -> " + ("windows voice" if _win_say(text) else "popup-only")
            if d.get("merge_morning"):
                _release_morning()          # the U18 loop says it the normal way
                d["merge_morning"] = False
        else:
            instr = ("Pehle bilkul ye bolo: '" + greeting(now) + "' Phir ye status chhote "
                     "vakyon mein sunao: " + text[len(greeting(now)):].strip())
            res = ks.say(instr, brief=False)
            if res != "spoken":                       # 21.2: voice not ready -> Windows voice
                res = f"{res} -> " + ("windows voice" if _win_say(text) else "silent")
        ks.timing("boot intro", time.monotonic() - t0,
                  f"{res}, morning {'merged' if d.get('merge_morning') else 'no'}")
        if d.get("merge_morning"):
            ks.log("SYS: Morning update boot intro ke saath bol diya.")
    except Exception as e:
        try:
            ks.log(f"SYS: Boot intro failed: {e}")
        except Exception:
            pass


def _release_morning() -> None:
    """Sleeping at boot: give the morning update back to the U18 loop (it
    will say it after he wakes Kaka, if still before 11)."""
    try:
        from core import kaka_feeds as kf
        s = kf.load_state()
        s.pop("last_run", None)
        kf.save_state(s)
    except Exception:
        pass


def selfcheck() -> tuple:
    try:
        p = sound_path()
        if not os.path.exists(p):
            make_sound(p)
        ok = os.path.exists(p) and os.path.getsize(p) > 40000
        up = uptime_s()
        lg = logon_age_s()
        on = is_enabled()
        msg = (f"next PC start pe chalega ({'on' if on else 'band - boot intro chalu karo'})"
               + ("" if ok else ", sound file nahi bani (intro bina sound)"))
        if up is None and lg is None:
            return False, "boot time Windows se nahi mila"
        return True, msg
    except Exception as e:
        return False, str(e)
