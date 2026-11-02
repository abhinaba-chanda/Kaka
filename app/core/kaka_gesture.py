r"""
core/kaka_gesture.py - Kaka update 20, feature 28: webcam hand gestures.

OFF by default (and after every Kaka restart). "gesture mode on" / the HUD
hand button switches the laptop webcam on in a background thread:
    320x240, ~10 fps (6 fps while no hand is seen), MediaPipe Hand Landmarker
    (mediapipe >= 0.10.30 ships a py3-none-win_amd64 wheel, so it runs on
    Python 3.13; model hand_landmarker.task in F:\Kaka\Data\Models).
Gestures (hold ~0.6 s, then 1.5 s cooldown):
    open palm      -> play / pause (media key)
    thumbs up      -> next track
    thumbs down    -> previous track
    one finger up  -> volume +10   (repeats while held)
    one finger down-> volume -10   (repeats while held)
    victory (V)    -> Kaka listen: wakes him from sleep / unmutes the mic
Camera honesty: Kaka says "camera on" / "camera off" every time, a camera
badge sits on the HUD while it is on, it switches off by itself after 10 min
without a hand, and while game mode is on (back on when the game closes).
The classifier is pure maths on the 21 landmarks so it is unit-tested
without a camera. Nothing here ever raises into Kaka.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Callable, Optional, Sequence

HOLD_S = 0.6
COOLDOWN_S = 1.5
GAP_S = 0.3                 # one missed frame inside a hold is forgiven
IDLE_OFF_S = 600.0          # 10 min without a hand -> camera off
FPS_ACTIVE = 10.0
FPS_IDLE = 6.0
FRAME_W, FRAME_H = 320, 240
REPEATABLE = {"point_up", "point_down"}
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_SHA256 = "FBC2A30080C3C557093B5DDFC334698132EB341044CCEE322CCF8BCF3607CDE1"
MODEL_SIZE = 7819105

ICON = {"palm": "\u270b", "thumbs_up": "\U0001f44d", "thumbs_down": "\U0001f44e",
        "point_up": "\u261d\ufe0f", "point_down": "\U0001f447", "victory": "\u270c\ufe0f"}
WHAT = {"palm": "play/pause", "thumbs_up": "agla gaana", "thumbs_down": "pichhla gaana",
        "point_up": "volume +10", "point_down": "volume -10", "victory": "Kaka sun raha hai"}


# ------------------------------------------------------------------ classifier
def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def classify(lm: Sequence[Sequence[float]]) -> Optional[str]:
    """21 (x, y[, z]) landmarks in pixels (y grows downwards) -> gesture name
    or None. Rotation-tolerant: uses distance ratios from the wrist, so a
    tilted hand still counts; direction checks only where the gesture IS a
    direction (thumbs up/down, finger up/down)."""
    try:
        if lm is None or len(lm) < 21:
            return None
        P = [(float(p[0]), float(p[1])) for p in lm[:21]]
        w = P[0]
        palm = _d(w, P[9])
        if palm <= 1e-6:
            return None

        def ratio(tip):
            pip = _d(P[tip - 2], w)
            return _d(P[tip], w) / pip if pip > 1e-6 else 0.0
        r = [ratio(t) for t in (8, 12, 16, 20)]
        ext = [x > 1.12 for x in r]
        curl = [x < 0.98 for x in r]
        th_out = _d(P[4], P[9]) / palm > 0.55 and _d(P[4], P[5]) > _d(P[3], P[5]) * 1.1

        # victory: index + middle out, ring + pinky folded
        if ext[0] and ext[1] and curl[2] and curl[3]:
            return "victory"
        # open palm: all four fingers out
        if all(ext):
            return "palm"
        # one finger: index out, others folded -> up / down by its direction
        if ext[0] and curl[1] and curl[2] and curl[3]:
            dx, dy = P[8][0] - P[5][0], P[8][1] - P[5][1]
            if abs(dy) > abs(dx) * 1.2:
                return "point_up" if dy < 0 else "point_down"
            return None
        # thumbs: all four fingers folded, thumb out and vertical
        if all(curl) and th_out:
            dx, dy = P[4][0] - P[2][0], P[4][1] - P[2][1]
            if abs(dy) > abs(dx) * 1.3:
                if dy < 0 and P[4][1] < min(P[5][1], P[9][1]):
                    return "thumbs_up"
                if dy > 0 and P[4][1] > max(P[5][1], P[9][1]):
                    return "thumbs_down"
        return None
    except Exception:
        return None


class Debouncer:
    """feed(label, t) -> label to act on, or None.
    A gesture fires once it has been held HOLD_S (gaps < GAP_S forgiven);
    after any fire nothing fires for COOLDOWN_S; palm / thumbs / V must be
    let go before they can fire again (so a held palm doesn't flip
    play-pause-play), finger up/down repeat while held (volume ramps)."""

    def __init__(self, hold: float = HOLD_S, cooldown: float = COOLDOWN_S, gap: float = GAP_S):
        self.hold, self.cooldown, self.gap = hold, cooldown, gap
        self.cur = None
        self.since = 0.0
        self.last_seen = 0.0
        self.last_fire = -1e9
        self.fired_label = None     # needs release before firing again

    def feed(self, label: Optional[str], t: float) -> Optional[str]:
        if label is None:
            if self.cur is not None and t - self.last_seen > self.gap:
                self.cur = None
                self.fired_label = None
            return None
        if label != self.cur:
            self.cur = label
            self.since = t
        self.last_seen = t
        if t - self.since < self.hold or t - self.last_fire < self.cooldown:
            return None
        if self.fired_label == label and label not in REPEATABLE:
            return None
        self.last_fire = t
        self.fired_label = label
        self.since = t if label in REPEATABLE else self.since
        return label


# ------------------------------------------------------------------ services
def _ks():
    from core import kaka_services as ks
    return ks


def _state_path():
    return _ks().data_path("gestures.json")


def model_path():
    return _ks().data_path("Models", "hand_landmarker.task")


def _sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def model_ok(path=None) -> bool:
    try:
        p = path or model_path()
        return os.path.getsize(p) == MODEL_SIZE and _sha256(p) == MODEL_SHA256
    except Exception:
        return False


def deps_ok() -> tuple:
    """(ok, reason). Cheap: looks for the packages without importing them."""
    try:
        import importlib.util as u
        miss = [n for n in ("mediapipe", "cv2", "numpy", "matplotlib") if u.find_spec(n) is None]
        if miss:
            return False, "python package missing: " + ", ".join(miss)
        return True, ""
    except Exception as e:
        return False, str(e)


def download_model(timeout: float = 90.0) -> tuple:
    """Fetch hand_landmarker.task (7.8 MB) once, verify sha256. (ok, msg)."""
    p = model_path()
    if model_ok(p):
        return True, "model already there"
    tmp = str(p) + ".part"
    try:
        import urllib.request
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Kaka/20"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
            while True:
                if time.monotonic() - t0 > timeout:
                    raise TimeoutError(f"download took > {int(timeout)} s")
                b = r.read(1 << 16)
                if not b:
                    break
                f.write(b)
        if not model_ok(tmp):
            os.remove(tmp)
            return False, "model download corrupt (sha256 mismatch)"
        os.replace(tmp, p)
        return True, "model downloaded"
    except Exception as e:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, f"model download failed: {e}"


def selfcheck() -> tuple:
    ok, why = deps_ok()
    if not ok:
        return False, why
    if not model_ok():
        return False, "hand model not downloaded"
    return True, "MediaPipe Hands ready (camera OFF until you say 'gesture mode on')"


# ------------------------------------------------------------------ actions
def _act(label: str) -> str:
    """Do what the gesture means. Returns a short log text."""
    try:
        if label == "victory":
            return _listen()
        from core import kaka_pc as pc
        if label == "palm":
            return "play/pause" if pc.media_key("play_pause") else "play/pause key failed"
        if label == "thumbs_up":
            return "next" if pc.media_key("next") else "next key failed"
        if label == "thumbs_down":
            return "previous" if pc.media_key("previous") else "previous key failed"
        if label in ("point_up", "point_down"):
            r = pc.volume_step(10 if label == "point_up" else -10)
            lv = r.get("level")
            return f"volume {lv}%" if lv is not None else ("volume step" if r.get("ok") else "volume failed")
    except Exception as e:
        return f"{label} failed: {e}"
    return label


def _listen() -> str:
    ks = _ks()
    j = ks.jarvis()
    did = []
    try:
        if j is not None and getattr(j, "_deep_sleep", False):
            j.deep_wake(reason="gesture V")
            did.append("jaga diya")
        elif j is not None and not getattr(j, "_awake", True):
            try:
                from core.wake_word import chime
                chime()
            except Exception:
                pass
            j.wake(reason="gesture V")
            did.append("sun raha hoon")
    except Exception as e:
        did.append(f"wake failed: {e}")
    try:
        if ks.mic_muted():
            ks.set_mic_muted(False)
            did.append("mic on")
    except Exception:
        pass
    return ", ".join(did) or "already listening"


# ------------------------------------------------------------------ HUD
class _Hud:
    """Hand button (toggle) + camera badge + gesture icon on the HUD face.
    Children of the HUD canvas, created on the Qt thread, never in a layout."""
    btn = None
    badge = None
    icon = None
    flash_until = 0.0


def _hud_canvas():
    try:
        return _ks().jarvis().ui._win.hud
    except Exception:
        return None


def _place():
    hud = _hud_canvas()
    if hud is None:
        return
    w = hud.width()
    try:
        if _Hud.btn is not None:
            _Hud.btn.move(w - _Hud.btn.width() - 10, 10)
        if _Hud.badge is not None:
            _Hud.badge.adjustSize()
            _Hud.badge.move(w - _Hud.badge.width() - 10, 46)
        if _Hud.icon is not None:
            _Hud.icon.move(w - _Hud.icon.width() - 10, 70)
    except Exception:
        pass


def install_hud() -> None:
    """Called on the Qt thread (via ks.run_in_ui) 2 s after start."""
    hud = _hud_canvas()
    if hud is None or _Hud.btn is not None:
        return
    from PyQt6.QtCore import QObject, QEvent, Qt
    from PyQt6.QtWidgets import QLabel, QPushButton
    b = QPushButton("\u270b", hud)
    b.setObjectName("kakaGestureBtn")
    b.setFixedSize(30, 30)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip("Gesture mode (webcam) - click / 'gesture mode on'")
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.clicked.connect(lambda: threading.Thread(target=toggle, daemon=True,
                                               name="kaka-u20-gesture-toggle").start())
    badge = QLabel("\U0001f4f7 GESTURE", hud)
    badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    badge.setStyleSheet("color:#ffb300;background:rgba(0,0,0,150);border:1px solid #ffb300;"
                        "border-radius:4px;padding:1px 5px;font:bold 8pt 'Courier New';")
    badge.hide()
    icon = QLabel("", hud)
    icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    icon.setFixedSize(48, 48)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setStyleSheet("background:rgba(0,0,0,120);border-radius:24px;"
                       "font:26pt 'Segoe UI Emoji';")
    icon.hide()
    _Hud.btn, _Hud.badge, _Hud.icon = b, badge, icon
    _style_btn(False)

    class _F(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Resize:
                _place()
            return False
    _Hud.filt = _F(hud)
    hud.installEventFilter(_Hud.filt)
    b.show()
    b.raise_()
    _place()


def _style_btn(on: bool) -> None:
    b = _Hud.btn
    if b is None:
        return
    col = "#ffb300" if on else "#4dd0e1"
    bg = "rgba(255,179,0,60)" if on else "rgba(0,0,0,110)"
    b.setStyleSheet(f"QPushButton{{color:{col};background:{bg};border:1px solid {col};"
                    f"border-radius:15px;font:13pt 'Segoe UI Emoji';}}"
                    f"QPushButton:hover{{background:rgba(77,208,225,70);}}")


def _hud_state(on: bool) -> None:
    def _do():
        try:
            _style_btn(on)
            if _Hud.badge is not None:
                _Hud.badge.setVisible(on)
                _Hud.badge.raise_()
            if not on and _Hud.icon is not None:
                _Hud.icon.hide()
            _place()
        except Exception:
            pass
    _ks().run_in_ui(_do)


def _hud_flash(label: str) -> None:
    def _do():
        try:
            if _Hud.icon is None:
                return
            _Hud.icon.setText(ICON.get(label, "?"))
            _Hud.icon.show()
            _Hud.icon.raise_()
            _place()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1200, _Hud.icon.hide)
        except Exception:
            pass
    _ks().run_in_ui(_do)


# ------------------------------------------------------------------ engine
class _Engine:
    lock = threading.Lock()
    thread: Optional[threading.Thread] = None
    stop = threading.Event()
    on = False
    paused_for_game = False
    last_hand = 0.0
    fired = 0
    cpu = {"busy": 0.0, "wall": 0.0, "frames": 0}


def is_on() -> bool:
    return bool(_Engine.on)


# test hooks: replace the camera / detector factories
_open_camera: Optional[Callable] = None
_make_detector: Optional[Callable] = None
_now = time.monotonic


def _default_camera():
    import cv2
    cap = None
    for backend in (getattr(cv2, "CAP_DSHOW", 0), getattr(cv2, "CAP_MSMF", 0), 0):
        try:
            cap = cv2.VideoCapture(0, backend)
            if cap is not None and cap.isOpened():
                break
        except Exception:
            cap = None
    if cap is None or not cap.isOpened():
        raise RuntimeError("webcam nahi khula (koi aur app camera use kar raha hai? ya camera privacy setting band hai)")
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    def read():
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        if frame.shape[1] != FRAME_W:
            frame = cv2.resize(frame, (FRAME_W, FRAME_H))
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return read, cap.release


def _default_detector():
    import sys
    if "matplotlib" not in sys.modules:            # mediapipe imports pyplot; never a GUI backend
        os.environ["MPLBACKEND"] = "Agg"
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks.python import vision, BaseOptions
    opts = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path())),
        running_mode=vision.RunningMode.VIDEO, num_hands=1,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5)
    lm = vision.HandLandmarker.create_from_options(opts)
    t0 = time.monotonic()
    last = [-1]

    def detect(rgb):
        ts = int((time.monotonic() - t0) * 1000)
        if ts <= last[0]:
            ts = last[0] + 1
        last[0] = ts
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        r = lm.detect_for_video(img, ts)
        if not r.hand_landmarks:
            return None
        h, w = rgb.shape[0], rgb.shape[1]
        return [(p.x * w, p.y * h) for p in r.hand_landmarks[0]]
    return detect, lm.close


def _run(stop: threading.Event) -> None:
    ks = _ks()
    release = close = None
    reason = ""
    try:
        t0 = time.monotonic()
        read, release = (_open_camera or _default_camera)()
        detect, close = (_make_detector or _default_detector)()
        ks.timing("gesture camera start", time.monotonic() - t0)
        deb = Debouncer()
        _Engine.last_hand = _now()
        stat_t = _now()
        busy = 0.0
        frames = 0
        pcpu0 = time.process_time()
        while not stop.is_set():
            if ks.in_game_mode():
                reason = "game"
                break
            tick = _now()
            c0 = time.perf_counter()
            rgb = read()
            pts = detect(rgb) if rgb is not None else None
            label = classify(pts) if pts else None
            if pts:
                _Engine.last_hand = tick
            fire = deb.feed(label, tick)
            busy += time.perf_counter() - c0
            frames += 1
            if fire:
                _Engine.fired += 1
                _hud_flash(fire)
                what = _act(fire)
                ks.log(f"SYS: Gesture {ICON.get(fire, '')} {fire} -> {what}")
            if tick - _Engine.last_hand > IDLE_OFF_S:
                reason = "idle"
                break
            if tick - stat_t >= 60:
                wall = tick - stat_t
                proc = (time.process_time() - pcpu0) / max(wall, 1e-6) / max(1, os.cpu_count() or 1) * 100.0
                ks.timing("gesture frame", busy / max(frames, 1),
                          f"{frames / wall:.1f} fps; whole Kaka process {proc:.1f}% of total CPU")
                _Engine.cpu = {"busy": busy, "wall": wall, "frames": frames, "proc_pct": proc}
                stat_t, busy, frames, pcpu0 = tick, 0.0, 0, time.process_time()
            fps = FPS_ACTIVE if tick - _Engine.last_hand < 2.0 else FPS_IDLE
            left = 1.0 / fps - (_now() - tick)
            if left > 0:
                stop.wait(left)
    except Exception as e:
        reason = "error"
        ks.log(f"SYS: Gesture mode band - {e}")
        ks.say_async(f"Gesture mode nahi chal paya: {e}. Camera band hai.")
    finally:
        for fn in (close, release):
            try:
                if fn:
                    fn()
            except Exception:
                pass
    _after_stop(reason)


def _after_stop(reason: str) -> None:
    ks = _ks()
    with _Engine.lock:
        if _Engine.thread is not None and _Engine.thread is not threading.current_thread():
            return
        _Engine.thread = None
        user_off = _Engine.stop.is_set()
        _Engine.on = False
    if user_off:
        return
    _hud_state(False)
    if reason == "game":
        _Engine.paused_for_game = True
        ks.log("SYS: Game mode - camera off (gesture), game band hote hi wapas on.")
        ks.popup("Gesture", "Game mode: camera off. Game band hote hi wapas on.")
    elif reason == "idle":
        ks.log("SYS: 10 min koi haath nahi dikha - camera off (gesture mode band).")
        ks.say_async("10 minute se koi ishara nahi, isliye camera off kar diya. "
                     "Chahiye toh gesture mode on bolo.")


def start(announce: bool = True) -> str:
    ks = _ks()
    if ks.in_game_mode():
        return "Game mode chal raha hai, isliye camera abhi on nahi karunga. Game band hone ke baad bolo."
    ok, why = deps_ok()
    if not ok:
        return f"Gesture mode nahi chal sakta: {why}. Kaka-Update-20.bat dobara chalao (internet ke saath)."
    if not model_ok():
        ok, msg = download_model()
        if not ok:
            return f"Gesture mode nahi chal sakta: haath ka model nahi mila ({msg})."
    with _Engine.lock:
        if _Engine.on:
            return "Gesture mode pehle se on hai, camera on hai."
        _Engine.stop = threading.Event()
        _Engine.on = True
        _Engine.paused_for_game = False
        t = threading.Thread(target=_run, args=(_Engine.stop,), daemon=True, name="kaka-u20-gesture")
        _Engine.thread = t
    t.start()
    _hud_state(True)
    ks.log("SYS: Camera on - gesture mode chalu (320x240, ~10 fps).")
    return ("Camera on. Gesture mode chalu: haath khula = play/pause, angootha upar = agla gaana, "
            "neeche = pichhla, ungli upar/neeche = volume, V = main sun raha hoon. "
            "10 minute koi haath nahi dikha toh camera khud band.")


def stop(say: bool = False) -> str:
    with _Engine.lock:
        was = _Engine.on or _Engine.paused_for_game
        _Engine.paused_for_game = False
        _Engine.stop.set()
        t = _Engine.thread
    if t is not None and t is not threading.current_thread():
        t.join(timeout=3.0)
    with _Engine.lock:
        _Engine.on = False
        _Engine.thread = None
    _hud_state(False)
    if not was:
        return "Gesture mode pehle se band hai, camera off hai."
    _ks().log("SYS: Camera off - gesture mode band.")
    return "Camera off. Gesture mode band."


def toggle() -> str:
    msg = stop() if (_Engine.on or _Engine.paused_for_game) else start()
    try:
        _ks().say_async(msg)
    except Exception:
        pass
    return msg


def status() -> str:
    if _Engine.on:
        return f"Gesture mode on hai, camera on hai ({_Engine.fired} ishare ab tak)."
    if _Engine.paused_for_game:
        return "Gesture mode game ki wajah se ruka hai (camera off), game band hote hi wapas on."
    return "Gesture mode band hai, camera off hai."


def _game_watch():
    """Resume after game mode ends (only if it was paused for the game)."""
    ks = _ks()
    while True:
        time.sleep(3.0)
        try:
            if _Engine.paused_for_game and not ks.in_game_mode():
                _Engine.paused_for_game = False
                msg = start()
                if msg.startswith("Camera on"):
                    ks.say_async("Game band ho gaya, camera on - gesture mode wapas chalu.")
        except Exception:
            pass


def _on_start():
    try:
        _ks().run_in_ui(install_hud)
    except Exception:
        pass
    threading.Thread(target=_game_watch, daemon=True, name="kaka-u20-gesture-game").start()
    try:
        import atexit
        atexit.register(lambda: _Engine.stop.set())
    except Exception:
        pass


try:
    from core import kaka_services as _ks0
    _ks0.on_start(_on_start)
except Exception:
    pass
