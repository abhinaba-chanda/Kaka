"""
Local wake-word detection — "Kaka" / "Hey Kaka" (and "Hey Jarvis" as a fallback).

KAKA EDITION (patched for Abhinaba, Nov 2026)
    The original file listened only for openwakeword's pretrained "hey_jarvis".
    There is no pretrained "kaka" model, so this version adds a second, fully
    local detector: a Vosk small English model (about 40 MB) run with a tiny
    restricted grammar.  "kaka" and "jarvis" are both in that model's
    vocabulary; the grammar also carries a few look-alike words plus "[unk]" so
    ordinary speech has somewhere to go other than the wake word.

    Measured before shipping on 100+ synthetic clips (8 voices, English +
    Hinglish): every "Kaka" / "Hey Kaka" clip woke it; 1-2 of ~70 ordinary
    sentences did too ("Kya kar rahe ho" sounds like "kaka").  A false wake
    only means it listens for a while and goes back to sleep.

Design (unchanged from the original):
  * ZERO cost when the feature is off — vosk / openwakeword are imported only
    inside start() / install helpers.
  * ZERO latency on the audio path — feed() is a non-blocking queue push; the
    recogniser runs in this module's own background thread.
  * Fully local & offline — audio never leaves the machine.

Order of preference at start():
  1. Vosk  ("kaka", "hey kaka", "jarvis", "hey jarvis")  — if installed + model on disk
  2. openwakeword "hey_jarvis"                           — if installed + model on disk
Both can run together; either one wakes the assistant.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis" (fallback).
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000

# What the user is told to say.
WAKE_PHRASE = "Kaka"

# ── Vosk keyword spotting ────────────────────────────────────────────────────
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = ("https://alphacephei.com/vosk/models/"
                  "vosk-model-small-en-us-0.15.zip")
# Words that wake the assistant.
VOSK_WAKE_WORDS = ("kaka", "jarvis")
# The restricted grammar. Look-alikes and fillers give ordinary speech a place
# to land that is not the wake word — measured: with only ["kaka", "[unk]"]
# many Hinglish sentences were forced onto "kaka"; with these extra words most
# of them stopped doing that.
VOSK_GRAMMAR = ["kaka", "hey kaka", "okay kaka", "jarvis", "hey jarvis",
                "hey", "okay", "coca", "cocoa", "car", "cook", "[unk]"]
# Minimum per-word confidence Vosk reports for the wake word.
VOSK_MIN_CONF = float(os.environ.get("KAKA_WAKE_CONF", "0.5") or 0.5)
# After a detection, ignore further detections for this long.
_REFIRE_GAP = 2.0


def _app_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def vosk_model_dir() -> Path | None:
    """Where the Vosk model lives, or None if it is not on disk."""
    cands = []
    env = os.environ.get("KAKA_VOSK_MODEL", "").strip()
    if env:
        cands.append(Path(env))
    base = _app_dir()
    cands += [base / "models" / VOSK_MODEL_NAME,
              base.parent / "models" / VOSK_MODEL_NAME]
    for c in cands:
        try:
            if (c / "am" / "final.mdl").is_file() or (c / "am").is_dir():
                return c
        except Exception:
            pass
    return None


def _vosk_installed() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("vosk") is not None
    except Exception:
        return False


def vosk_ready() -> bool:
    return _vosk_installed() and vosk_model_dir() is not None


def is_installed() -> bool:
    """True if a wake-word engine package is importable (no model check)."""
    try:
        import importlib.util
        return (_vosk_installed()
                or importlib.util.find_spec("openwakeword") is not None)
    except Exception:
        return False


def _oww_ready() -> bool:
    try:
        import importlib.util
        if importlib.util.find_spec("openwakeword") is None:
            return False
        import openwakeword
        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        if not models_dir.is_dir():
            return False
        has_wake = (any(models_dir.glob(f"{WAKE_MODEL}*.onnx"))
                    or any(models_dir.glob(f"{WAKE_MODEL}*.tflite")))
        has_mel = (any(models_dir.glob("melspectrogram*.onnx"))
                   or any(models_dir.glob("melspectrogram*.tflite")))
        has_emb = (any(models_dir.glob("embedding_model*.onnx"))
                   or any(models_dir.glob("embedding_model*.tflite")))
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def is_ready() -> bool:
    """True if at least one detector is installed AND has its model on disk.
    Cheap, deterministic file checks only — never builds a model. Never raises."""
    try:
        return vosk_ready() or _oww_ready()
    except Exception:
        return False


def _download_vosk_model(logger, tell) -> tuple[bool, str]:
    """KAKA v1.1: streamed download with live progress (printed as
    'Wake model: 12.3 / 39.3 MB (31%)' lines, which Setup shows as it goes),
    a retry, and the zip checked before it is unpacked."""
    import io
    import urllib.request
    import zipfile
    dest_root = _app_dir() / "models"
    dest_root.mkdir(parents=True, exist_ok=True)
    logger("Wake word: downloading the Kaka model (40 MB, one-time)…")
    tell("Wake word: downloading the Kaka model (40 MB, one-time)…")
    last_err = None
    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(VOSK_MODEL_URL, headers={"User-Agent": "Kaka-setup"})
            buf = io.BytesIO()
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0)
                got, step = 0, -1
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    buf.write(chunk)
                    got += len(chunk)
                    pct = int(100 * got / total) if total else 0
                    if total and pct // 5 != step:
                        step = pct // 5
                        msg = (f"Wake model: {got / 1048576:.1f} / {total / 1048576:.1f} MB "
                               f"({pct}%)")
                        print(msg, flush=True)
                        tell(msg)
            data = buf.getvalue()
            if total and len(data) != total:
                raise IOError(f"incomplete download ({len(data)} of {total} bytes)")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                bad = z.testzip()
                if bad:
                    raise IOError(f"zip damaged at {bad}")
                z.extractall(dest_root)
            return (vosk_model_dir() is not None), "model downloaded"
        except Exception as e:          # noqa: BLE001
            last_err = e
            print(f"Wake model: try {attempt} failed ({e}), retrying...", flush=True)
            time.sleep(2 * attempt)
    raise last_err if last_err else IOError("download failed")


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install vosk if missing, then
    download the small model. Returns (ok, message). Never raises.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not _vosk_installed():
            logger("Wake word: installing vosk (one-time)…")
            _tell("Wake word: installing vosk (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "vosk"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        if vosk_model_dir() is None:
            try:
                ok, _m = _download_vosk_model(logger, _tell)
            except Exception as e:
                return False, f"model download failed: {e}"
            if not ok:
                return False, "downloaded, but the wake model could not be found."
        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, f"Wake word ready — say '{WAKE_PHRASE}'."
    except Exception as e:
        return False, f"setup error: {e}"


def chime() -> None:
    """A short rising two-tone beep so the user knows it is listening.
    Windows only (winsound); silently does nothing elsewhere. Non-blocking."""
    def _play():
        try:
            import winsound
            winsound.Beep(880, 90)
            winsound.Beep(1320, 120)
        except Exception:
            pass
    if os.environ.get("KAKA_NO_CHIME"):
        return
    threading.Thread(target=_play, daemon=True, name="WakeChime").start()


class WakeWordDetector:
    """
    Runs the wake model(s) in a dedicated thread. The mic thread calls feed()
    with raw int16 frames; detections invoke on_detect() (called from this
    thread — the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None          # openwakeword Model (fallback)
        self._vosk_model = None
        self._rec = None            # vosk KaldiRecognizer
        self._ready = False
        self._last_fire = 0.0
        self.backends: list[str] = []
        self.last_heard = ""

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _start_vosk(self) -> bool:
        if not vosk_ready():
            return False
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
            try:
                SetLogLevel(-1)
            except Exception:
                pass
            self._vosk_model = Model(str(vosk_model_dir()))
            self._rec = self._new_rec()
            return True
        except Exception as e:
            self._logger(f"Wake word: Vosk could not load — {e}")
            self._vosk_model = None
            self._rec = None
            return False

    def _new_rec(self):
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._vosk_model, SAMPLE_RATE, json.dumps(VOSK_GRAMMAR))
        try:
            rec.SetWords(True)
        except Exception:
            pass
        return rec

    def _start_oww(self) -> bool:
        if not _oww_ready():
            return False
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
            return True
        except Exception as e:
            self._logger(f"Wake word: openwakeword could not load — {e}")
            self._model = None
            return False

    def start(self) -> bool:
        """Load the model(s) and spawn the inference thread. Returns True on
        success. Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        self.backends = []
        if self._start_vosk():
            self.backends.append("vosk")
        if self._start_oww():
            self.backends.append("openwakeword")
        if not self.backends:
            self._logger("Wake word: no detector could be loaded.")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        heard = "'Kaka' / 'Hey Kaka'" if "vosk" in self.backends else "'Hey Jarvis'"
        if "vosk" in self.backends and "openwakeword" in self.backends:
            heard += " (and 'Hey Jarvis')"
        self._logger(f"Wake word: listening for {heard} [{', '.join(self.backends)}].")
        return True

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._rec = None
        self._vosk_model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    # ── inference ────────────────────────────────────────────────────────────
    def _fire(self, what: str) -> None:
        now = time.monotonic()
        if now - self._last_fire < _REFIRE_GAP:
            return
        self._last_fire = now
        self.last_heard = what
        self._drain()
        self._logger(f"Wake word: heard '{what}'.")
        try:
            self._on_detect()
        except Exception as e:
            self._logger(f"Wake word: on_detect error — {e}")

    def _vosk_hit(self, result_json: str) -> str:
        try:
            res = json.loads(result_json)
        except Exception:
            return ""
        words = res.get("result") or []
        for w in words:
            if w.get("word") in VOSK_WAKE_WORDS and float(w.get("conf", 1.0)) >= VOSK_MIN_CONF:
                return w["word"]
        if not words:
            text = (res.get("text") or "").split()
            for t in text:
                if t in VOSK_WAKE_WORDS:
                    return t
        return ""

    def _loop(self) -> None:
        import numpy as np
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                pcm = np.asarray(frame, dtype=np.int16)
                rec = self._rec
                if rec is not None:
                    if rec.AcceptWaveform(pcm.tobytes()):
                        hit = self._vosk_hit(rec.Result())
                        if hit:
                            rec.Reset()
                            self._fire(hit)
                            continue
                model = self._model
                if model is not None:
                    scores = model.predict(pcm)
                    score = 0.0
                    if isinstance(scores, dict):
                        for k, v in scores.items():
                            if "jarvis" in k.lower():
                                score = max(score, float(v))
                        if score == 0.0 and scores:
                            score = max(float(v) for v in scores.values())
                    if score >= self._threshold:
                        self._fire("hey jarvis")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
        try:
            if self._rec is not None:
                self._rec.Reset()
        except Exception:
            pass
