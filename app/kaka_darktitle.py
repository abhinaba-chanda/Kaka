"""KAKA: dark Windows title bar on every Kaka window, even when Windows is in
light mode.

Windows paints the title bar of a normal window in the system colours, so in
light mode Kaka's dark HUD sits under a white bar. DWM lets an app ask for the
dark ("immersive dark mode") title bar per window. install(app) puts a small
event filter on the QApplication that does this for every top-level window
(main window, dialogs, settings, panels) the moment it is shown.

Windows only and fail-safe: on other systems, or if anything goes wrong, it
does nothing and Kaka runs as before. Turn it off with KAKA_DARK_TITLE=0.
"""
from __future__ import annotations

import os
import sys

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20          # Windows 10 2004+ / Windows 11
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19      # Windows 10 1809 - 1909
_DWMWA_BORDER_COLOR = 34                     # Windows 11 (build 22000+) only
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_CAPTION_RGB = (0x0A, 0x0A, 0x0A)            # near-black bar on Windows 11
_TEXT_RGB = (0xFF, 0xFF, 0xFF)

_filter = None                               # keep a reference (Qt does not)
_dwm = None
_build = 0
_logged = False


def _log(msg: str) -> None:
    global _logged
    if _logged:
        return
    _logged = True
    try:
        sys.stderr.write(f"[kaka_darktitle] {msg}\n")
    except Exception:
        pass


def _colorref(rgb) -> int:
    r, g, b = rgb
    return (b << 16) | (g << 8) | r


def _load() -> bool:
    global _dwm, _build
    if _dwm is not None:
        return bool(_dwm)
    _dwm = False
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        dwm = ctypes.WinDLL("dwmapi")
        fn = dwm.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = ctypes.c_long
        try:
            _build = int(sys.getwindowsversion().build)
        except Exception:
            _build = 0
        _dwm = fn
        return True
    except Exception as e:
        _log(f"dwmapi not available: {e!r}")
        return False


def _set_int(hwnd: int, attr: int, value: int) -> bool:
    import ctypes
    v = ctypes.c_int(value)
    return _dwm(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0


def apply_hwnd(hwnd: int, refresh: bool = False) -> bool:
    """Dark title bar for one native window handle. Returns True on success."""
    if not hwnd or not _load():
        return False
    try:
        ok = _set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        if not ok:
            ok = _set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        if _build >= 22000:                  # Windows 11: real black bar too
            for attr, rgb in ((_DWMWA_CAPTION_COLOR, _CAPTION_RGB),
                              (_DWMWA_TEXT_COLOR, _TEXT_RGB),
                              (_DWMWA_BORDER_COLOR, _CAPTION_RGB)):
                try:
                    ok = _set_int(hwnd, attr, _colorref(rgb)) or ok
                except Exception:
                    pass
        if ok and refresh:
            _refresh(hwnd)
        if not ok:
            _log(f"DwmSetWindowAttribute refused (Windows build {_build})")
        return ok
    except Exception as e:
        _log(f"apply failed: {e!r}")
        return False


def _refresh(hwnd: int) -> None:
    """Make an already-visible window repaint its frame with the new colours."""
    try:
        import ctypes
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP)
    except Exception:
        pass


def _wants_title(flags) -> bool:
    """Only real windows with a title bar (not menus, tooltips, popups)."""
    try:
        from PyQt6.QtCore import Qt
        t = flags & Qt.WindowType.WindowType_Mask
        if t in (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                 Qt.WindowType.SplashScreen, Qt.WindowType.Desktop,
                 Qt.WindowType.SubWindow):
            return False
        return True
    except Exception:
        return True


def apply_widget(w, refresh: bool = False) -> bool:
    """Dark title bar for a top-level QWidget or a QWindow."""
    try:
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtGui import QWindow
        if isinstance(w, QWidget):
            if not w.isWindow() or not _wants_title(w.windowFlags()):
                return False
            if w.windowHandle() is None:      # no native window yet
                return False
            return apply_hwnd(int(w.winId()), refresh)
        if isinstance(w, QWindow):
            if w.parent() is not None or not _wants_title(w.flags()):
                return False
            return apply_hwnd(int(w.winId()), refresh)
    except Exception as e:
        _log(f"widget apply failed: {e!r}")
    return False


def install(app=None) -> bool:
    """Hook the QApplication so every Kaka window gets a dark title bar.
    Safe to call more than once; does nothing off Windows."""
    global _filter
    try:
        if os.environ.get("KAKA_DARK_TITLE", "1").strip().lower() in ("0", "off", "no", "false"):
            return False
        if sys.platform != "win32" or not _load():
            return False
        from PyQt6.QtCore import QObject, QEvent
        from PyQt6.QtWidgets import QApplication, QWidget
        from PyQt6.QtGui import QWindow
        app = app or QApplication.instance()
        if app is None:
            return False
        if _filter is not None:
            return True

        watch = {QEvent.Type.Show, QEvent.Type.WinIdChange}

        class _DarkTitleFilter(QObject):
            def eventFilter(self, obj, ev):     # noqa: N802 (Qt name)
                try:
                    if ev.type() in watch and isinstance(obj, (QWidget, QWindow)):
                        apply_widget(obj, refresh=ev.type() == QEvent.Type.WinIdChange)
                except Exception:
                    pass
                return False                     # never eat the event

        _filter = _DarkTitleFilter(app)
        app.installEventFilter(_filter)
        for w in app.topLevelWidgets():          # windows already on screen
            try:
                if w.isVisible():
                    apply_widget(w, refresh=True)
            except Exception:
                pass
        return True
    except Exception as e:
        _log(f"install failed: {e!r}")
        return False
