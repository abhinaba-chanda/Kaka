r"""
core/kaka_face.py - Kaka update 21 (feature 30): Kaka knows HIS face.

    "mera chehra yaad karo"  -> camera on (~5 s), 5-8 good frames, only the
                                 face EMBEDDINGS (128 numbers each) are saved in
                                 F:\Kaka\Data\face\face.json - never a photo
                                 (unless he asks: save_photo=true).
    "mera chehra bhool jao"  -> one question, then the face folder is wiped.
    "face greet band/chalu karo".

Greeting "Welcome back Abhinaba": on the boot-intro start (merged into the
intro's own greeting - one greeting, not two) and when he comes back after the
PC was idle/locked >= 10 min (core/kaka_alarm.py watches that): a camera check
of at most 3 s, then the camera is off again. Never while Kaka sleeps, never in
game mode. An unknown face -> nothing is spoken (the thief alarm may act).

Engine: OpenCV's own cv2.FaceDetectorYN (YuNet 2023mar, 227 KB) and
cv2.FaceRecognizerSF (SFace 2021dec int8, 9.9 MB; fp32 38 MB only as a
fallback). The ONNX files live in F:\Kaka\Tools\face-models (downloaded once by
the update, sha256-checked). No dlib, no compiler. cv2 is imported only when
the camera is actually used, so Kaka's startup cost is ~0.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

MODELS = {
    "yunet": {"file": "face_detection_yunet_2023mar.onnx", "size": 232589,
              "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
              "urls": ["https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                       "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
                       "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"]},
    "sface": {"file": "face_recognition_sface_2021dec_int8.onnx", "size": 9896933,
              "sha256": "2b0e941e6f16cc048c20aee0c8e31f569118f65d702914540f7bfdc14048d78a",
              "urls": ["https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec_int8.onnx",
                       "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec_int8.onnx"]},
    "sface_fp32": {"file": "face_recognition_sface_2021dec.onnx", "size": 38696353,
                   "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
                   "urls": ["https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
                            "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx"]},
}

MATCH = 0.40            # cosine (SFace): same person 0.73-0.95, different <= 0.31 in our real-photo test
DET_SCORE = 0.80        # YuNet confidence for a face to count
ENROL_SECONDS = 5.0
ENROL_MAX_SECONDS = 9.0
ENROL_TARGET = 8
ENROL_MIN = 4
ENROL_GAP = 0.45        # s between two kept frames
CHECK_SECONDS = 3.0     # the brief camera check (greet / alarm / Discord photo)
MIN_FACE_PX = 56        # face width in a 640x480 frame
GREET_COOLDOWN = 1800.0
FRAME_W, FRAME_H = 640, 480

# test hooks
_TEST: dict = {}
_now: Callable[[], float] = time.monotonic
_CAM_LOCK = threading.Lock()
_ENGINE: dict = {"eng": None, "err": "", "key": None}
_INTRO: dict = {"done": threading.Event(), "started": False, "result": None, "text": None, "lock": threading.Lock()}
STATE = {"enrolling": False, "last_check": None, "last_greet": 0.0, "camera": "off"}


def _ks():
    from core import kaka_services as ks
    return ks


def _log(text: str) -> None:
    try:
        _ks().log(text)
    except Exception:
        pass


# ------------------------------------------------------------------ paths
def models_dir() -> Path:
    r"""F:\Kaka\Tools\face-models (beside F:\Kaka\Data). KAKA_TOOLS overrides."""
    env = os.environ.get("KAKA_TOOLS", "").strip()
    if env:
        return Path(env) / "face-models"
    try:
        import kaka_paths
        return Path(kaka_paths.DATA_ROOT).parent / "Tools" / "face-models"
    except Exception:
        return Path.home() / "Kaka" / "Tools" / "face-models"


def face_dir() -> Path:
    p = _ks().data_path("face", "x").parent
    return p


def _store_path() -> Path:
    return face_dir() / "face.json"


def _settings_path() -> Path:
    return _ks().data_path("kaka_u21.json")


def settings() -> dict:
    d = {"greet": True, "alarm": False}
    try:
        got = _ks().read_json(_settings_path(), {})
        if isinstance(got, dict):
            d.update(got)
    except Exception:
        pass
    return d


def save_settings(**kw) -> bool:
    d = settings()
    d.update(kw)
    try:
        return bool(_ks().write_json(_settings_path(), d))
    except Exception:
        return False


def greet_on() -> bool:
    return bool(settings().get("greet", True))


# ------------------------------------------------------------------ models
def model_path(key: str) -> Path:
    return models_dir() / MODELS[key]["file"]


def _file_ok(p: Path, key: str) -> bool:
    try:
        return p.is_file() and p.stat().st_size == MODELS[key]["size"]
    except Exception:
        return False


def rec_key() -> Optional[str]:
    """Which recogniser file is on disk (int8 preferred)."""
    for k in ("sface", "sface_fp32"):
        if _file_ok(model_path(k), k):
            return k
    return None


def models_ready() -> tuple:
    if not _file_ok(model_path("yunet"), "yunet"):
        return False, "face detector model nahi hai (update dobara chalao, internet ke saath)"
    if rec_key() is None:
        return False, "face recognition model nahi hai (update dobara chalao, internet ke saath)"
    return True, ""


def cv2_ok() -> tuple:
    if _TEST.get("cv2_ok") is not None:
        return _TEST["cv2_ok"]
    try:
        import importlib.util
        if importlib.util.find_spec("cv2") is None:
            return False, "opencv-python nahi hai"
        import cv2
        if not (hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")):
            return False, f"OpenCV {cv2.__version__} purana hai (4.8+ chahiye)"
        return True, cv2.__version__
    except Exception as e:
        return False, f"OpenCV load nahi hua: {str(e)[:80]}"


class Engine:
    """YuNet detector + SFace recogniser (real OpenCV)."""

    def __init__(self, det_path: str, rec_path: str, rec_name: str = "sface"):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(det_path), "", (320, 320), DET_SCORE, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(str(rec_path), "")
        self.rec_name = rec_name
        self._size = None

    def faces(self, img) -> list:
        h, w = img.shape[:2]
        if self._size != (w, h):
            self.det.setInputSize((w, h))
            self._size = (w, h)
        _, f = self.det.detect(img)
        if f is None:
            return []
        rows = [r for r in f if float(r[-1]) >= DET_SCORE]
        rows.sort(key=lambda r: -float(r[2] * r[3]))
        return rows

    def embed(self, img, row):
        import numpy as np
        al = self.rec.alignCrop(img, row)
        e = self.rec.feature(al).flatten().astype("float32")
        n = float(np.linalg.norm(e)) or 1.0
        return e / n


def engine():
    """-> (Engine | None, error text). Loaded once, on first camera use."""
    if _TEST.get("engine") is not None:
        return _TEST["engine"], ""
    key = rec_key()
    if _ENGINE["eng"] is not None and _ENGINE["key"] == key:
        return _ENGINE["eng"], ""
    ok, why = cv2_ok()
    if not ok:
        return None, why
    ok, why = models_ready()
    if not ok:
        return None, why
    try:
        eng = Engine(model_path("yunet"), model_path(key), key)
    except Exception as e:
        _ENGINE["err"] = f"face model load nahi hua: {str(e)[:100]}"
        return None, _ENGINE["err"]
    _ENGINE.update(eng=eng, err="", key=key)
    return eng, ""


def cosine(a, b) -> float:
    try:
        import numpy as np
        a = np.asarray(a, dtype="float32")
        b = np.asarray(b, dtype="float32")
        na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        if not na or not nb:
            return 0.0
        return float(a @ b) / (na * nb)
    except Exception:
        return 0.0


# ------------------------------------------------------------------ the store (embeddings only)
def load_store() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("embeddings"), list) and d["embeddings"]:
            return d
    except Exception:
        pass
    return {}


def enrolled() -> bool:
    try:
        return _store_path().is_file() and bool(load_store())
    except Exception:
        return False


def save_store(embs: list, model: str, extra: Optional[dict] = None) -> bool:
    d = {"version": 1, "model": model, "created": datetime.now().isoformat(timespec="seconds"),
         "frames": len(embs), "embeddings": [[round(float(x), 6) for x in e] for e in embs],
         "note": "Sirf chehre ke numbers (embeddings). Koi photo nahi."}
    if extra:
        d.update(extra)
    try:
        p = _store_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception as e:
        _log(f"ERR: face data save nahi hua: {e}")
        return False


def forget() -> int:
    """Delete everything in F:\\Kaka\\Data\\face. -> files removed."""
    n = 0
    try:
        d = face_dir()
        for f in list(d.iterdir()):
            try:
                if f.is_file():
                    f.unlink()
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    _INTRO["result"] = None
    return n


def match(emb, store: Optional[dict] = None) -> tuple:
    """-> (best cosine, is_him)."""
    st = store if store is not None else load_store()
    embs = st.get("embeddings") or []
    if not embs:
        return 0.0, False
    best = max(cosine(emb, e) for e in embs)
    thr = float(st.get("threshold") or MATCH)
    return best, best >= thr


def pick_enrolment(embs: list) -> list:
    """Drop outliers (someone else walking by): keep frames close to the mean."""
    if len(embs) < 2:
        return list(embs)
    import numpy as np
    m = np.mean(np.asarray(embs, dtype="float32"), axis=0)
    keep = [e for e in embs if cosine(e, m) >= 0.55]
    return keep


# ------------------------------------------------------------------ camera
def camera_busy() -> str:
    """'' when free, else a Hinglish reason (gesture mode holds the webcam)."""
    if _TEST.get("busy") is not None:
        return _TEST["busy"]
    try:
        import sys
        G = sys.modules.get("core.kaka_gesture")
        if G is not None and getattr(G, "is_on", lambda: False)():
            return "camera abhi gesture mode use kar raha hai ('gesture mode off' bolo)"
    except Exception:
        pass
    if STATE.get("enrolling"):
        return "camera abhi chehra yaad karne mein laga hai"
    return ""


def _open_camera():
    """-> (read() -> BGR frame | None, release())."""
    if _TEST.get("camera") is not None:
        return _TEST["camera"]()
    import cv2
    cap = None
    for backend in (getattr(cv2, "CAP_DSHOW", 0), getattr(cv2, "CAP_MSMF", 0), 0):
        try:
            cap = cv2.VideoCapture(0, backend)
            if cap is not None and cap.isOpened():
                break
            if cap is not None:
                cap.release()
        except Exception:
            cap = None
    if cap is None or not cap.isOpened():
        raise RuntimeError("webcam nahi khula (Zoom/Teams jaisa koi app camera use kar raha hai, "
                           "ya Windows camera privacy setting band hai)")
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
        if frame.shape[1] > FRAME_W:
            frame = cv2.resize(frame, (FRAME_W, int(frame.shape[0] * FRAME_W / frame.shape[1])))
        return frame
    return read, cap.release


def _cam_note(on: bool, why: str) -> None:
    STATE["camera"] = "on" if on else "off"
    _log(f"SYS: Camera {'on' if on else 'off'}" + (f" ({why})" if why else ""))


def _brightness(frame) -> float:
    try:
        return float(frame.mean())
    except Exception:
        return 0.0


def check(seconds: float = CHECK_SECONDS, why: str = "face check", need_store: bool = True) -> dict:
    """Brief camera look (<= seconds). -> {"result": him|unknown|noface|noenrol|
    nomodel|nocam|busy (|face for a plain photo), "score", "frame" (best BGR frame
    or None), "why"}. Stops early the moment HIS face is seen. The camera is
    always released."""
    out = {"result": "nocam", "score": 0.0, "frame": None, "why": "", "faces": 0}
    store = load_store() if need_store else {}
    eng, err = engine()
    busy = camera_busy()
    if busy:
        out.update(result="busy", why=busy)
        return out
    if not _CAM_LOCK.acquire(timeout=seconds):
        out.update(result="busy", why="camera abhi doosre kaam mein hai")
        return out
    t0 = _now()
    release = None
    try:
        try:
            read, release = _open_camera()
        except Exception as e:
            out.update(result="nocam", why=str(e)[:160])
            return out
        _cam_note(True, f"{why}, max {int(round(seconds))} s")
        best_face = None          # (face area, frame, score)
        last_bright = None
        got_frame = False
        while _now() - t0 < seconds:
            frame = read()
            if frame is None:
                if _TEST.get("camera") is None:
                    time.sleep(0.03)
                continue
            got_frame = True
            if last_bright is None or _brightness(frame) > 12:
                last_bright = frame
            if eng is None:
                if _now() - t0 >= min(seconds, 1.2) and _brightness(last_bright) > 12:
                    break          # photo only: auto-exposure had ~1 s
                continue
            try:
                rows = eng.faces(frame)
            except Exception:
                rows = []
            if not rows:
                continue
            out["faces"] = max(out["faces"], len(rows))
            if not store:
                area = float(rows[0][2] * rows[0][3])
                if best_face is None or area > best_face[0]:
                    best_face = (area, frame, 0.0)
                if _now() - t0 >= 1.0:
                    break
                continue
            for r in rows[:3]:
                try:
                    s, him = match(eng.embed(frame, r), store)
                except Exception:
                    continue
                out["score"] = max(out["score"], s)
                if him:
                    out.update(result="him", frame=frame, why="")
                    return out
                area = float(r[2] * r[3])
                if best_face is None or area > best_face[0]:
                    best_face = (area, frame, s)
        if not got_frame:
            out.update(result="nocam", why="camera se tasveer nahi aayi (shayad koi aur app camera use kar raha hai)")
            return out
        out["frame"] = best_face[1] if best_face is not None else last_bright
        if need_store and not store:
            out.update(result="noenrol", why="chehra abhi yaad nahi hai ('mera chehra yaad karo')")
        elif need_store and eng is None:
            out.update(result="nomodel", why=err)
        elif best_face is not None:
            out["result"] = "unknown" if need_store else "face"
        else:
            out["result"] = "noface"
        return out
    finally:
        try:
            if release is not None:
                release()
        except Exception:
            pass
        _CAM_LOCK.release()
        if release is not None:
            _cam_note(False, "")
        out["seconds"] = round(_now() - t0, 2)
        STATE["last_check"] = {k: v for k, v in out.items() if k != "frame"}


def save_jpg(frame, path: Path, max_w: int = 1280) -> bool:
    if _TEST.get("save_jpg") is not None:
        return _TEST["save_jpg"](frame, path)
    try:
        import cv2
        h, w = frame.shape[:2]
        if w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)))
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return False
        path.write_bytes(buf.tobytes())
        return True
    except Exception as e:
        _log(f"ERR: photo save nahi hui: {e}")
        return False


# ------------------------------------------------------------------ enrolment
def _sharp(eng, frame, row) -> float:
    try:
        cv2 = eng.cv2
        x, y, w, h = [int(v) for v in row[:4]]
        crop = frame[max(0, y):y + h, max(0, x):x + w]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(g, cv2.CV_64F).var())
    except Exception:
        return 100.0


def enroll(save_photo: bool = False) -> tuple:
    """-> (ok, Hinglish result line). Blocking ~5-9 s."""
    eng, err = engine()
    if eng is None:
        return False, f"Chehra yaad nahi kar paaya - {err}."
    busy = camera_busy()
    if busy:
        return False, f"Chehra yaad nahi kar paaya - {busy}."
    if not _CAM_LOCK.acquire(timeout=4.0):
        return False, "Camera abhi doosre kaam mein hai, thodi der baad bolo."
    STATE["enrolling"] = True
    release = None
    embs, kept_frame, seen_many, dark = [], None, 0, 0
    t0 = _now()
    try:
        try:
            read, release = _open_camera()
        except Exception as e:
            return False, f"Chehra yaad nahi kar paaya - {e}."
        _cam_note(True, "chehra yaad kar raha hoon")
        last_keep = -9.0
        while True:
            el = _now() - t0
            if len(embs) >= ENROL_TARGET or el >= ENROL_MAX_SECONDS or (el >= ENROL_SECONDS and len(embs) >= 5):
                break
            frame = read()
            if frame is None:
                if _TEST.get("camera") is None:
                    time.sleep(0.03)
                continue
            if _brightness(frame) < 25:
                dark += 1
                continue
            if el - last_keep < ENROL_GAP:
                continue
            try:
                rows = eng.faces(frame)
            except Exception:
                rows = []
            if not rows:
                continue
            if len(rows) > 1:
                seen_many += 1
            r = rows[0]
            if float(r[2]) < MIN_FACE_PX or float(r[-1]) < 0.85:
                continue
            if _sharp(eng, frame, r) < 15:
                continue
            try:
                embs.append(eng.embed(frame, r))
            except Exception:
                continue
            last_keep = el
            if kept_frame is None:
                kept_frame = frame
    finally:
        try:
            if release is not None:
                release()
        except Exception:
            pass
        STATE["enrolling"] = False
        _CAM_LOCK.release()
        if release is not None:
            _cam_note(False, "")
    good = pick_enrolment(embs)
    if len(good) < ENROL_MIN:
        if dark > 5 and len(embs) < ENROL_MIN:
            why = "roshni bahut kam hai, light on karke dobara bolo"
        elif seen_many:
            why = "ek se zyada chehre dikhe ya chehra saaf nahi tha - akele camera ke saamne dobara bolo"
        else:
            why = "chehra saaf nahi dikha - camera ki taraf seedha dekh ke dobara bolo"
        return False, f"Chehra yaad nahi hua ({len(good)} achhe frame mile) - {why}. Camera off."
    extra = {}
    if save_photo and kept_frame is not None:
        p = face_dir() / "abhinaba-face.jpg"
        if save_jpg(kept_frame, p):
            extra["photo"] = str(p)
    if not save_store(good, getattr(eng, "rec_name", "sface"), extra):
        return False, "Chehra ka data save nahi hua (F:\\Kaka\\Data\\face). Camera off."
    _INTRO["result"] = None
    msg = (f"Ho gaya {_kaka_user_name()}, tumhara chehra yaad kar liya ({len(good)} frames). Sirf numbers save kiye, "
           + ("aur ek photo bhi jaisa tumne bola." if extra.get("photo") else "koi photo nahi.")
           + " Camera off.")
    return True, msg


def enroll_async(save_photo: bool = False) -> str:
    """Tool path: speaks 'camera on' now, the result when done."""
    if STATE.get("enrolling"):
        return "Abhi chehra yaad kar raha hoon, camera ki taraf dekhte raho."
    eng, err = engine()
    if eng is None:
        return f"Chehra yaad nahi kar sakta - {err}."
    busy = camera_busy()
    if busy:
        return f"Abhi nahi ho sakta - {busy}."
    try:
        if _ks().in_game_mode():
            return "Game mode on hai, isliye camera nahi kholunga. Game band karke bolo."
    except Exception:
        pass

    def _run():
        ok, msg = enroll(save_photo)
        _log(("SYS: " if ok else "ERR: ") + msg)
        try:
            _ks().popup("Face", msg)
            _ks().say_async(msg)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True, name="kaka-u21-enrol").start()
    try:
        _ks().popup("Face", "Camera on - seedha camera ki taraf dekho (5 second).")
    except Exception:
        pass
    return ("Camera on. Seedha camera ki taraf dekho, 5 second, thoda sa sar idhar-udhar ghumao. "
            "(Say exactly this to him.)")


# ------------------------------------------------------------------ greeting
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


def welcome_text() -> str:
    return "Welcome back " + _kaka_user_name() + "."


def _blocked_reason() -> str:
    try:
        ks = _ks()
        if ks._asleep():
            return "sleep mode"
        if ks.in_game_mode():
            return "game mode"
    except Exception:
        pass
    return ""


def start_intro_check() -> bool:
    """Start the one camera check for this Kaka start (boot intro / logon),
    shared by the intro greeting and the thief alarm. Idempotent."""
    with _INTRO["lock"]:
        if _INTRO["started"]:
            return False
        _INTRO["started"] = True

    def _run():
        try:
            _INTRO["result"] = check(CHECK_SECONDS, "Kaka start: chehra check")
        except Exception as e:
            _INTRO["result"] = {"result": "nocam", "why": str(e)[:100], "frame": None}
        finally:
            _INTRO["done"].set()
    threading.Thread(target=_run, daemon=True, name="kaka-u21-startcheck").start()
    return True


def intro_greeting(g: str, wait: float = 4.5) -> str:
    """core/kaka_bootintro.greeting(): 'Good evening Abhinaba. Systems online.'
    -> 'Welcome back Abhinaba. Good evening. Systems online.' when the camera
    saw HIM. Decided once per start (both intro calls get the same text).
    Unchanged (and no camera) when face greet is off / not enrolled / game."""
    if _INTRO["text"] is not None:
        return _INTRO["text"].get(g, g)
    try:
        if not greet_on() or not enrolled() or _blocked_reason() or not models_ready()[0]:
            return g
    except Exception:
        return g
    start_intro_check()
    _INTRO["done"].wait(wait)
    r = _INTRO["result"] or {}
    if r.get("result") == "him":
        _n = _kaka_user_name()                 # KAKA: user name
        new = welcome_text() + " " + g.replace(" " + _n + ".", ".").replace(" " + _n, "")
        STATE["last_greet"] = time.time()
        _INTRO["text"] = {g: new}
        _log("SYS: Chehra pehchana - intro mein 'Welcome back' joda.")
        return new
    _INTRO["text"] = {}
    return g


def greet_now(result: Optional[dict] = None, reason: str = "wapas aaye") -> str:
    """Speak 'Welcome back Abhinaba.' if the check says it is him. Returns what
    happened (for logs/tests)."""
    if not greet_on():
        return "greet off"
    why = _blocked_reason()
    if why:
        return why
    r = result
    if r is None:
        if not enrolled():
            return "not enrolled"
        r = check(CHECK_SECONDS, f"{reason}: chehra check")
    if r.get("result") != "him":
        return r.get("result") or "no"       # unknown face -> nothing spoken
    if time.time() - float(STATE.get("last_greet") or 0) < GREET_COOLDOWN:
        return "greeted recently"
    STATE["last_greet"] = time.time()
    speak_greet(welcome_text())
    _log("SYS: " + welcome_text())
    return "greeted"


# fix 21.1: the greeting must be HEARD. Kaka's own voice first; if the live
# session is not ready (after unlock it is often still reconnecting), fall back
# to the Windows built-in voice (offline, no install, no admin).
SAY_WAIT = 6.0


def _windows_voice(text: str) -> bool:
    if os.name != "nt":
        return False
    t = str(text).replace("'", "")
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Add-Type -AssemblyName System.Speech; "
                            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                            "$s.Rate = 0; $s.Volume = 100; $s.Speak('" + t + "')"],
                           creationflags=0x08000000, timeout=25,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def speak_greet(text: str) -> None:
    def _run():
        how = "no-session"
        try:
            how = _ks().say(text, False, SAY_WAIT, True)
        except Exception:
            how = "error"
        if how != "spoken":
            ok = _windows_voice(text)
            how = how + (" -> windows voice" if ok else " -> windows voice failed")
        try:
            _ks().timing("face greet voice", 0.0, how)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True, name="kaka-u21-greetvoice").start()


# ------------------------------------------------------------------ Discord photo (34)
def webcam_photo() -> tuple:
    """-> (jpg path | None, error). Camera on <= 3 s; prefers a frame with a face."""
    busy = camera_busy()
    if busy:
        return None, busy[0].upper() + busy[1:]
    ok, why = cv2_ok()
    if not ok:
        return None, f"Camera photo nahi le sakta - {why}"
    r = check(CHECK_SECONDS, "Discord se webcam photo", need_store=False)
    if r.get("result") in ("busy", "nocam"):
        return None, (r.get("why") or "camera nahi khula")
    fr = r.get("frame")
    if fr is None:
        return None, "Camera se tasveer nahi aayi"
    d = _ks().data_path("Cache", "webcam", "x").parent
    _cleanup(d, 86400)
    p = d / f"webcam-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
    if not save_jpg(fr, p):
        return None, "Photo save nahi hui"
    return str(p), ""


def _cleanup(d: Path, max_age: float) -> None:
    try:
        now = time.time()
        for f in d.glob("webcam-*.jpg"):
            if now - f.stat().st_mtime > max_age:
                f.unlink()
    except Exception:
        pass


# ------------------------------------------------------------------ status / selfcheck
def status() -> str:
    parts = []
    parts.append("Face greet " + ("on" if greet_on() else "band"))
    st = load_store()
    parts.append(f"chehra yaad hai ({st.get('frames', len(st.get('embeddings') or []))} frames)" if st
                 else "chehra abhi yaad nahi ('mera chehra yaad karo')")
    ok, why = models_ready()
    if not ok:
        parts.append(why)
    return ", ".join(parts) + "."


def selfcheck() -> tuple:
    """(ready, reason) for the installer. Loads the real models once."""
    ok, why = cv2_ok()
    if not ok:
        return False, why
    ok, why2 = models_ready()
    if not ok:
        return False, why2
    eng, err = engine()
    if eng is None:
        return False, err
    try:
        import numpy as np
        img = np.zeros((240, 320, 3), dtype="uint8")
        eng.faces(img)
    except Exception as e:
        return False, f"face model chala nahi: {str(e)[:80]}"
    tail = "chehra yaad hai" if enrolled() else "bolo 'mera chehra yaad karo'"
    return True, f"OpenCV {why} + YuNet/SFace ({rec_key()}), {tail}"
