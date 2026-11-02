r"""
core/kaka_screen.py - Kaka update 18: take ONE screenshot in memory.

    from core import kaka_screen
    img, mime, info = kaka_screen.capture()          # active window, JPEG
    img, mime, info = kaka_screen.capture(mode="screen")
    # img is None on failure; info["error"] says why (Hinglish, sayable).

* Active window = the foreground window; if that is Kaka's own HUD (or
  nothing useful), the next real window under it in z-order. Its rectangle is
  read DPI-aware (DWM extended frame bounds, physical pixels - he runs 125 %).
* Grabbers, first that works: mss -> PIL.ImageGrab -> PowerShell
  System.Drawing (temp PNG, deleted in `finally`).
* Downscaled to max 1280 px long side, JPEG quality 80. Nothing is ever kept
  on disk.
"""
from __future__ import annotations

import os
import sys
import time

MAX_SIDE = 1280
QUALITY = 80
_IS_WIN = sys.platform == "win32"


def kaka_titles() -> set:
    """Same rule as main.py _kaka_titles(): Kaka's own HUD window titles."""
    names = {"KAKA", "KAKA \u2014 MARK LVI"}
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        n = (json.loads(p.read_text(encoding="utf-8")).get("assistant_name") or "").strip()
        if n:
            names.add(n.upper())
    except Exception:
        pass
    return names


def _is_kaka(title: str) -> bool:
    t = (title or "").strip().upper()
    return bool(t) and t in kaka_titles()


# ------------------------------------------------------------ OS hooks (mockable)
def _windows_in_zorder() -> list:
    """[(hwnd, title, rect)] visible, non-minimised top-level windows, topmost
    first, starting with the foreground window."""
    if not _IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindow.restype = wintypes.HWND
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    old_ctx = None
    try:   # per-thread DPI awareness only - never changes Kaka's process setting
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old_ctx = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        old_ctx = None
    out = []
    try:
        fg = u.GetForegroundWindow()
        order = []
        if fg:
            order.append(fg)
        h = u.GetWindow(fg, 0) if fg else None          # GW_HWNDFIRST
        n = 0
        while h and n < 400:
            if h != fg:
                order.append(h)
            h = u.GetWindow(h, 2)                          # GW_HWNDNEXT
            n += 1
        for h in order:
            try:
                if not u.IsWindowVisible(h) or u.IsIconic(h):
                    continue
                if _cloaked_or_tool(u, h):
                    continue
                ln = u.GetWindowTextLengthW(h)
                if ln <= 0:
                    continue
                buf = ctypes.create_unicode_buffer(ln + 1)
                u.GetWindowTextW(h, buf, ln + 1)
                rect = _rect_of(h)
                if rect:
                    out.append((int(h), buf.value, rect))
            except Exception:
                continue
    finally:
        if old_ctx:
            try:
                u.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))
            except Exception:
                pass
    return out


def _cloaked_or_tool(u, h) -> bool:
    """UWP windows that are 'visible' but cloaked, and tool/overlay windows."""
    import ctypes
    from ctypes import wintypes
    try:
        if u.GetWindowLongW(wintypes.HWND(h), -20) & 0x80:      # WS_EX_TOOLWINDOW
            return True
    except Exception:
        pass
    try:
        c = ctypes.c_int(0)
        if ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
                wintypes.HWND(h), 14, ctypes.byref(c), ctypes.sizeof(c)) == 0 and c.value:
            return True                                         # DWMWA_CLOAKED
    except Exception:
        pass
    return False


def _rect_of(hwnd) -> tuple:
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    try:
        dwm = ctypes.WinDLL("dwmapi")
        if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r),
                                     ctypes.sizeof(r)) == 0:
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    u = ctypes.WinDLL("user32", use_last_error=True)
    if u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return (r.left, r.top, r.right, r.bottom)
    return ()


def _virtual_screen() -> tuple:
    if not _IS_WIN:
        return (0, 0, 1920, 1080)
    try:
        import ctypes
        u = ctypes.WinDLL("user32")
        x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        if w > 0 and h > 0:
            return (x, y, x + w, y + h)
    except Exception:
        pass
    return (0, 0, 1920, 1080)


