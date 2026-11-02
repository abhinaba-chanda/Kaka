r"""
core/kaka_notes_panel.py - Kaka update 19: his notes, always on the desktop.

"notes mera screen par dikhe anytime": a small always-on-top, draggable,
semi-transparent panel (its own frameless top-level window, not inside the
HUD). It never takes focus (Qt WindowDoesNotAcceptFocus + WA_ShowWithoutActivating,
and on Windows WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW), so clicking it does not
pull his game / editor out of focus, and it only covers its own small rect.

    show() / hide() / toggle()   voice, HUD button, Ctrl+2 (Ctrl+Alt+2 if taken)
    is_visible()                 his saved preference
    start()                      run by kaka_services 2 s after Kaka starts

State: F:\Kaka\Data\notes_panel.json {x, y, visible}. First run: visible,
top-right of the primary screen. During game mode the panel hides (the saved
preference does not change) and comes back after.

Import is cheap: PyQt6 / ctypes are imported only inside functions. Nothing
here ever raises; every public function returns a short Hinglish status.
"""
from __future__ import annotations

import platform
import threading
from typing import Callable

_IS_WIN = platform.system() == "Windows"

PANEL_W = 260
MARGIN = 20
MAX_H_FRAC = 0.45
CONFIRM_MS = 3000
GAME_POLL_MS = 1000

_LOCK = threading.RLock()
_ST = {
    "pref": None,        # {"x", "y", "visible"} loaded lazily
    "panel": None,       # the QWidget (Qt thread only)
    "game_hidden": False,
    "hotkey": None,
    "btn": None,         # HUD button
    "started": False,
    "subscribed": False,
}


def _ks():
    from core import kaka_services as ks
    return ks


def _log(msg: str) -> None:
    try:
        _ks().log(msg)
    except Exception:
        pass


# ------------------------------------------------------------------ state file
def _state_path():
    return _ks().data_path("notes_panel.json")


def _pref() -> dict:
    with _LOCK:
        if _ST["pref"] is None:
            raw = {}
            try:
                raw = _ks().read_json(_state_path(), {})
            except Exception:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            pref = {"x": None, "y": None, "visible": True}
            try:
                if raw.get("x") is not None and raw.get("y") is not None:
                    pref["x"], pref["y"] = int(raw["x"]), int(raw["y"])
            except Exception:
                pass
            if "visible" in raw:
                pref["visible"] = bool(raw.get("visible"))
            _ST["pref"] = pref
        return _ST["pref"]


def _save_pref(**kw) -> None:
    with _LOCK:
        p = _pref()
        p.update(kw)
        try:
            _ks().write_json(_state_path(), dict(p))
        except Exception:
            pass


def _reset_module_state() -> None:
    """Tests: forget everything held in memory (as after a restart)."""
    with _LOCK:
        _ST["pref"] = None
        _ST["game_hidden"] = False


# ------------------------------------------------------------------ Qt helpers
def _qt_app():
    try:
        from PyQt6.QtWidgets import QApplication
        return QApplication.instance()
    except Exception:
        return None


def _in_ui(fn: Callable[[], None]) -> bool:
    try:
        return bool(_ks().run_in_ui(fn))
    except Exception:
        return False


def _in_game() -> bool:
    try:
        return bool(_ks().in_game_mode())
    except Exception:
        return False


def clamp_position(x, y, w, h, screens, primary):
    """(x, y) kept on a connected screen. screens / primary: QRect-likes with
    left()/top()/right()/bottom() (availableGeometry). None x/y -> default
    top-right of primary. Pure function (tested without Qt screens)."""
    def _default():
        return (primary.right() - w - MARGIN + 1, primary.top() + MARGIN)
    if x is None or y is None or primary is None:
        return _default() if primary is not None else (0, 0)
    target = None
    # the header's middle point must be on some screen
    hx, hy = x + w // 2, y + 12
    for g in screens or []:
        if g.left() <= hx <= g.right() and g.top() <= hy <= g.bottom():
            target = g
            break
    if target is None:
        return _default()
    nx = max(target.left(), min(x, target.right() - w + 1))
    ny = max(target.top(), min(y, target.bottom() - min(h, 40) + 1))
    return (nx, ny)


