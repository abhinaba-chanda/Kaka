r"""
core/kaka_recorder.py - Kaka update 20 (feature 32): screen recording by voice.

    start(window=False, mic=False) -> str   "recording shuru karo" / "ye window record karo"
    stop() -> str                           "recording band karo"
    status() -> str                         "recording kitni hui"
    replay_on() / replay_off() -> str       "replay buffer chalu / band karo"
    save_replay(seconds=30) -> str          "pichhle 30 second save karo"

ffmpeg (core/kaka_ffmpeg.py) runs as its own process: gdigrab 30 fps ->
h264_nvenc (libx264 fallback, automatic when NVENC dies in the first 2 s) ->
F:\Kaka\Data\Recordings\kaka-YYYYMMDD-HHMMSS.mp4. Stopped cleanly with 'q' on
its stdin (the mp4 gets its index), killed only after 15 s. ffmpeg itself
carries -t 7200 and -fs (free space - 2 GB), so it ends cleanly at 2 h / low
disk even if Kaka dies. A watcher thread updates the HUD "REC 00:42" badge,
checks the disk every 10 s and speaks when ffmpeg stopped on its own.

Importing this module is cheap (stdlib only); nothing starts until asked.
Never raises out of the public functions.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_IS_WIN = sys.platform == "win32"
MIN_FREE = 2 * 1024 ** 3          # 2 GB
MAX_S = 7200                      # 2 h
FPS = 30
START_CHECK_S = 2.0               # ffmpeg dying inside this = failed start
STOP_WAIT_S = 15.0
DISK_EVERY_S = 10.0
WATCH_S = 1.0
REPLAY_SEG_S = 10
REPLAY_WRAP = 5

_LOCK = threading.RLock()
_S: dict = {"proc": None}                 # recording session
_R: dict = {"proc": None}                 # replay buffer
_B: dict = {"w": None, "rec": "", "replay": ""}
_ATEXIT = {"on": False}

# ------------------------------------------------------------- test hooks
_popen = subprocess.Popen
_clock = time.monotonic


def _disk_free(path) -> int:
    try:
        p = Path(path)
        while not p.exists() and p != p.parent:
            p = p.parent
        return int(shutil.disk_usage(str(p)).free)
    except Exception:
        return 1 << 50      # unknown -> don't block


def _window_region():
    """-> (title, (l,t,r,b)) of the window in front that is not Kaka's HUD, or None."""
    try:
        from core import kaka_screen as S
        return S.pick_window(S._windows_in_zorder(), S._virtual_screen())
    except Exception:
        return None


def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def _F():
    from core import kaka_ffmpeg as F
    return F


def _log(m: str) -> None:
    ks = _ks()
    if ks:
        ks.log(m)


def _timing(label, s, extra=""):
    ks = _ks()
    if ks:
        ks.timing(label, s, extra)


def _say(text: str) -> None:
    ks = _ks()
    if ks:
        try:
            ks.say_async(text)
        except Exception:
            pass


