r"""
core/kaka_pc.py - Kaka update 18: volume, brightness and media keys.

Primitives only; actions/media_control.py is the voice tool on top.
Everything that touches Windows sits behind a tiny module-level function
(_press_vk, _endpoint, _run_ps, _ddc_set) so the unit tests can replace it.

VOLUME   pycaw/comtypes IAudioEndpointVolume when importable (exact level,
         real mute), else media keys through ctypes keybd_event (each step is
         2 %; "volume 40" = 50 steps down to 0, then 20 up).
BRIGHTNESS  WMI (laptop panel) through one PowerShell call, no admin.
         External monitor -> DDC/CI through dxva2 (ctypes) -> else an honest
         "ye monitor brightness control support nahi karta".
MEDIA    VK_MEDIA_PLAY_PAUSE / NEXT / PREV / STOP through keybd_event - works
         for Spotify, YouTube in the browser, VLC, ...

Never raises out of a public function.  Top level is stdlib only.
"""
from __future__ import annotations

import subprocess
import sys
import time

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

MEDIA_VK = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "stop": VK_MEDIA_STOP,
}

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW


def _clamp(v) -> int:
    try:
        return max(0, min(100, int(round(float(v)))))
    except Exception:
        return 0


# ---------------------------------------------------------------- OS seams
def _press_vk(vk: int, times: int = 1, gap: float = 0.004) -> bool:
    """Press a virtual key `times` times. False when not on Windows."""
    if sys.platform != "win32":
        return False
    import ctypes
    ke = ctypes.windll.user32.keybd_event
    for _ in range(max(0, int(times))):
        ke(vk, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        ke(vk, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)
        if gap:
            time.sleep(gap)
    return True


def _com_init() -> None:
    """Executor threads have no COM apartment; pycaw needs one."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)   # MTA; S_FALSE / changed-mode are fine
    except Exception:
        pass


def _endpoint():
    """IAudioEndpointVolume or None (pycaw/comtypes missing or no device).
    Reuses computer_settings._win_endpoint_volume, which already copes with
    both old and new pycaw."""
    if sys.platform != "win32":
        return None
    try:
        _com_init()
        try:
            from actions.computer_settings import _win_endpoint_volume
        except Exception:
            _win_endpoint_volume = None
        if _win_endpoint_volume is not None:
            return _win_endpoint_volume()
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        sp = AudioUtilities.GetSpeakers()
        ep = getattr(sp, "EndpointVolume", None)
        if ep is not None:
            return ep
        dev = getattr(sp, "_dev", sp)
        return cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                    POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def _run_ps(script: str, timeout: float = 8.0):
    """(returncode, stdout, stderr) of a hidden PowerShell; (-1,'',why) on error."""
    if sys.platform != "win32":
        return (-1, "", "not windows")
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=_NO_WINDOW)
        return (r.returncode, r.stdout or "", r.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", "timeout")
    except Exception as e:
        return (-1, "", str(e))


# ---------------------------------------------------------------- volume
def volume_get():
    ep = _endpoint()
    if ep is None:
        return None
    try:
        return _clamp(ep.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return None


def volume_set(level) -> dict:
    """{'ok', 'level', 'exact', 'how', 'before'}"""
    level = _clamp(level)
    ep = _endpoint()
    if ep is not None:
        try:
            before = None
            try:
                before = _clamp(ep.GetMasterVolumeLevelScalar() * 100)
            except Exception:
                pass
            ep.SetMasterVolumeLevelScalar(level / 100.0, None)
            if level > 0:
                try:
                    if ep.GetMute():
                        ep.SetMute(0, None)
                except Exception:
                    pass
            now = level
            try:
                now = _clamp(ep.GetMasterVolumeLevelScalar() * 100)
            except Exception:
                pass
            return {"ok": True, "level": now, "exact": True, "how": "pycaw", "before": before}
        except Exception:
            pass
    # no-dependency fallback: to 0, then up in 2 % steps
    if _press_vk(VK_VOLUME_DOWN, 50) and _press_vk(VK_VOLUME_UP, int(round(level / 2.0))):
        return {"ok": True, "level": int(round(level / 2.0)) * 2, "exact": False,
                "how": "keys", "before": None}
    return {"ok": False, "level": None, "exact": False, "how": "none", "before": None}


def volume_step(delta: int) -> dict:
    """+/- delta percent (default caller uses 10)."""
    delta = int(delta or 0)
    ep = _endpoint()
    if ep is not None:
        try:
            cur = _clamp(ep.GetMasterVolumeLevelScalar() * 100)
            r = volume_set(_clamp(cur + delta))
            r["before"] = cur
            if r.get("ok"):
                return r
        except Exception:
            pass
    vk = VK_VOLUME_UP if delta > 0 else VK_VOLUME_DOWN
    if _press_vk(vk, max(1, abs(delta) // 2)):
        return {"ok": True, "level": None, "exact": False, "how": "keys", "before": None}
    return {"ok": False, "level": None, "exact": False, "how": "none", "before": None}


def set_mute(on: bool) -> dict:
    """Real mute/unmute with pycaw (so "mute" never UN-mutes); else the mute
    key, which only toggles - reported as such ('toggled': True)."""
    ep = _endpoint()
    if ep is not None:
        try:
            was = None
            try:
                was = bool(ep.GetMute())
            except Exception:
                pass
            ep.SetMute(1 if on else 0, None)
            return {"ok": True, "toggled": False, "was": was, "how": "pycaw"}
        except Exception:
            pass
    if _press_vk(VK_VOLUME_MUTE, 1):
        return {"ok": True, "toggled": True, "was": None, "how": "keys"}
    return {"ok": False, "toggled": False, "was": None, "how": "none"}


# ---------------------------------------------------------------- brightness
_WMI_SET = "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop"
_WMI_GET = "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction Stop"


def brightness_script(level=None, delta=None) -> str:
    """One PowerShell call: (optionally) set, then print 'OK <before> <after>'
    or 'NOSUPPORT <why>'."""
    lines = ["$ErrorActionPreference='Stop'", "try {",
             f"  $g = @({_WMI_GET})[0]",
             "  $b = [int]$g.CurrentBrightness"]
    if level is not None:
        lines.append(f"  $t = {_clamp(level)}")
    elif delta is not None:
        lines.append(f"  $t = [Math]::Max(0,[Math]::Min(100,$b + ({int(delta)})))")
    else:
        lines.append("  $t = -1")
    lines += [
        "  if ($t -ge 0) {",
        f"    {_WMI_SET} | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=[uint32]1;Brightness=[byte]$t}} | Out-Null",
        "    Start-Sleep -Milliseconds 150",
        f"    $a = [int](@({_WMI_GET})[0].CurrentBrightness)",
        "  } else { $a = $b }",
        "  Write-Output ('OK ' + $b + ' ' + $a)",
        "} catch { Write-Output ('NOSUPPORT ' + $_.Exception.Message) }",
    ]
    return "\n".join(lines)


def _ddc_set(level=None, delta=None):
    """External monitor via DDC/CI (dxva2). Returns (before, after) or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32, dxva2 = ctypes.windll.user32, ctypes.windll.dxva2

        class PHYS(ctypes.Structure):
            _fields_ = [("h", wintypes.HANDLE), ("d", wintypes.WCHAR * 128)]
        mons = []
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user32.EnumDisplayMonitors(None, None, PROC(lambda h, *_: mons.append(h) or True), 0)
        out = None
        for hm in mons:
            n = wintypes.DWORD()
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hm, ctypes.byref(n)) or not n.value:
                continue
            arr = (PHYS * n.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hm, n.value, arr):
                continue
            try:
                for p in arr:
                    mn, cur, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                    if not dxva2.GetMonitorBrightness(p.h, ctypes.byref(mn), ctypes.byref(cur), ctypes.byref(mx)):
                        continue
                    span = max(1, mx.value - mn.value)
                    before = _clamp((cur.value - mn.value) * 100 / span)
                    tgt = _clamp(level) if level is not None else _clamp(before + int(delta or 0))
                    raw = mn.value + int(round(tgt * span / 100))
                    if dxva2.SetMonitorBrightness(p.h, raw):
                        out = (before, tgt)
            finally:
                dxva2.DestroyPhysicalMonitors(n.value, arr)
        return out
    except Exception:
        return None