def _screens():
    from PyQt6.QtGui import QGuiApplication
    prim = QGuiApplication.primaryScreen()
    screens = [s.availableGeometry() for s in QGuiApplication.screens()]
    return screens, (prim.availableGeometry() if prim else (screens[0] if screens else None))


def _apply_noactivate(widget, user32=None) -> bool:
    """Windows: WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST on the real
    HWND, then re-assert topmost with SWP_NOACTIVATE (never SetForegroundWindow),
    so clicking or showing the panel never pulls focus from his game / editor."""
    if not _IS_WIN and user32 is None:
        return False
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x08000000, 0x00000080, 0x00000008
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        hwnd = int(widget.winId())
        u32 = user32 if user32 is not None else ctypes.windll.user32
        get = getattr(u32, "GetWindowLongPtrW", None) or u32.GetWindowLongW
        setf = getattr(u32, "SetWindowLongPtrW", None) or u32.SetWindowLongW
        if user32 is None:
            get.restype = ctypes.c_ssize_t
            get.argtypes = [ctypes.c_void_p, ctypes.c_int]
            setf.restype = ctypes.c_ssize_t
            setf.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        cur = get(hwnd, GWL_EXSTYLE)
        setf(hwnd, GWL_EXSTYLE, cur | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        return True
    except Exception as e:
        _log(f"SYS: Notes panel no-focus style nahi laga: {e}")
        return False


# ------------------------------------------------------------------ the widget
_CLS = {"panel": None}


def _panel_class():
    if _CLS["panel"] is not None:
        return _CLS["panel"]
    from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint
    from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QPushButton, QScrollArea, QFrame, QSizePolicy)

    BG = QColor(4, 16, 26, 222)          # ~87% opaque dark navy
    EDGE = QColor(165, 0, 68, 170)       # subtle Barca garnet
    TXT = "#e6f4fa"
    DIM = "#6f8a96"
    ACC = "#ffcc33"                      # Barca gold for numbers
    BLUE = "#3d7fd1"

    BTN = ("QPushButton {{ background: transparent; color: {c}; border: 1px solid {b};"
           " border-radius: 3px; padding: 0px 2px; }}"
           " QPushButton:hover {{ background: rgba(255,255,255,30); }}")

    class _Header(QWidget):
        def __init__(self, panel):
            super().__init__(panel)
            self._panel = panel
            self._off = None
            self.setCursor(Qt.CursorShape.SizeAllCursor)

        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                gp = ev.globalPosition().toPoint()
                self._off = gp - self._panel.frameGeometry().topLeft()
                ev.accept()

        def mouseMoveEvent(self, ev):
            if self._off is not None:
                self._panel.move(ev.globalPosition().toPoint() - self._off)
                ev.accept()

        def mouseReleaseEvent(self, ev):
            if self._off is not None:
                self._off = None
                self._panel.save_position()
                ev.accept()

    class NotesPanel(QWidget):
        def __init__(self):
            super().__init__(None)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint
                                | Qt.WindowType.Tool
                                | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setWindowTitle("Kaka Notes")
            self.setFixedWidth(PANEL_W)
            self._rows: list = []
            self._pending = {"id": None, "gen": 0}
            self._styled = False

            root = QVBoxLayout(self)
            root.setContentsMargins(10, 6, 8, 8)
            root.setSpacing(4)

            self.header = _Header(self)
            hl = QHBoxLayout(self.header)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            self.title = QLabel("NOTES")
            self.title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            self.title.setStyleSheet(f"color: {ACC}; background: transparent;")
            self.count_lbl = QLabel("")
            self.count_lbl.setFont(QFont("Courier New", 7))
            self.count_lbl.setStyleSheet(f"color: {DIM}; background: transparent;")
            self.hide_btn = QPushButton("-")
            self.hide_btn.setFixedSize(20, 18)
            self.hide_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.hide_btn.setToolTip("Chhupao (Ctrl+2 / 'notes dikhao')")
            self.hide_btn.setStyleSheet(BTN.format(c=TXT, b="rgba(255,255,255,60)"))
            self.hide_btn.clicked.connect(lambda: hide())
            hl.addWidget(self.title)
            hl.addWidget(self.count_lbl, 1)
            hl.addWidget(self.hide_btn)
            root.addWidget(self.header)

            self.scroll = QScrollArea(self)
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.scroll.setStyleSheet(
                "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                " QScrollBar:vertical { width: 6px; background: transparent; }"
                " QScrollBar::handle:vertical { background: rgba(255,255,255,60); border-radius: 3px; }")
            self.body = QWidget()
            self.body.setStyleSheet("background: transparent;")
            self.body_lay = QVBoxLayout(self.body)
            self.body_lay.setContentsMargins(0, 0, 0, 0)
            self.body_lay.setSpacing(3)
            self.scroll.setWidget(self.body)
            root.addWidget(self.scroll)

            self.empty_lbl = QLabel("Koi note nahi - bolo 'note karo ...'")
            self.empty_lbl.setWordWrap(True)
            self.empty_lbl.setFont(QFont("Segoe UI", 8))
            self.empty_lbl.setStyleSheet(f"color: {DIM}; background: transparent;")

            self.game_timer = QTimer(self)
            self.game_timer.setInterval(GAME_POLL_MS)
            self.game_timer.timeout.connect(self.check_game)
            self.game_timer.start()
            self.refresh()

        # -- painting: rounded translucent card
        def paintEvent(self, ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
            p.fillPath(path, BG)
            p.setPen(QPen(EDGE, 1))
            p.drawPath(path)
            p.end()

        # -- rows
        def _clear_rows(self):
            while self.body_lay.count():
                it = self.body_lay.takeAt(0)
                w = it.widget()
                if w is not None and w is not self.empty_lbl:
                    w.setParent(None)
                    w.deleteLater()
            self._rows = []

        def refresh(self):
            try:
                from core import kaka_notes as N
                notes = N.items()
            except Exception:
                notes = []
            self._clear_rows()
            if not notes:
                self.body_lay.addWidget(self.empty_lbl)
                self.empty_lbl.show()
            else:
                self.empty_lbl.hide()
                for n, it in enumerate(notes, 1):
                    self.body_lay.addWidget(self._make_row(n, it))
            self.body_lay.addStretch(1)
            open_n = len([i for i in notes if not i.get("done")])
            self.count_lbl.setText(f"{open_n}/{len(notes)}" if notes else "")
            self._fit_height()
            QTimer.singleShot(0, self._fit_height)     # again once word-wrap has settled

        def _make_row(self, n, it):
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 1, 0, 1)
            lay.setSpacing(4)
            done = bool(it.get("done"))
            num = QLabel(f"{n}.")
            num.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            num.setStyleSheet(f"color: {DIM if done else ACC}; background: transparent;")
            num.setAlignment(Qt.AlignmentFlag.AlignTop)
            num.setFixedWidth(22)
            txt = QLabel(it.get("text", ""))
            txt.setWordWrap(True)
            f = QFont("Segoe UI", 9)
            f.setStrikeOut(done)
            txt.setFont(f)
            txt.setStyleSheet(f"color: {DIM if done else TXT}; background: transparent;")
            txt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            tick = QPushButton("\u2713")
            tick.setFixedSize(20, 18)
            tick.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tick.setToolTip("Ho gaya / wapas")
            tick.setStyleSheet(BTN.format(c=("#33dd88" if done else DIM),
                                          b=("#33dd88" if done else "rgba(255,255,255,50)")))
            nid = it.get("id")
            tick.clicked.connect(lambda _=False, i=nid: self.on_tick(i))
            dl = QPushButton("x")
            dl.setFixedSize(20, 18)
            dl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            dl.setToolTip("Hatao (do baar dabao)")
            dl.setStyleSheet(BTN.format(c="#ff6688", b="rgba(255,102,136,90)"))
            dl.clicked.connect(lambda _=False, i=nid, b=dl: self.on_delete(i, b))
            lay.addWidget(num)
            lay.addWidget(txt, 1)
            lay.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            lay.addWidget(dl, 0, Qt.AlignmentFlag.AlignTop)
            self._rows.append({"id": nid, "num": num, "text": txt, "tick": tick,
                               "del": dl, "done": done})
            return row

        def _fit_height(self):
            try:
                avail = 800
                scr = self.screen() or None
                if scr is None:
                    _s, prim = _screens()
                    avail = prim.height() if prim is not None else 800
                else:
                    avail = scr.availableGeometry().height()
                cap = max(120, int(avail * MAX_H_FRAC))
                bw = PANEL_W - 18 - 8          # panel margins + scrollbar
                self.body_lay.activate()
                if self.body_lay.hasHeightForWidth():
                    bh = self.body_lay.totalHeightForWidth(bw)
                else:
                    bh = self.body.sizeHint().height()
                want = bh + self.header.sizeHint().height() + 22
                h = max(70, min(cap, want))
                self.setFixedHeight(h)
            except Exception:
                pass

        # -- buttons
        def on_tick(self, nid):
            try:
                from core import kaka_notes as N
                N.toggle_done(nid)
            except Exception as e:
                _log(f"SYS: Notes tick fail: {e}")

        def on_delete(self, nid, btn):
            try:
                if self._pending["id"] == nid:
                    self._pending["id"] = None
                    from core import kaka_notes as N
                    N.remove(ids=[nid])
                    return
                self._pending["id"] = nid
                self._pending["gen"] += 1
                gen = self._pending["gen"]
                for r in self._rows:
                    if r["id"] != nid:
                        r["del"].setText("x")
                        r["del"].setFixedSize(20, 18)
                btn.setText("hatao?")
                btn.setFixedSize(48, 18)
                QTimer.singleShot(CONFIRM_MS, lambda: self._reset_pending(gen))
            except Exception as e:
                _log(f"SYS: Notes delete fail: {e}")

        def _reset_pending(self, gen):
            try:
                if self._pending["gen"] != gen or self._pending["id"] is None:
                    return
                self._pending["id"] = None
                for r in self._rows:
                    r["del"].setText("x")
                    r["del"].setFixedSize(20, 18)
            except Exception:
                pass

        # -- position / visibility
        def save_position(self):
            try:
                pos = self.pos()
                _save_pref(x=int(pos.x()), y=int(pos.y()))
            except Exception:
                pass

        def place(self):
            try:
                p = _pref()
                screens, prim = _screens()
                x, y = clamp_position(p.get("x"), p.get("y"), self.width(),
                                      self.height(), screens, prim)
                self.move(QPoint(int(x), int(y)))
            except Exception:
                pass

        def show_quiet(self):
            if not self.isVisible():
                self.show()
            if not _apply_noactivate(self):
                if not _IS_WIN:
                    self.raise_()     # Windows: SetWindowPos(SWP_NOACTIVATE) above, no raise_()

        def check_game(self):
            try:
                if _in_game():
                    if self.isVisible():
                        _ST["game_hidden"] = True
                        self.hide()
                elif _ST["game_hidden"]:
                    _ST["game_hidden"] = False
                    if _pref().get("visible"):
                        self.show_quiet()
            except Exception:
                pass

    _CLS["panel"] = NotesPanel
    return NotesPanel


