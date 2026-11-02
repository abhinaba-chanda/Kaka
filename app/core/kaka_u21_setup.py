r"""
core/kaka_u21_setup.py - Kaka update 21 installer step (run by
Kaka-Update-21.bat from the app folder:  python -u -m core.kaka_u21_setup).

Prints every line the moment it happens (flush), including a % line while a
model downloads, so the window never looks frozen:
    PROG|30|<text>                                  (progress)
    SETUP|<feature>|ok|<text>  or  SETUP|<feature>|skipped|<reason>
Models: the smallest OpenCV-zoo face models (YuNet 227 KB + SFace int8
9.9 MB), GitHub media first, HuggingFace mirror next; a mirror slower than
~40 KB/s is dropped after 20 s and the next one continues the same .part
file; sha256 checked; hard time limits. A failure only skips that feature.
Never touches config/api_keys.json.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from typing import Optional

PIP_LOG = os.path.join(os.path.expanduser("~"), "kaku-setup-logs", "kaka-update21-pip.txt")
SLOW_BPS = 40_000
SLOW_AFTER = 20.0

# test hooks
_TEST: dict = {}


def say(text: str) -> None:
    print(text, flush=True)


def result(feat: str, ok: bool, text: str) -> None:
    say(f"SETUP|{feat}|{'ok' if ok else 'skipped'}|{text}")


def prog(feat: str, text: str) -> None:
    say(f"PROG|{feat}|{text}")


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _open(url: str, start: int, timeout: float):
    if _TEST.get("open") is not None:
        return _TEST["open"](url, start)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Kaka-Update-21",
                                               **({"Range": f"bytes={start}-"} if start else {})})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch(url: str, part: str, size: int, label: str, feat: str, deadline: float,
          clock=time.monotonic, allow_slow: bool = False) -> str:
    """Download url into part (resuming). -> 'done' | 'slow' | 'timeout' | 'error: ...'."""
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if have >= size:
        return "done"
    try:
        r = _open(url, have, 20.0)
    except Exception as e:
        return f"error: {str(e)[:80]}"
    try:
        code = getattr(r, "status", None) or getattr(r, "code", 200)
        if have and code != 206:
            have = 0                              # server ignored Range -> start again
        t0 = clock()
        got = 0
        last_print = -1.0
        last_pct = -1
        with open(part, "ab" if have else "wb") as f:
            while True:
                if clock() > deadline:
                    return "timeout"
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                done = have + got
                el = max(clock() - t0, 1e-6)
                pct = int(done * 100 / size) if size else 0
                if pct // 10 != last_pct // 10 or el - last_print >= 5.0:
                    prog(feat, f"{label}: {min(pct, 100)}% ({done / 1e6:.1f} / {size / 1e6:.1f} MB, "
                               f"{got / el / 1e6:.2f} MB/s)")
                    last_print, last_pct = el, pct
                if not allow_slow and el > SLOW_AFTER and got / el < SLOW_BPS and size - done > 1_000_000:
                    return "slow"
        return "done" if os.path.getsize(part) >= size else "error: download beech mein toota"
    except Exception as e:
        return f"error: {str(e)[:80]}"
    finally:
        try:
            r.close()
        except Exception:
            pass


def get_model(key: str, feat: str, label: str, total_s: float, clock=time.monotonic) -> tuple:
    from core import kaka_face as F
    m = F.MODELS[key]
    dest = F.model_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == m["size"] and sha256(dest) == m["sha256"]:
        prog(feat, f"{label}: pehle se hai")
        return True, "already there"
    part = str(dest) + ".part"
    deadline = clock() + total_s
    why = []
    for rnd in range(2):                     # each mirror, twice (resume)
        for url in m["urls"]:
            if clock() > deadline:
                break
            host = url.split("/")[2]
            prog(feat, f"{label}: {host} se download ({m['size'] / 1e6:.1f} MB)...")
            res = fetch(url, part, m["size"], label, feat, deadline, clock)
            if res == "done":
                if sha256(part) == m["sha256"]:
                    os.replace(part, dest)
                    prog(feat, f"{label}: 100% - checksum OK")
                    return True, f"{m['size'] / 1e6:.1f} MB from {host}"
                why.append(f"{host}: checksum galat")
                prog(feat, f"{label}: {host} wali file kharab nikli (checksum) - agla mirror")
                try:
                    os.remove(part)
                except Exception:
                    pass
                continue
            why.append(f"{host}: {res}")
            prog(feat, f"{label}: {host} {'bahut slow' if res == 'slow' else res} - agla mirror try kar raha hoon")
    return False, "download nahi hua (" + "; ".join(why[-3:]) + ")"


def run(args, timeout: float) -> tuple:
    try:
        os.makedirs(os.path.dirname(PIP_LOG), exist_ok=True)
        with open(PIP_LOG, "a", encoding="utf-8", errors="replace") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}\n")
            log.flush()
            p = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode == 0, f"exit {p.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"{int(timeout)} s se zyada laga"
    except Exception as e:
        return False, str(e)[:100]


def step_face() -> bool:
    from core import kaka_face as F
    ok, why = F.cv2_ok()
    if not ok and "nahi hai" in why:
        prog("30", "OpenCV install ho raha hai (PyPI)...")
        ok2, w2 = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                       "--only-binary=:all:", "opencv-python>=4.8,<5"], 300)
        importlib.invalidate_caches()
        ok, why = F.cv2_ok()
        if not ok:
            result("30", False, f"OpenCV nahi laga ({w2}) - face greet, chor alarm pehchan, webcam photo band")
            return False
    if not ok:
        result("30", False, why)
        return False
    ok, why = get_model("yunet", "30", "Face detector (YuNet)", 180)
    if not ok:
        result("30", False, f"YuNet {why}")
        return False
    ok, why = get_model("sface", "30", "Face recogniser (SFace int8)", 600)
    if ok:
        eng, err = F.engine()
        if eng is None:
            prog("30", f"int8 model is OpenCV pe nahi chala ({err[:60]}) - bada model (38 MB) la raha hoon")
            try:
                os.remove(F.model_path("sface"))
            except Exception:
                pass
            ok, why = get_model("sface_fp32", "30", "Face recogniser (SFace)", 900)
    if not ok:
        result("30", False, f"SFace {why}")
        return False
    good, txt = F.selfcheck()
    result("30", good, txt)
    return good


def _pypi_wheel(pkg: str, tag: str) -> Optional[dict]:
    if _TEST.get("pypi") is not None:
        return _TEST["pypi"](pkg, tag)
    import json
    import urllib.request
    req = urllib.request.Request(f"https://pypi.org/pypi/{pkg}/json", headers={"User-Agent": "Kaka-Update-21"})
    with urllib.request.urlopen(req, timeout=20) as r:
        meta = json.loads(r.read().decode("utf-8"))
    for f in meta.get("urls") or []:
        if f.get("filename", "").endswith(tag + ".whl"):
            return {"url": f["url"], "size": int(f["size"]), "sha256": f["digests"]["sha256"],
                    "name": f["filename"]}
    return None


def step_ffmpeg_fast(clock=time.monotonic) -> None:
    """Only when F:\\Kaka\\Tools\\ffmpeg has no working ffmpeg.exe (fresh PCs via
    Kaka-Upgrade.bat): take it from PyPI (imageio-ffmpeg wheel, ~39 MB, the fast
    route on his net) instead of the slow gyan.dev zip. Live % lines."""
    try:
        from core import kaka_ffmpeg as FF
    except Exception:
        return
    if os.name != "nt" and not _TEST.get("ffmpeg_force"):
        return
    exe = FF.tools_dir() / "bin" / "ffmpeg.exe"
    if exe.is_file() and FF.works(str(exe)):
        prog("32", "Screen recording ffmpeg: pehle se hai")
        return
    try:
        w = _pypi_wheel("imageio-ffmpeg", "win_amd64")
    except Exception as e:
        result("32", False, f"PyPI se ffmpeg info nahi mili ({str(e)[:60]}) - recording ke liye baad mein dobara chalao")
        return
    if not w:
        result("32", False, "PyPI pe Windows ffmpeg nahi mila")
        return
    exe.parent.mkdir(parents=True, exist_ok=True)
    part = str(FF.tools_dir() / "ffmpeg-pypi.whl.part")
    deadline = clock() + 1200
    res = "error"
    while clock() < deadline:
        res = fetch(w["url"], part, w["size"], f"ffmpeg (PyPI, {w['size'] / 1e6:.0f} MB)", "32", deadline, clock, True)
        if res == "done" or res == "timeout":
            break
        prog("32", f"ffmpeg: {res} - wahin se dobara try kar raha hoon")
        time.sleep(0 if _TEST else 3)
    if res != "done":
        result("32", False, f"ffmpeg download poora nahi hua ({res}) - dobara chalao, jahan ruka wahin se aage")
        return
    if sha256(part) != w["sha256"]:
        os.remove(part)
        result("32", False, "ffmpeg download kharab nikla (checksum) - dobara chalao")
        return
    import zipfile
    try:
        with zipfile.ZipFile(part) as z:
            names = [n for n in z.namelist() if "/binaries/ffmpeg" in n and n.endswith(".exe")]
            if not names:
                raise RuntimeError("wheel mein ffmpeg.exe nahi")
            with z.open(names[0]) as src, open(str(exe) + ".tmp", "wb") as dst:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    dst.write(b)
        os.replace(str(exe) + ".tmp", exe)
        os.remove(part)
    except Exception as e:
        result("32", False, f"ffmpeg nikal nahi paaya: {str(e)[:80]}")
        return
    ok = FF.works(str(exe)) if os.name == "nt" else True
    result("32", ok, "ffmpeg PyPI se laga (screen recording ready)" if ok else "ffmpeg.exe chala nahi")


STEPS = [("30", step_face), ("32", step_ffmpeg_fast)]


def main(argv=None) -> int:
    say("U21-SETUP START")
    for feat, fn in STEPS:
        try:
            fn()
        except Exception as e:
            result(feat, False, f"error: {str(e)[:120]}")
    say("U21-SETUP DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