def brightness_set(level=None, delta=None) -> dict:
    """{'ok', 'before', 'level', 'how', 'why'} - why is set when unsupported."""
    rc, out, err = _run_ps(brightness_script(level=level, delta=delta), timeout=8.0)
    line = ""
    for ln in (out or "").splitlines():
        if ln.startswith("OK ") or ln.startswith("NOSUPPORT"):
            line = ln.strip()
    if line.startswith("OK "):
        try:
            _ok, b, a = line.split()
            return {"ok": True, "before": int(b), "level": int(a), "how": "wmi", "why": ""}
        except Exception:
            pass
    why = line[len("NOSUPPORT"):].strip() if line else (err.strip()[:160] or "no answer")
    d = _ddc_set(level=level, delta=delta)
    if d:
        return {"ok": True, "before": d[0], "level": d[1], "how": "ddc", "why": ""}
    return {"ok": False, "before": None, "level": None, "how": "none", "why": why}


def brightness_get():
    r = brightness_set(level=None, delta=None)
    return r["level"] if r.get("ok") and r.get("how") == "wmi" else None


# ---------------------------------------------------------------- media
def media_key(which: str) -> bool:
    vk = MEDIA_VK.get(which)
    if vk is None:
        return False
    return _press_vk(vk, 1, gap=0)