# ------------------------------------------------------------------ Qt-thread work
def _ensure_panel():
    """Qt thread only. The panel, created once (None if Qt is missing)."""
    if _ST["panel"] is not None:
        return _ST["panel"]
    if _qt_app() is None:
        return None
    try:
        cls = _panel_class()
        w = cls()
        w.place()
        _ST["panel"] = w
        _subscribe()
        return w
    except Exception as e:
        _log(f"SYS: Notes panel ban nahi paya: {e}")
        return None


def _ui_apply() -> None:
    """Qt thread: make the window match the preference (and game mode)."""
    try:
        want = bool(_pref().get("visible"))
        w = _ST["panel"]
        if want:
            w = _ensure_panel()
            if w is None:
                return
            if _in_game():
                _ST["game_hidden"] = True
                w.hide()
            else:
                w.refresh()
                w.show_quiet()
        elif w is not None:
            _ST["game_hidden"] = False
            w.hide()
        _update_button()
    except Exception as e:
        _log(f"SYS: Notes panel dikkat: {e}")


def _ui_refresh() -> None:
    try:
        w = _ST["panel"]
        if w is not None:
            w.refresh()
    except Exception:
        pass


def _on_notes_change() -> None:
    _in_ui(_ui_refresh)


def _subscribe() -> None:
    with _LOCK:
        if _ST["subscribed"]:
            return
        _ST["subscribed"] = True
    try:
        from core import kaka_notes as N
        N.on_change(_on_notes_change)
    except Exception:
        pass


