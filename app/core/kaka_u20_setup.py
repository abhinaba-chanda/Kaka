r"""
core/kaka_u20_setup.py - Kaka update 20 installer downloads (run by
Kaka-Update-20.bat from the app folder:  python -m core.kaka_u20_setup).

Each step has its own hard timeout and prints exactly one line:
    SETUP|<feature>|ok|<text>    or    SETUP|<feature>|skipped|<reason>
A failed step never fails the update - that feature just says "skipped" and
Kaka tells him honestly if he asks for it. pip output goes to
%USERPROFILE%\kaku-setup-logs\kaka-update20-pip.txt. Never touches
config/api_keys.json.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
PIP_LOG = os.path.join(os.path.expanduser("~"), "kaku-setup-logs", "kaka-update20-pip.txt")

# test hooks
_run = None


def say(text: str) -> None:
    print(text, flush=True)


def result(feat: str, ok: bool, text: str) -> None:
    print(f"SETUP|{feat}|{'ok' if ok else 'skipped'}|{text}", flush=True)


def has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def run(args, timeout: float) -> tuple:
    """-> (rc, why). rc None = timed out (child killed)."""
    if _run is not None:
        return _run(args, timeout)
    try:
        os.makedirs(os.path.dirname(PIP_LOG), exist_ok=True)
        with open(PIP_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args[2:])}\n")
            f.flush()
            p = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **_NOWIN)
            try:
                return p.wait(timeout=timeout), ""
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(10)
                except Exception:
                    pass
                return None, f"took more than {int(timeout // 60)} min"
    except Exception as e:
        return -1, str(e)


def pip(pkgs, timeout: float, no_deps: bool = False) -> tuple:
    args = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--prefer-binary", "--only-binary=:all:"]
    if no_deps:
        args.append("--no-deps")
    rc, why = run(args + list(pkgs), timeout)
    if rc == 0:
        return True, ""
    return False, (why or "pip failed (internet? see kaka-update20-pip.txt)")


def importable(code: str, timeout: float = 90) -> bool:
    rc, _ = run([sys.executable, "-c", code], timeout)
    return rc == 0


# ------------------------------------------------------------------ 28 gestures
GESTURE_PKGS = ["absl-py~=2.3", "flatbuffers~=25.9", "matplotlib", "certifi",
                "pillow>=10,<13", "numpy>=1.24,<3"]      # keep Kaka's own pins (requirements.txt)


def step_gesture() -> None:
    from core import kaka_gesture as G
    t0 = time.monotonic()
    if not (has("mediapipe") and has("cv2") and has("matplotlib") and has("absl") and has("flatbuffers") and has("certifi")):
        say("Haath ke ishare: MediaPipe install kar raha hoon (~40 MB, 1-3 min)...")
        # mediapipe's own dependency list pulls opencv-contrib (a 2nd cv2 next to
        # Kaka's opencv-python) - so: mediapipe without deps, then its real deps.
        ok, why = pip(["mediapipe==1.2.0"], 300, no_deps=True)
        if ok:
            extra = list(GESTURE_PKGS)
            if not has("cv2"):
                extra.append("opencv-python>=4.8,<5")
            ok, why = pip(extra, 300)
        if not ok:
            result("28", False, f"MediaPipe install nahi hua ({why}) - gesture mode band rahega")
            return
    if not importable("import os; os.environ['MPLBACKEND']='Agg'; import mediapipe, cv2; "
                      "from mediapipe.tasks.python import vision; vision.HandLandmarker"):
        result("28", False, "MediaPipe install hua par load nahi hua - gesture mode band rahega")
        return
    ok, msg = G.download_model(timeout=150)
    if not ok:
        result("28", False, msg)
        return
    result("28", True, f"MediaPipe 1.2.0 + hand model ready ({time.monotonic() - t0:.0f} s)")


# ------------------------------------------------------------------ 29 discord
def step_discord() -> None:
    try:
        from core import kaka_discord as D
    except Exception as e:
        result("29", False, f"discord module load nahi hua: {e}")
        return
    if not D.lib_installed():
        say("Discord remote: discord.py install kar raha hoon (~2.3 MB)...")
    try:
        ok, msg = D.install(240.0)
    except Exception as e:
        ok, msg = False, str(e)
    result("29", bool(ok), msg)


# ------------------------------------------------------------------ 32 recording
def step_ffmpeg() -> None:
    try:
        from core import kaka_ffmpeg as F
    except Exception as e:
        result("32", False, f"ffmpeg module load nahi hua: {e}")
        return
    exe = F.tools_dir() / "bin" / "ffmpeg.exe"
    if not exe.is_file():
        say("Screen recording: ffmpeg download kar raha hoon (~110 MB, ek hi baar, 1-5 min)...")
    try:
        ok, msg = F.install()
    except Exception as e:
        ok, msg = False, str(e)
    if ok:
        try:
            nv = F.probe_nvenc(force=True)
            msg += ", NVIDIA encoder (NVENC) ok" if nv else ", NVENC nahi chala - x264 se record hoga"
        except Exception:
            pass
    result("32", bool(ok), msg)


STEPS = [("28", step_gesture), ("29", step_discord), ("32", step_ffmpeg)]


def main(argv=None) -> int:
    t0 = time.time()
    only = set((argv or sys.argv[1:]) or [])
    dry = os.environ.get("KAKA_U20_SETUP_DRY") == "1"     # installer simulation on Linux
    for feat, fn in STEPS:
        if only and feat not in only:
            continue
        if dry:
            say(f"(dry run) step {feat}")
            result(feat, False, "dry run - nothing downloaded")
            continue
        try:
            fn()
        except Exception as e:
            result(feat, False, f"setup error: {e}")
    print(f"U20-SETUP DONE in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
