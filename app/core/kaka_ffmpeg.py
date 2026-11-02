r"""
core/kaka_ffmpeg.py - Kaka update 20 (feature 32): ffmpeg for screen recording.

    python -m core.kaka_ffmpeg --install     # installer step: one status line
    python -m core.kaka_ffmpeg --check       # selfcheck line

* install(): downloads a static Windows ffmpeg build into F:\Kaka\Tools\ffmpeg
  (keeps only bin\ffmpeg.exe, bin\ffprobe.exe, LICENSE). Primary gyan.dev
  "release-essentials" (sha256 from gyan.dev), fallback BtbN GitHub
  "master-latest-win64-gpl" (sha256 from its checksums.sha256). Hard timeouts,
  resume-safe .part file, size sanity, idempotent (skips when a working
  ffmpeg.exe is already there). stdlib only (urllib + zipfile).
* probe_nvenc(): one tiny h264_nvenc test encode (10 s timeout), cached in
  F:\Kaka\Data\Cache\ffmpeg_probe.json.
* list_devices() / parse_dshow(): dshow audio devices (Stereo Mix + mic).
* build_record_cmd() / build_replay_cmd() / build_replay_save_cmds(): the
  exact ffmpeg command lines (pure functions, unit tested).

Nothing here runs at Kaka startup. Never raises out of public helpers.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

_IS_WIN = sys.platform == "win32"
_APP = Path(__file__).resolve().parent.parent

GYAN_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
GYAN_SHA = GYAN_URL + ".sha256"
BTBN_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
BTBN_SHA = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256"
SOURCES = [("gyan.dev", GYAN_URL, GYAN_SHA), ("BtbN GitHub", BTBN_URL, BTBN_SHA)]
MIN_ZIP = 30 * 1024 * 1024          # size sanity for a static win64 build
MAX_ZIP = 600 * 1024 * 1024
DL_TIMEOUT = 30                     # seconds per socket read / connect
DL_DEADLINE = 20 * 60               # whole download, per source
KEEP = {"ffmpeg.exe": "bin/ffmpeg.exe", "ffprobe.exe": "bin/ffprobe.exe",
        "LICENSE": "LICENSE", "LICENSE.txt": "LICENSE"}

MIX_WORDS = ("stereo mix", "stereomix", "stereo-mix", "what u hear", "what you hear",
             "wave out mix", "waveout mix", "mixage st", "mezcla est", "missaggio stereo",
             "mixagem est", "mix st\u00e9r", "stereo mixer", "\u7acb\u4f53\u58f0\u6df7\u97f3",
             "\u30b9\u30c6\u30ec\u30aa \u30df\u30c3\u30af\u30b9")

NO_WINDOW = 0x08000000 if _IS_WIN else 0          # CREATE_NO_WINDOW
NEW_GROUP = 0x00000200 if _IS_WIN else 0          # CREATE_NEW_PROCESS_GROUP


def _print(m):
    try:
        print(m, flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ paths
def tools_dir() -> Path:
    r"""F:\Kaka\Tools\ffmpeg (beside F:\Kaka\Data). KAKA_TOOLS overrides."""
    env = os.environ.get("KAKA_TOOLS", "").strip()
    if env:
        return Path(env) / "ffmpeg"
    try:
        import kaka_paths
        return Path(kaka_paths.DATA_ROOT).parent / "Tools" / "ffmpeg"
    except Exception:
        return Path.home() / "Kaka" / "Tools" / "ffmpeg"


def _exe_name(n: str) -> str:
    return n + ".exe" if _IS_WIN else n


def ffmpeg_path() -> str:
    """Our own copy first, then PATH. '' when none."""
    p = tools_dir() / "bin" / _exe_name("ffmpeg")
    if p.is_file():
        return str(p)
    return shutil.which("ffmpeg") or ""


def ffprobe_path() -> str:
    p = tools_dir() / "bin" / _exe_name("ffprobe")
    if p.is_file():
        return str(p)
    return shutil.which("ffprobe") or ""


def _cache_file(name: str) -> Path:
    try:
        from core import kaka_services as ks
        return ks.data_path("Cache", name)
    except Exception:
        p = Path.home() / "Kaka" / "Data" / "Cache" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(p, data):
    try:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ running (mockable)
def _run(args, timeout: float = 10.0) -> tuple:
    """-> (returncode, text output). (-1, why) on timeout/error. Never raises."""
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout:.0f} s"
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def works(exe: str = "") -> bool:
    exe = exe or ffmpeg_path()
    if not exe:
        return False
    rc, out = _run([exe, "-hide_banner", "-version"], timeout=10)
    return rc == 0 and "ffmpeg version" in out


# ------------------------------------------------------------------ download (mockable)
def _urlopen(url: str, headers: dict | None = None, timeout: float = DL_TIMEOUT):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Kaka-Update20/1.0",
                                               **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_text(url: str, limit: int = 1 << 20) -> str:
    with _urlopen(url) as r:
        return r.read(limit).decode("utf-8", "replace")


def expected_sha(sha_text: str, zip_name: str) -> str:
    """gyan: bare hex. BtbN: '<hex>  <file>' lines -> the line for zip_name."""
    lines = [l.strip() for l in (sha_text or "").splitlines() if l.strip()]
    for l in lines:
        parts = l.replace("*", " ").split()
        if len(parts) >= 2 and parts[-1].strip() == zip_name and len(parts[0]) == 64:
            return parts[0].lower()
    if len(lines) == 1:
        h = lines[0].split()[0]
        if len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h):
            return h.lower()
    return ""


def download(url: str, dest: Path, deadline_s: float = DL_DEADLINE) -> int:
    """Resume-safe download into dest (a .part file). Returns total bytes.
    Raises on error/timeout/size problems."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    if have > MAX_ZIP:
        dest.unlink()
        have = 0
    hdr = {"Range": f"bytes={have}-"} if have else {}
    t0 = time.monotonic()
    with _urlopen(url, hdr) as r:
        code = getattr(r, "status", None) or r.getcode()
        if have and code != 206:          # server ignored the range -> start over
            have = 0
        total = None
        cl = r.headers.get("Content-Length") if getattr(r, "headers", None) else None
        if cl and str(cl).isdigit():
            total = int(cl) + have
            if total > MAX_ZIP or total < MIN_ZIP:
                raise RuntimeError(f"size {total} bytes looks wrong")
        with open(dest, "ab" if have else "wb") as f:
            n = have
            while True:
                if time.monotonic() - t0 > deadline_s:
                    raise TimeoutError(f"download took > {deadline_s // 60:.0f} min")
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                n += len(b)
                if n > MAX_ZIP:
                    raise RuntimeError("download too big")
    size = dest.stat().st_size
    if total is not None and size != total:
        raise RuntimeError(f"incomplete download {size}/{total}")
    if size < MIN_ZIP:
        raise RuntimeError(f"download too small ({size} bytes)")
    return size


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def needed_members(names: list) -> dict:
    """zip member -> relative target, only ffmpeg.exe/ffprobe.exe (in a bin/
    folder) and the top LICENSE."""
    out = {}
    for n in names:
        if n.endswith("/"):
            continue
        parts = n.split("/")
        base = parts[-1]
        if base in ("ffmpeg.exe", "ffprobe.exe") and len(parts) >= 2 and parts[-2] == "bin":
            out[n] = KEEP[base]
        elif base in ("LICENSE", "LICENSE.txt") and len(parts) <= 2 and "LICENSE" not in out.values():
            out[n] = "LICENSE"
    return out