# ------------------------------------------------------------------ public API
def is_visible() -> bool:
    """His saved preference (not whether game mode hid it right now)."""
    try:
        return bool(_pref().get("visible"))
    except Exception:
        return False


def is_shown() -> bool:
    """Whether the window is on screen right now."""
    try:
        w = _ST["panel"]
        return bool(w is not None and w.isVisible())
    except Exception:
        return False


def show() -> str:
    try:
        _save_pref(visible=True)
        if not _in_ui(_ui_apply):
            return "Notes save hain, par panel abhi nahi khul paya (HUD chal nahi raha)."
        if _in_game():
            return "Notes panel on hai - game band hote hi screen pe aa jayega."
        return "Notes panel screen pe dikh raha hai."
    except Exception as e:
        return f"Notes panel mein dikkat: {e}"


def hide() -> str:
    try:
        _save_pref(visible=False)
        _in_ui(_ui_apply)
        hk = hotkey_label()
        return "Notes panel chhupa diya." + (f" Wapas: '{hk}' ya 'notes dikhao'." if hk else " Wapas: 'notes dikhao'.")
    except Exception as e:
        return f"Notes panel mein dikkat: {e}"


def toggle() -> str:
    return hide() if is_visible() else show()


def refresh() -> None:
    _in_ui(_ui_refresh)