def _data(*parts) -> Path:
    ks = _ks()
    if ks:
        return ks.data_path(*parts)
    p = Path.home().joinpath("Kaka", "Data", *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def rec_dir() -> Path:
    p = _data("Recordings", "x").parent
    p.mkdir(parents=True, exist_ok=True)
    return p


def replay_dir() -> Path:
    p = _data("Cache", "replay", "x").parent
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------- text helpers
def fmt_len(sec: float) -> str:
    s = int(round(max(0.0, float(sec))))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ghanta {m} min" if m else f"{h} ghanta"
    if m:
        return f"{m} min {s} sec" if s else f"{m} min"
    return f"{s} sec"


def clock(sec: float) -> str:
    s = int(max(0, sec))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


NO_MIX_MSG = ("Stereo Mix nahi mila, sirf video record ho raha hai - Sound settings > Recording > "
              "Stereo Mix enable karo toh awaaz bhi aayegi.")


def out_path(prefix: str = "kaka") -> Path:
    d = rec_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = d / f"{prefix}-{stamp}.mp4"
    i = 2
    while p.exists():
        p = d / f"{prefix}-{stamp}-{i}.mp4"
        i += 1
    return p


def _tail(path, n: int = 400) -> str:
    try:
        b = Path(path).read_bytes()[-4000:].decode("utf-8", "replace")
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        return " | ".join(lines[-3:])[-n:]
    except Exception:
        return ""


# ------------------------------------------------------------- process control
def _launch(cmd: list, logpath: Path):
    logf = open(logpath, "wb")
    kw = {}
    if _IS_WIN:
        F = _F()
        kw["creationflags"] = F.NO_WINDOW | F.NEW_GROUP
    else:
        kw["start_new_session"] = True
    try:
        p = _popen(cmd, stdin=subprocess.PIPE, stdout=logf, stderr=logf, **kw)
    except Exception:
        logf.close()
        raise
    return p, logf


def _died_fast(p, wait_s: float = START_CHECK_S) -> bool:
    try:
        p.wait(timeout=wait_s)
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return p.poll() is not None


def graceful_stop(p, wait_s: float = STOP_WAIT_S) -> str:
    """'q' on stdin -> wait -> terminate -> kill. -> 'clean'|'terminated'|'killed'|'gone'."""
    if p is None:
        return "gone"
    if p.poll() is not None:
        return "gone"
    try:
        p.stdin.write(b"q")
        p.stdin.flush()
    except Exception:
        pass
    try:
        p.wait(timeout=wait_s)
        how = "clean"
    except subprocess.TimeoutExpired:
        how = "terminated"
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            how = "killed"
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
    try:
        if p.stdin:
            p.stdin.close()
    except Exception:
        pass
    return how


def _pidfile(name: str) -> Path:
    return _data("Cache", name)


def _write_pid(name: str, p) -> None:
    try:
        _pidfile(name).write_text(str(int(p.pid)), encoding="utf-8")
    except Exception:
        pass


def _clear_pid(name: str) -> None:
    try:
        _pidfile(name).unlink()
    except Exception:
        pass


# ------------------------------------------------------------- HUD badge
def _badge_text() -> str:
    parts = []
    if _B["rec"]:
        parts.append('<span style="color:#ff2d2d">\u25cf</span> REC ' + _B["rec"])
    if _B["replay"]:
        parts.append('<span style="color:#ff9a2d">\u23fa</span> REPLAY')
    return " &nbsp; ".join(parts)


def _badge_update() -> None:
    ks = _ks()
    if ks is None or ks.jarvis() is None:
        return
    text = _badge_text()

    def _do():
        try:
            hud = ks.jarvis().ui._win.hud
        except Exception:
            return
        lab = _B["w"]
        if not text:
            if lab is not None:
                try:
                    lab.hide()
                    lab.deleteLater()
                except Exception:
                    pass
                _B["w"] = None
            return
        if lab is None:
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QLabel
            lab = QLabel(hud)
            lab.setObjectName("kakaRecBadge")
            lab.setTextFormat(Qt.TextFormat.RichText)
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            lab.setStyleSheet("QLabel#kakaRecBadge{color:#ffffff;background:rgba(0,0,0,150);"
                              "border:1px solid rgba(255,45,45,160);border-radius:9px;"
                              "padding:2px 8px;font-family:'Segoe UI';font-size:12px;"
                              "font-weight:600;}")
            lab.move(10, 10)
            _B["w"] = lab
        lab.setText(text)
        lab.adjustSize()
        lab.show()
        lab.raise_()
    try:
        ks.run_in_ui(_do)
    except Exception:
        pass


# ------------------------------------------------------------- recording
def recording() -> bool:
    p = _S.get("proc")
    return p is not None and p.poll() is None


def elapsed() -> float:
    t0 = _S.get("t0")
    return max(0.0, _clock() - t0) if t0 is not None else 0.0


def _describe(enc: str, auds: dict) -> str:
    a = []
    if auds.get("sys"):
        a.append("system audio")
    if auds.get("mic"):
        a.append("mic")
    return ("NVENC" if enc == "h264_nvenc" else "x264") + ", " + (" + ".join(a) if a else "sirf video")


def start(window: bool = False, mic: bool = False) -> str:
    t0 = time.monotonic()
    try:
        F = _F()
    except Exception as e:
        return f"Recording ka module load nahi hua: {e}"
    with _LOCK:
        if recording():
            return f"Recording pehle se chal rahi hai - {fmt_len(elapsed())} ho gaye. 'recording band karo' bolo rokne ke liye."
        exe = F.ffmpeg_path()
        if not exe:
            return ("Recording nahi kar sakta - ffmpeg install nahi hai (F:\\Kaka\\Tools\\ffmpeg). "
                    "Update-20 installer dobara chalao, internet on rakhna.")
        d = rec_dir()
        free = _disk_free(d)
        if free < MIN_FREE:
            return (f"Recording shuru nahi ki - {d.anchor or 'F:'} drive pe sirf {gb(free)} khali hai, "
                    "2 GB se kam. Thodi jagah khali karo.")
        notes = []
        region, title = None, ""
        if window:
            hit = _window_region()
            if hit:
                title, region = hit[0], hit[1]
            else:
                notes.append("Saamne koi window nahi mili, poori screen record kar raha hoon.")
        try:
            devs = F.list_devices()
        except Exception:
            devs = []
        mix, micdev = F.pick_audio(devs)
        if not mix:
            notes.append(NO_MIX_MSG)
        if mic and not micdev:
            notes.append("Mic nahi mila, mic ke bina record kar raha hoon.")
        want_mic = micdev if mic else None
        try:
            nv = F.probe_nvenc(exe=exe)
        except Exception:
            nv = False
        out = out_path()
        max_bytes = max(free - MIN_FREE - 64 * 1024 ** 2, 64 * 1024 ** 2)
        logpath = _data("Cache", "ffmpeg-record.log")
        enc0 = "h264_nvenc" if nv else "libx264"
        try:
            grabs = F.grab_order()
        except Exception:
            grabs = ["ddagrab", "gdigrab"]
        encs = [enc0] + (["libx264"] if enc0 == "h264_nvenc" else [])
        audsets = [(mix, want_mic)] + ([(None, None)] if (mix or want_mic) else [])
        ladder = [(e, a, m, g) for (a, m) in audsets for e in encs for g in grabs]
        proc = logf = None
        used = None
        failed = []
        for enc, a_sys, a_mic, grab in ladder:
            cmd = F.build_record_cmd(str(out), encoder=enc, region=region, sys_audio=a_sys,
                                     mic=a_mic, fps=FPS, max_s=MAX_S, max_bytes=max_bytes, exe=exe,
                                     grabber=grab)
            try:
                proc, logf = _launch(cmd, logpath)
            except Exception as e:
                failed.append((enc, a_sys, a_mic, f"{type(e).__name__}: {e}", grab))
                proc = None
                continue
            if _died_fast(proc):
                why = _tail(logpath)
                try:
                    logf.close()
                except Exception:
                    pass
                failed.append((enc, a_sys, a_mic, why, grab))
                proc = None
                try:
                    if out.exists() and out.stat().st_size < 4096:
                        out.unlink()
                except Exception:
                    pass
                continue
            used = (enc, a_sys, a_mic, grab)
            break
        if proc is None:
            why = failed[-1][3] if failed else "?"
            _log(f"SYS: recording start failed: {why}")
            return f"Recording shuru nahi ho payi - ffmpeg ruk gaya ({why[:160]})."
        enc, a_sys, a_mic, grab = used
        if grab == "gdigrab" and any(f[4] == "ddagrab" and f[0] == enc and f[1] == a_sys and f[2] == a_mic
                                     for f in failed):
            try:
                F.mark_grabber("ddagrab", False)
            except Exception:
                pass
        if enc == "libx264" and any(f[0] == "h264_nvenc" and f[1] == a_sys and f[2] == a_mic
                                    for f in failed):
            try:
                F.mark_nvenc_failed(next(f[3] for f in failed if f[0] == "h264_nvenc"))
            except Exception:
                pass
            notes.append("NVIDIA encoder nahi chala, CPU encoder (x264) se record kar raha hoon - game thoda slow ho sakta hai.")
        if (mix and not a_sys) or (want_mic and not a_mic):
            notes = [n for n in notes if n != NO_MIX_MSG]
            notes.append("Audio device ne kaam nahi kiya, sirf video record ho raha hai.")
        auds = {"sys": bool(a_sys), "mic": bool(a_mic)}
        _S.clear()
        _S.update({"proc": proc, "logf": logf, "log": logpath, "out": out, "t0": _clock(),
                   "enc": enc, "auds": auds, "grab": grab, "title": title, "stopping": False,
                   "mode": "window" if region else "screen"})
        _write_pid("recording.pid", proc)
        if not _ATEXIT["on"]:
            atexit.register(_on_exit)
            _ATEXIT["on"] = True
        th = threading.Thread(target=_watch, args=(proc,), name="kaka-u20-rec-watch", daemon=True)
        _S["watch"] = th
        th.start()
    _B["rec"] = clock(0)
    _badge_update()
    what = f"window '{title[:40]}'" if region else "poori screen"
    _timing("recording start", time.monotonic() - t0, f"{grab}, {_describe(enc, auds)}")
    _log(f"SYS: Recording -> {out}")
    msg = f"Recording shuru: {what}, 30 fps, {_describe(enc, auds)}."
    if notes:
        msg += " " + " ".join(notes)
    return msg + " Rokne ke liye 'recording band karo' bolo."


def _finish(proc, how: str, reason: str = "") -> str:
    """After ffmpeg ended: tidy up and build the spoken result."""
    out = _S.get("out")
    el = elapsed()
    try:
        if _S.get("logf"):
            _S["logf"].close()
    except Exception:
        pass
    logpath = _S.get("log")
    _S.clear()
    _S["proc"] = None
    _clear_pid("recording.pid")
    _B["rec"] = ""
    _badge_update()
    pre = (reason + " ") if reason else ""
    try:
        size = out.stat().st_size if out and out.exists() else 0
    except Exception:
        size = 0
    if size < 4096:
        return pre + f"Recording save nahi ho payi - file khaali hai ({_tail(logpath)[:160]})."
    dur = 0.0
    try:
        dur = _F().probe_duration(str(out))
    except Exception:
        dur = 0.0
    if dur <= 0:
        dur = el
    extra = "" if how == "clean" or how == "gone" else " (ffmpeg ko zabardasti band karna pada, file shayad na chale)"
    return pre + f"Recording save ho gayi: {out}, {fmt_len(dur)}.{extra}"


def stop(reason: str = "") -> str:
    t0 = time.monotonic()
    with _LOCK:
        proc = _S.get("proc")
        if proc is None:
            return "Abhi koi recording nahi chal rahi."
        _S["stopping"] = True
        how = graceful_stop(proc)
        msg = _finish(proc, how, reason)
    _timing("recording stop", time.monotonic() - t0, how)
    _log("SYS: " + msg)
    return msg


def _watch(proc) -> None:
    last_disk = _clock()
    while True:
        time.sleep(WATCH_S)
        with _LOCK:
            if _S.get("proc") is not proc or _S.get("stopping"):
                return
            ended = proc.poll() is not None
        if ended:
            with _LOCK:
                if _S.get("proc") is not proc:
                    return
                el = elapsed()
                if el >= MAX_S - 5:
                    reason = "2 ghante ho gaye, recording apne aap band ho gayi."
                elif _disk_free(rec_dir()) < MIN_FREE + 256 * 1024 ** 2:
                    reason = "Disk bhar rahi thi, recording band kar di."
                else:
                    reason = "Recording beech mein ruk gayi (ffmpeg band ho gaya)."
                msg = _finish(proc, "gone", reason)
            _log("SYS: " + msg)
            _say(msg)
            return
        el = elapsed()
        _B["rec"] = clock(el)
        _badge_update()
        if el >= MAX_S:
            _say(stop("2 ghante ho gaye, recording band kar di."))
            return
        if _clock() - last_disk >= DISK_EVERY_S:
            last_disk = _clock()
            free = _disk_free(rec_dir())
            if free < MIN_FREE:
                _say(stop(f"Disk pe sirf {gb(free)} bacha tha, recording band kar di."))
                return


def status() -> str:
    with _LOCK:
        parts = []
        if recording():
            what = f"window '{_S.get('title', '')[:40]}'" if _S.get("mode") == "window" else "poori screen"
            parts.append(f"Recording chal rahi hai: {fmt_len(elapsed())} ({what}, "
                         f"{_describe(_S.get('enc', ''), _S.get('auds', {}))}).")
        else:
            parts.append("Abhi koi recording nahi chal rahi.")
        if replay_running():
            parts.append("Replay buffer chalu hai - 'pichhle 30 second save karo' bol sakte ho.")
        return " ".join(parts)


# ------------------------------------------------------------- replay buffer
def replay_running() -> bool:
    p = _R.get("proc")
    return p is not None and p.poll() is None


def _clear_segments(d: Path) -> None:
    for f in list(d.glob("seg*.ts")) + list(d.glob("snap-*")) + list(d.glob("joined.ts")) + list(d.glob("list.txt")) + list(d.glob("segs.txt")):
        try:
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()
        except Exception:
            pass


def replay_on() -> str:
    t0 = time.monotonic()
    try:
        F = _F()
    except Exception as e:
        return f"Replay buffer load nahi hua: {e}"
    with _LOCK:
        if replay_running():
            return "Replay buffer pehle se chalu hai. 'pichhle 30 second save karo' bolo."
        exe = F.ffmpeg_path()
        if not exe:
            return "Replay buffer nahi chala sakta - ffmpeg install nahi hai. Update-20 installer dobara chalao."
        try:
            nv = F.probe_nvenc(exe=exe)
        except Exception:
            nv = False
        if not nv:
            return ("Replay buffer sirf NVIDIA encoder (NVENC) pe chalta hai, warna game slow ho jayega - "
                    "yahan NVENC nahi chal raha. Normal recording chalegi: 'recording shuru karo'.")
        d = replay_dir()
        if _disk_free(d) < MIN_FREE:
            return "Replay buffer nahi chalaya - disk pe 2 GB se kam jagah hai."
        _clear_segments(d)
        try:
            devs = F.list_devices()
        except Exception:
            devs = []
        mix, _mic = F.pick_audio(devs)
        logpath = _data("Cache", "ffmpeg-replay.log")
        proc = None
        used_mix = None
        try:
            grabs = F.grab_order()
        except Exception:
            grabs = ["ddagrab", "gdigrab"]
        for a in ([mix, None] if mix else [None]):
            for grab in grabs:
                cmd = F.build_replay_cmd(str(d), encoder="h264_nvenc", sys_audio=a, fps=FPS,
                                         seg_s=REPLAY_SEG_S, wrap=REPLAY_WRAP, exe=exe, grabber=grab)
                try:
                    p, lf = _launch(cmd, logpath)
                except Exception as e:
                    return f"Replay buffer shuru nahi hua: {e}"
                if _died_fast(p):
                    try:
                        lf.close()
                    except Exception:
                        pass
                    continue
                proc, used_mix = p, a
                break
            if proc is not None:
                break
        if proc is None:
            return f"Replay buffer shuru nahi hua - ffmpeg ruk gaya ({_tail(logpath)[:160]})."
        _R.clear()
        _R.update({"proc": proc, "logf": lf, "dir": d, "t0": _clock(), "audio": bool(used_mix)})
        _write_pid("replay.pid", proc)
        if not _ATEXIT["on"]:
            atexit.register(_on_exit)
            _ATEXIT["on"] = True
    _B["replay"] = "on"
    _badge_update()
    _timing("replay on", time.monotonic() - t0)
    msg = (f"Replay buffer chalu - pichhle {REPLAY_SEG_S * (REPLAY_WRAP - 1)} second tak hamesha yaad rahenge. "
           "'pichhle 30 second save karo' bolo.")
    if not used_mix:
        msg += " (Sirf video - Stereo Mix nahi mila.)"
    return msg


def replay_off() -> str:
    with _LOCK:
        p = _R.get("proc")
        if p is None:
            return "Replay buffer pehle se band hai."
        graceful_stop(p, wait_s=5)
        try:
            _R.get("logf") and _R["logf"].close()
        except Exception:
            pass
        d = _R.get("dir")
        _R.clear()
        _R["proc"] = None
        _clear_pid("replay.pid")
        if d:
            _clear_segments(Path(d))
    _B["replay"] = ""
    _badge_update()
    return "Replay buffer band kar diya."


PARTIAL_MIN = 32 * 1024     # the segment being written is used once it has real data


def pick_segments(d: Path, seconds: int, seg_s: int = 0, wrap: int = 0) -> list:
    """Newest segments covering `seconds`, oldest first: the finished ones in
    ffmpeg's own order (segs.txt) + the one being written now."""
    d = Path(d)
    seg_s = seg_s or REPLAY_SEG_S
    wrap = wrap or REPLAY_WRAP
    need = -(-int(seconds) // seg_s) + 1
    done = []
    try:
        for line in (d / "segs.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            name = Path(line.strip()).name
            if name and (d / name).is_file():
                done.append(d / name)
    except Exception:
        done = []
    if done:
        try:
            idx = int(done[-1].stem[3:])
            cur = d / f"seg{(idx + 1) % wrap:03d}.ts"
            if cur not in done and cur.is_file() and cur.stat().st_size >= PARTIAL_MIN:
                done.append(cur)
        except Exception:
            pass
        return done[-need:]
    segs = sorted((p for p in d.glob("seg*.ts") if p.stat().st_size >= PARTIAL_MIN),
                  key=lambda p: (p.stat().st_mtime, p.name))
    return segs[-need:]


def save_replay(seconds: int = 30) -> str:
    t0 = time.monotonic()
    try:
        seconds = int(seconds or 30)
    except Exception:
        seconds = 30
    seconds = max(5, min(seconds, REPLAY_SEG_S * (REPLAY_WRAP - 1)))
    if not replay_running():
        return ("Replay buffer band hai, isliye pichhla hissa save nahi ho sakta. Pehle 'replay buffer chalu karo' "
                "bolo - uske baad kabhi bhi 'pichhle 30 second save karo' bologe toh save ho jayega.")
    F = _F()
    d = Path(_R["dir"])
    have = _clock() - _R.get("t0", _clock())
    segs = pick_segments(d, seconds)
    if not segs:
        return "Replay buffer mein abhi kuch nahi hai, 10-15 second ruk ke bolo."
    snap = d / f"snap-{int(time.time())}"
    snap.mkdir(parents=True, exist_ok=True)
    try:
        copies = []
        for i, s in enumerate(segs):
            c = snap / f"{i:02d}.ts"
            shutil.copyfile(s, c)
            copies.append(c)
        lst = snap / "list.txt"
        lst.write_text(F.concat_list_text(copies), encoding="utf-8")
        out = out_path("kaka-replay")
        exe = F.ffmpeg_path() or "ffmpeg"
        cmds = F.build_replay_save_cmds(str(lst), str(snap / "joined.ts"), str(out), seconds, exe=exe)
        for c in cmds:
            rc, txt = F._run(c, timeout=90)
            if rc != 0:
                return f"Replay save nahi ho paya - ffmpeg: {txt.strip()[-160:]}"
        dur = F.probe_duration(str(out)) or min(seconds, have)
    finally:
        shutil.rmtree(snap, ignore_errors=True)
    _timing("replay save", time.monotonic() - t0, f"{dur:.1f} s")
    msg = f"Clip save ho gaya: {out}, {fmt_len(dur)}."
    if have < seconds:
        msg += f" (Buffer abhi sirf {fmt_len(have)} ka tha.)"
    return msg


# ------------------------------------------------------------- exit / startup
def _on_exit() -> None:
    try:
        if _S.get("proc") is not None:
            graceful_stop(_S["proc"], wait_s=8)
        if _R.get("proc") is not None:
            graceful_stop(_R["proc"], wait_s=3)
    except Exception:
        pass


def _pid_is_ffmpeg(pid: int) -> bool:
    if not _IS_WIN:
        return False
    try:
        rc, out = _F()._run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], timeout=5)
        return rc == 0 and "ffmpeg" in out.lower()
    except Exception:
        return False


def startup_cleanup() -> None:
    """Background (ks.on_start): an ffmpeg replay buffer left over from a Kaka
    crash is killed (segments are throwaway); a left-over recording is left to
    finish by itself (-t / -fs) so its mp4 stays valid."""
    try:
        pf = _pidfile("replay.pid")
        if pf.exists():
            pid = int(pf.read_text().strip() or 0)
            if pid and _pid_is_ffmpeg(pid):
                _F()._run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=8)
                _log("SYS: purana replay buffer (ffmpeg) band kiya.")
            _clear_pid("replay.pid")
            _clear_segments(replay_dir())
        pf = _pidfile("recording.pid")
        if pf.exists():
            pid = int(pf.read_text().strip() or 0)
            if pid and _pid_is_ffmpeg(pid):
                _log("SYS: pichhli recording abhi bhi chal rahi hai - 2 ghante ki limit pe khud save ho jayegi.")
            else:
                _clear_pid("recording.pid")
    except Exception:
        pass


def selfcheck() -> tuple:
    try:
        from core import kaka_ffmpeg as F
        return F.selfcheck()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


try:
    from core import kaka_services as _ksm
    _ksm.on_start(startup_cleanup)
except Exception:
    pass