def pick_window(wins: list, screen: tuple):
    """First window that is not Kaka's HUD, not the desktop/taskbar and not
    tiny. -> (title, rect) or None."""
    skip = {"program manager", "start", "task switching", "windows input experience",
            "search", "action center", "notification centre", "notification center"}
    for _h, title, rect in wins:
        if _is_kaka(title) or (title or "").strip().lower() in skip:
            continue
        l, t, r, b = rect
        l, t = max(l, screen[0]), max(t, screen[1])
        r, b = min(r, screen[2]), min(b, screen[3])
        if r - l < 120 or b - t < 80:
            continue
        return title, (l, t, r, b)
    return None


def _grab_mss(rect):
    import mss
    import mss.tools
    l, t, r, b = rect
    with mss.mss() as s:
        shot = s.grab({"left": l, "top": t, "width": r - l, "height": b - t})
        return mss.tools.to_png(shot.rgb, shot.size)


def _grab_pil(rect):
    from PIL import ImageGrab
    import io
    im = ImageGrab.grab(bbox=rect, all_screens=True)
    bio = io.BytesIO()
    im.save(bio, "PNG")
    return bio.getvalue()


def _run_ps(script: str, timeout: float = 15.0):
    import subprocess
    flags = 0x08000000 if _IS_WIN else 0
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-Command", script], capture_output=True, text=True,
                          timeout=timeout, creationflags=flags)


def _grab_ps(rect):
    import tempfile
    l, t, r, b = rect
    fd, path = tempfile.mkstemp(prefix="kaka_shot_", suffix=".png")
    os.close(fd)
    try:
        ps = ("Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] public static extern "
              "bool SetProcessDPIAware();' -Name D -Namespace K | Out-Null;"
              "[K.D]::SetProcessDPIAware() | Out-Null;"          # physical pixels at 125 %
              "Add-Type -AssemblyName System.Drawing;"
              f"$b=New-Object System.Drawing.Bitmap({r - l},{b - t});"
              "$g=[System.Drawing.Graphics]::FromImage($b);"
              f"$g.CopyFromScreen({l},{t},0,0,$b.Size);"
              f"$b.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png);"
              "$g.Dispose();$b.Dispose()")
        _run_ps(ps)
        with open(path, "rb") as f:
            data = f.read()
        return data or None
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


GRABBERS = [("mss", _grab_mss), ("pil", _grab_pil), ("powershell", _grab_ps)]


def to_jpeg(png: bytes, max_side: int = MAX_SIDE, quality: int = QUALITY) -> tuple:
    """-> (bytes, mime). Falls back to the PNG itself if PIL is missing."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png))
        im = im.convert("RGB")
        w, h = im.size
        s = max(w, h)
        if s > max_side:
            k = max_side / float(s)
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=quality, optimize=True)
        return bio.getvalue(), "image/jpeg"
    except Exception:
        return png, "image/png"


def capture(mode: str = "window") -> tuple:
    """-> (bytes|None, mime, info). info: mode, title, size, grabber, error."""
    t0 = time.monotonic()
    info = {"mode": "screen", "title": "", "grabber": "", "error": ""}
    screen = _virtual_screen()
    rect = screen
    if mode != "screen":
        try:
            hit = pick_window(_windows_in_zorder(), screen)
        except Exception:
            hit = None
        if hit:
            info["mode"], info["title"], rect = "window", hit[0], hit[1]
    errs = []
    png = None
    for name, fn in GRABBERS:
        try:
            png = fn(rect)
            if png:
                info["grabber"] = name
                break
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
    if not png:
        info["error"] = "screenshot nahi le paaya (" + "; ".join(errs)[:200] + ")"
        return None, "", info
    img, mime = to_jpeg(png)
    del png
    info["bytes"] = len(img)
    info["secs"] = round(time.monotonic() - t0, 2)
    return img, mime, info