# ------------------------------------------------------------------ hotkey
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_2 = 0x32
CHORDS = [
    (MOD_CONTROL | MOD_NOREPEAT, VK_2, "Ctrl+2"),
    (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_2, "Ctrl+Alt+2"),
]
_HOTKEY_ID = 0x4B4E      # "KN" (sleep uses 0x4B4B)


class NotesHotkey:
    """RegisterHotKey in its own daemon thread with its own GetMessageW loop."""

    def __init__(self, on_press: Callable[[], None], log: Callable[[str], None] | None = None,
                 chords=None, user32=None):
        self._on_press = on_press
        self._log = log or (lambda m: None)
        self._chords = list(chords or CHORDS)
        self._user32 = user32
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()
        self.label = ""
        self.error = ""

    def _register(self, u32) -> str:
        for mods, vk, label in self._chords:
            try:
                ok = u32.RegisterHotKey(None, _HOTKEY_ID, mods, vk)
            except Exception as e:
                ok = False
                self.error = str(e)
            if ok:
                return label
            self._log(f"SYS: Notes shortcut {label} pehle se kisi aur program ka hai - agla try kar raha hoon.")
        return ""

    def _loop(self) -> None:
        u32 = None
        try:
            import ctypes
            u32 = self._user32 or ctypes.WinDLL("user32", use_last_error=True)
            try:
                self._tid = ctypes.WinDLL("kernel32").GetCurrentThreadId()
            except Exception:
                self._tid = 0
            self.label = self._register(u32)
        except Exception as e:
            self.error = str(e)
            self.label = ""
        self._ready.set()
        if not self.label:
            return
        try:
            import ctypes
            try:
                from ctypes import wintypes
                msg = wintypes.MSG()
                ref = ctypes.byref(msg)
            except Exception:
                msg, ref = None, None
            while True:
                r = u32.GetMessageW(ref, None, 0, 0)
                if r == 0 or r == -1:
                    break
                if msg is not None and msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        self._on_press()
                    except Exception as e:
                        print(f"[NotesHotkey] {e}")
        except Exception as e:
            self.error = str(e)
        finally:
            try:
                u32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass

    def start(self, timeout: float = 3.0) -> str:
        if not _IS_WIN and self._user32 is None:
            return ""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="kaka-notes-hotkey")
            self._thread.start()
        self._ready.wait(timeout)
        return self.label

    def stop(self) -> None:
        if self._tid:
            try:
                import ctypes
                ctypes.WinDLL("user32").PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass


def _hotkey_pressed() -> None:
    msg = toggle()
    _log(f"SYS: Notes shortcut: {msg}")