def extract(zip_path: Path, target: Path) -> list:
    """Extract only the needed members into target (via target.new, then swap).
    Returns the relative paths written. Raises if ffmpeg.exe is missing."""
    with zipfile.ZipFile(zip_path) as z:
        want = needed_members(z.namelist())
        if "bin/ffmpeg.exe" not in want.values():
            raise RuntimeError("zip mein ffmpeg.exe nahi mila")
        tmp = target.parent / (target.name + ".new")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        written = []
        for member, rel in want.items():
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            written.append(rel)
    old = target.parent / (target.name + ".old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    return sorted(written)


def install(verify_exe: bool = True) -> tuple:
    """-> (ok, one status line). Idempotent; never raises."""
    t0 = time.monotonic()
    target = tools_dir()
    try:
        mine = target / "bin" / "ffmpeg.exe"          # the Windows build we install
        if mine.is_file() and (not verify_exe or works(str(mine))):
            return True, f"ffmpeg: ready (already installed in {target})"
        work = target.parent / "ffmpeg-download"
        work.mkdir(parents=True, exist_ok=True)
        errs = []
        for label, url, sha_url in SOURCES:
            part = work / (url.rsplit("/", 1)[-1] + ".part")
            try:
                want = expected_sha(fetch_text(sha_url), url.rsplit("/", 1)[-1])
                if not want:
                    raise RuntimeError("sha256 file samajh nahi aaya")
                if not (part.exists() and part.stat().st_size >= MIN_ZIP and sha256_file(part) == want):
                    download(url, part)
                    got = sha256_file(part)
                    if got != want:
                        try:
                            part.unlink()
                        except Exception:
                            pass
                        raise RuntimeError(f"sha256 mismatch ({got[:12]} != {want[:12]})")
                files = extract(part, target)
                if verify_exe and not works(str(target / "bin" / "ffmpeg.exe")):
                    raise RuntimeError("ffmpeg.exe chal nahi raha (-version failed)")
                try:
                    part.unlink()
                    work.rmdir()
                except Exception:
                    pass
                return True, (f"ffmpeg: ready ({label}, sha256 ok, {len(files)} files, "
                              f"{time.monotonic() - t0:.0f} s)")
            except Exception as e:
                errs.append(f"{label}: {type(e).__name__}: {str(e)[:120]}")
        return False, "ffmpeg: skipped (download failed - " + " | ".join(errs) + ")"
    except Exception as e:
        return False, f"ffmpeg: skipped ({type(e).__name__}: {str(e)[:150]})"


# ------------------------------------------------------------------ NVENC probe
def _exe_sig(exe: str) -> str:
    try:
        st = os.stat(exe)
        return f"{exe}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        return exe


def probe_nvenc(force: bool = False, exe: str = "") -> bool:
    """True when h264_nvenc really encodes on this PC. Cached per ffmpeg build."""
    exe = exe or ffmpeg_path()
    if not exe:
        return False
    cf = _cache_file("ffmpeg_probe.json")
    c = _read_json(cf, {})
    sig = _exe_sig(exe)
    if not force and c.get("sig") == sig and "nvenc" in c:
        return bool(c["nvenc"])
    t0 = time.monotonic()
    rc, out = _run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=256x144:r=30", "-frames:v", "10",
                    "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=10)
    ok = rc == 0
    c.update({"sig": sig, "nvenc": ok, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "why": "" if ok else out.strip()[-300:]})
    _write_json(cf, c)
    try:
        from core import kaka_services as ks
        ks.timing("ffmpeg nvenc probe", time.monotonic() - t0, "ok" if ok else "no nvenc")
    except Exception:
        pass
    return ok


def mark_nvenc_failed(why: str = "") -> None:
    exe = ffmpeg_path()
    cf = _cache_file("ffmpeg_probe.json")
    c = _read_json(cf, {})
    c.update({"sig": _exe_sig(exe), "nvenc": False, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "why": ("failed at recording start: " + why)[-300:]})
    _write_json(cf, c)


# ------------------------------------------------------------------ dshow devices
def parse_dshow(text: str) -> list:
    """ffmpeg -list_devices output -> [{"name","alt","kind"}] (audio+video).
    Handles the new '"Name" (audio)' format and the old section format."""
    out = []
    section = None
    for raw in (text or "").splitlines():
        line = raw.split("]", 1)[1] if raw.lstrip().startswith("[") and "]" in raw else raw
        s = line.strip()
        low = s.lower()
        if "directshow video devices" in low:
            section = "video"
            continue
        if "directshow audio devices" in low:
            section = "audio"
            continue
        if low.startswith("alternative name"):
            q = s.find('"')
            if q >= 0 and out:
                out[-1]["alt"] = s[q + 1:s.rfind('"')] if s.rfind('"') > q else s[q + 1:]
            continue
        if s.startswith('"'):
            end = s.find('"', 1)
            if end <= 0:
                continue
            name = s[1:end]
            rest = s[end + 1:].strip().lower()
            if rest.startswith("(audio"):
                kind = "audio"
            elif rest.startswith("(video"):
                kind = "video"
            elif rest.startswith("(none") or "none" == name.lower():
                continue
            else:
                kind = section or "video"
            out.append({"name": name, "alt": "", "kind": kind})
    return out


def is_mix(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in MIX_WORDS)


def pick_audio(devices: list) -> tuple:
    """-> (system_mix_device_or_None, mic_device_or_None), each the string to
    pass as audio=<...> (friendly name, or the alternative name when two
    devices share the friendly name)."""
    aud = [d for d in devices if d.get("kind") == "audio"]
    names = [d["name"] for d in aud]

    def ident(d):
        if names.count(d["name"]) > 1 and d.get("alt"):
            return d["alt"]
        return d["name"]
    mix = next((ident(d) for d in aud if is_mix(d["name"])), None)
    mic = next((ident(d) for d in aud if not is_mix(d["name"])), None)
    return mix, mic


def list_devices(max_age_s: float = 600, force: bool = False, exe: str = "") -> list:
    exe = exe or ffmpeg_path()
    cf = _cache_file("ffmpeg_devices.json")
    c = _read_json(cf, {})
    if not force and c.get("devices") is not None and time.time() - float(c.get("t", 0)) < max_age_s:
        return c["devices"]
    if not exe or not _IS_WIN:
        return c.get("devices") or []
    _rc, out = _run([exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                    timeout=8)
    devs = parse_dshow(out)
    _write_json(cf, {"t": time.time(), "devices": devs})
    return devs


# ------------------------------------------------------------------ command build
EVEN = "crop=trunc(iw/2)*2:trunc(ih/2)*2"


def video_encoder_args(encoder: str, gop: int = 60) -> list:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23",
                "-b:v", "0", "-g", str(gop), "-bf", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", str(gop)]


GRABBERS = ("ddagrab", "gdigrab")


def video_input_args(region=None, fps: int = 30, grabber: str = "ddagrab") -> list:
    """ddagrab (Desktop Duplication: GPU, also catches full-screen games) or
    gdigrab (GDI, the old fallback). region=(l,t,r,b) physical pixels -> just
    that window."""
    if grabber == "ddagrab":
        opt = f"ddagrab=output_idx=0:draw_mouse=1:framerate={fps}"
        if region:
            l, t, r, b = [int(v) for v in region]
            w, h = (r - l) // 2 * 2, (b - t) // 2 * 2
            opt += f":video_size={w}x{h}:offset_x={max(0, l)}:offset_y={max(0, t)}"
        return ["-f", "lavfi", "-i", opt + ",hwdownload,format=bgra"]
    a = ["-thread_queue_size", "512", "-f", "gdigrab", "-framerate", str(fps),
         "-draw_mouse", "1"]
    if region:
        l, t, r, b = [int(v) for v in region]
        w, h = (r - l) // 2 * 2, (b - t) // 2 * 2
        a += ["-offset_x", str(l), "-offset_y", str(t), "-video_size", f"{w}x{h}"]
    return a + ["-i", "desktop"]


def grab_order() -> list:
    """ddagrab first unless it already failed on this PC (cached)."""
    c = _read_json(_cache_file("ffmpeg_probe.json"), {})
    return ["gdigrab", "ddagrab"] if c.get("ddagrab") is False else list(GRABBERS)


def mark_grabber(name: str, ok: bool) -> None:
    cf = _cache_file("ffmpeg_probe.json")
    c = _read_json(cf, {})
    c[name] = bool(ok)
    _write_json(cf, c)


def audio_input_args(dev: str) -> list:
    return ["-thread_queue_size", "1024", "-f", "dshow", "-audio_buffer_size", "50",
            "-i", f"audio={dev}"]


def _graph(n_audio: int) -> tuple:
    """-> (filter_complex, maps)"""
    g = f"[0:v]{EVEN},format=yuv420p[v]"
    maps = ["-map", "[v]"]
    if n_audio == 1:
        maps += ["-map", "1:a"]
    elif n_audio == 2:
        g += ";[1:a][2:a]amix=inputs=2:duration=longest:dropout_transition=0[a]"
        maps += ["-map", "[a]"]
    return g, maps


def build_record_cmd(out: str, encoder: str = "h264_nvenc", region=None, sys_audio=None,
                     mic=None, fps: int = 30, max_s: int = 7200, max_bytes: int = 0,
                     exe: str = "ffmpeg", video_in: list | None = None,
                     audio_in: list | None = None, grabber: str = "ddagrab") -> list:
    """The full recording command. video_in/audio_in override inputs (tests)."""
    cmd = [exe, "-hide_banner", "-loglevel", "warning", "-nostats", "-y"]
    cmd += video_in if video_in is not None else video_input_args(region, fps, grabber)
    auds = [d for d in (sys_audio, mic) if d]
    if audio_in is not None:
        for a in audio_in:
            cmd += a
        n = len(audio_in)
    else:
        for d in auds:
            cmd += audio_input_args(d)
        n = len(auds)
    g, maps = _graph(n)
    cmd += ["-filter_complex", g] + maps
    cmd += video_encoder_args(encoder, gop=fps * 2)
    if n:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += ["-t", str(int(max_s))]
    if max_bytes and max_bytes > 0:
        cmd += ["-fs", str(int(max_bytes))]
    cmd += [str(out)]
    return cmd


def build_replay_cmd(seg_dir: str, encoder: str = "h264_nvenc", sys_audio=None, fps: int = 30,
                     seg_s: int = 10, wrap: int = 5, max_s: int = 6 * 3600, exe: str = "ffmpeg",
                     video_in: list | None = None, audio_in: list | None = None,
                     grabber: str = "ddagrab") -> list:
    """Replay buffer: 1 s GOP, rolling .ts segments seg000..seg004."""
    cmd = [exe, "-hide_banner", "-loglevel", "warning", "-nostats", "-y"]
    cmd += video_in if video_in is not None else video_input_args(None, fps, grabber)
    if audio_in is not None:
        for a in audio_in:
            cmd += a
        n = len(audio_in)
    else:
        n = 0
        if sys_audio:
            cmd += audio_input_args(sys_audio)
            n = 1
    g, maps = _graph(min(n, 1))
    cmd += ["-filter_complex", g] + maps
    cmd += video_encoder_args(encoder, gop=fps)
    cmd += ["-force_key_frames", "expr:gte(t,n_forced*1)"]
    if n:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += ["-t", str(int(max_s)), "-f", "segment", "-segment_time", str(seg_s),
            "-segment_wrap", str(wrap), "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-segment_list", str(Path(seg_dir) / "segs.txt"), "-segment_list_type", "flat",
            "-segment_list_size", str(max(1, wrap - 1)),
            str(Path(seg_dir) / "seg%03d.ts")]
    return cmd


def build_replay_save_cmds(list_file: str, joined: str, out: str, seconds: int,
                           exe: str = "ffmpeg") -> list:
    """[concat newest segments -> joined.ts, cut the last N s -> out.mp4]
    (+1 s because a stream-copy cut starts on the 1 s keyframe grid)."""
    return [
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", "-f", "mpegts", str(joined)],
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-sseof", f"-{int(seconds) + 1}",
         "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(out)],
    ]


def concat_list_text(paths: list) -> str:
    lines = []
    for p in paths:
        s = str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{s}'")
    return "\n".join(lines) + "\n"


def probe_duration(path: str, exe: str = "") -> float:
    exe = exe or ffprobe_path()
    if not exe:
        return 0.0
    rc, out = _run([exe, "-v", "error", "-show_entries", "format=duration", "-of",
                    "default=noprint_wrappers=1:nokey=1", str(path)], timeout=15)
    try:
        return float(out.strip().splitlines()[-1]) if rc == 0 else 0.0
    except Exception:
        return 0.0


# ------------------------------------------------------------------ selfcheck
def selfcheck() -> tuple:
    """(ready, reason). Fast (<5 s), read-only apart from the probe cache."""
    try:
        exe = ffmpeg_path()
        if not exe:
            return False, "ffmpeg nahi mila (F:\\Kaka\\Tools\\ffmpeg)"
        if not works(exe):
            return False, "ffmpeg.exe chal nahi raha"
        c = _read_json(_cache_file("ffmpeg_probe.json"), {})
        enc = "NVENC" if c.get("nvenc") else ("x264 (NVENC nahi chala)" if "nvenc" in c else "encoder probe pehli recording pe")
        return True, f"ffmpeg ok, {enc}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _main(argv) -> int:
    if "--install" in argv:
        ok, line = install()
        _print(line)
        if ok:
            try:      # warm the NVENC cache now (10 s max) so the first recording is instant
                probe_nvenc(force=True)
            except Exception:
                pass
        return 0 if ok else 2
    if "--check" in argv:
        ok, why = selfcheck()
        _print(("ready" if ok else "skipped") + f" ({why})")
        return 0 if ok else 2
    _print("usage: python -m core.kaka_ffmpeg --install | --check")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