def start_hotkey(user32=None) -> str:
    try:
        with _LOCK:
            hk = _ST["hotkey"]
            if hk is None or (user32 is not None and hk._user32 is not user32):
                hk = NotesHotkey(_hotkey_pressed, log=_log, user32=user32)
                _ST["hotkey"] = hk
        label = hk.start()
        if label:
            _log(f"SYS: Notes shortcut {label} ready (notes panel dikhao/chhupao).")
        elif _IS_WIN or user32 is not None:
            _log("SYS: Notes shortcut nahi mila - HUD button aur 'notes dikhao' bolna chalega.")
        _update_button_async()
        return label
    except Exception as e:
        _log(f"SYS: Notes shortcut fail: {e}")
        return ""


def check_hotkey(user32=None) -> str:
    """Installer self-check: register Ctrl+2 (fallback Ctrl+Alt+2) on this
    thread and unregister it at once. -> the label that worked, or ""."""
    if not _IS_WIN and user32 is None:
        return ""
    try:
        import ctypes
        u32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
        hk = NotesHotkey(lambda: None, user32=u32)
        label = hk._register(u32)
        if label:
            try:
                u32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass
        return label
    except Exception:
        return ""


def hotkey_label() -> str:
    try:
        hk = _ST["hotkey"]
        return hk.label if hk is not None else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ HUD button
def _button_text() -> str:
    hk = hotkey_label()
    base = "NOTES CHHUPAO" if is_visible() else "NOTES DIKHAO"
    return f"\U0001F4DD  {base}" + (f"  [{hk}]" if hk else "")


def _update_button() -> None:
    try:
        b = _ST["btn"]
        if b is not None:
            b.setText(_button_text())
    except Exception:
        pass


def _update_button_async() -> None:
    if _ST["btn"] is not None:
        _in_ui(_update_button)


def inject_hud_button(win=None):
    """Qt thread: a NOTES DIKHAO / CHHUPAO button right after the HUD's sleep
    button. Returns the button, or None (logged) if the HUD looks different."""
    try:
        if _ST["btn"] is not None:
            return _ST["btn"]
        if win is None:
            j = _ks().jarvis()
            win = getattr(getattr(j, "ui", None), "_win", None)
        sleep_btn = getattr(win, "_sleep_btn", None) if win is not None else None
        parent = sleep_btn.parentWidget() if sleep_btn is not None else None
        lay = parent.layout() if parent is not None else None
        if lay is None:
            _log("SYS: Notes HUD button skip (HUD layout nahi mila) - Ctrl+2 / 'notes dikhao' chalega.")
            return None
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QPushButton
        b = QPushButton(parent)
        b.setObjectName("KakaNotesButton")
        b.setFixedHeight(30)
        b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton { background: #0b0b0b; color: #5ab8cc;"
            " border: 1px solid #0d3347; border-radius: 3px; }"
            " QPushButton:hover { color: #00d4ff; border-color: #00d4ff; }"
            " QPushButton:pressed { background: #001a26; }")
        b.clicked.connect(lambda _=False: toggle())
        idx = lay.indexOf(sleep_btn)
        if idx >= 0 and hasattr(lay, "insertWidget"):
            lay.insertWidget(idx + 1, b)
        else:
            lay.addWidget(b)
        _ST["btn"] = b
        _update_button()
        try:
            setattr(win, "_notes_btn", b)
        except Exception:
            pass
        return b
    except Exception as e:
        _log(f"SYS: Notes HUD button skip: {e}")
        return None


# ------------------------------------------------------------------ start
def _ui_start() -> None:
    try:
        _subscribe()
        inject_hud_button()
        if is_visible():
            _ui_apply()
    except Exception as e:
        _log(f"SYS: Notes panel start dikkat: {e}")


def start() -> str:
    """kaka_services starter (background thread, ~2 s after Kaka opens)."""
    with _LOCK:
        if _ST["started"]:
            return "already"
        _ST["started"] = True
    try:
        if _qt_app() is None:
            with _LOCK:
                _ST["started"] = False
            return ""
        _in_ui(_ui_start)
        start_hotkey()
        return "ok"
    except Exception as e:
        _log(f"SYS: Notes panel start fail: {e}")
        return ""


try:
    _ks().on_start(start)
except Exception:
    pass
