r"""
core/kaka_research_panel.py - KAKA: the Deep Research report on screen, and
its PDF copy.

A frameless, always-on-top, draggable, scrollable card in the same look as
the notes panel (dark navy, Barca garnet edge, gold headings). While a
research runs it shows live progress (sub-question 2/5 ...); when it is done
the full report, with every source a clickable link. Buttons: PDF (opens the
saved PDF), Folder (opens Data\Research), - (hide). Sized from the primary
screen's available area, so it fits 1920x1080 at 125 % (1536x864 logical).

write_pdf(html, path) renders HTML to an A4 PDF with Qt's own QTextDocument +
QPdfWriter (no extra package).

Import is cheap (PyQt6 only inside functions); nothing here raises.
"""
from __future__ import annotations

import os
import platform
import subprocess
import threading
from typing import Optional

_IS_WIN = platform.system() == "Windows"

PANEL_W = 660            # logical px (max); shrinks on small screens
MARGIN = 20
NOTES_GAP = 290          # leave the notes panel (260 px, top-right) visible

_ST = {"panel": None, "rep": None, "paths": None}
_CLS = {"panel": None}


def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def _log(msg: str) -> None:
    ks = _ks()
    try:
        (ks.log if ks else print)(msg)
    except Exception:
        pass


def _qt_app():
    try:
        from PyQt6.QtWidgets import QApplication
        return QApplication.instance()
    except Exception:
        return None


def _on_qt_thread() -> bool:
    try:
        from PyQt6.QtCore import QThread, QCoreApplication
        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def _in_ui(fn) -> bool:
    """Run fn on the Qt thread (directly when already there)."""
    if _on_qt_thread():
        try:
            fn()
            return True
        except Exception as e:
            _log(f"SYS: Research panel dikkat: {e}")
            return False
    ks = _ks()
    try:
        return bool(ks and ks.run_in_ui(fn))
    except Exception:
        return False


def _in_game() -> bool:
    ks = _ks()
    try:
        return bool(ks and ks.in_game_mode())
    except Exception:
        return False


def geometry_for(avail_left, avail_top, avail_w, avail_h) -> tuple:
    """(x, y, w, h) for the panel inside the available screen area. Pure."""
    w = int(min(PANEL_W, max(420, avail_w * 0.45)))
    h = int(max(360, avail_h - 2 * MARGIN))
    x = avail_left + avail_w - w - MARGIN - NOTES_GAP
    if x < avail_left + MARGIN:
        x = avail_left + avail_w - w - MARGIN
    x = max(avail_left, x)
    y = avail_top + MARGIN
    return x, y, w, min(h, avail_h)


# ------------------------------------------------------------------ PDF
def _write_pdf_here(html: str, path: str) -> tuple:
    from PyQt6.QtCore import QMarginsF, QSizeF
    from PyQt6.QtGui import QPdfWriter, QPageSize, QPageLayout, QTextDocument, QFont
    tmp = path + ".tmp"
    w = QPdfWriter(tmp)
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageMargins(QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter)
    w.setResolution(120)
    w.setTitle("Kaka Deep Research")
    w.setCreator("Kaka")
    doc = QTextDocument()
    f = QFont("Segoe UI" if _IS_WIN else "DejaVu Sans")
    f.setPointSizeF(10.5)
    doc.setDefaultFont(f)
    doc.setDefaultStyleSheet(
        "h1 { font-size: 20pt; } h2 { font-size: 14pt; margin-top: 14px; } "
        "h3 { font-size: 12pt; } p { margin-bottom: 6px; } li { margin-bottom: 3px; }")
    doc.setHtml(html)
    doc.print(w) if hasattr(doc, "print") else doc.print_(w)
    del w                                   # QPdfWriter finishes the file on destruction
    if not os.path.exists(tmp) or os.path.getsize(tmp) < 500:
        return False, "PDF khaali bana"
    os.replace(tmp, path)
    return True, ""


def write_pdf(html: str, path: str, timeout: float = 30.0) -> tuple:
    """-> (ok, error). Uses the Qt thread when Kaka's HUD is running."""
    try:
        if _qt_app() is None:
            from PyQt6.QtGui import QGuiApplication
            if os.environ.get("QT_QPA_PLATFORM") is None and not _IS_WIN:
                os.environ["QT_QPA_PLATFORM"] = "offscreen"
            _ST["app"] = QGuiApplication([])          # stand-alone (tests / no HUD)
        if _on_qt_thread():
            return _write_pdf_here(html, path)
        box, ev = {}, threading.Event()

        def _go():
            try:
                box["r"] = _write_pdf_here(html, path)
            except Exception as e:
                box["r"] = (False, f"{type(e).__name__}: {e}")
            ev.set()
        if not _in_ui(_go):
            return False, "Qt nahi chal raha"
        if not ev.wait(timeout):
            return False, "PDF banane mein der lagi"
        return box.get("r", (False, "?"))
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------ open helpers
def open_path(path: str) -> bool:
    try:
        if not path or not os.path.exists(path):
            return False
        if _IS_WIN:
            os.startfile(path)              # noqa: S606 - his own file
        else:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return True
    except Exception:
        return False


def open_folder(path: str) -> bool:
    try:
        if _IS_WIN and path and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True
        return open_path(os.path.dirname(path) if path else "")
    except Exception:
        return False


# ------------------------------------------------------------------ widget
def _panel_class():
    if _CLS["panel"] is not None:
        return _CLS["panel"]
    from PyQt6.QtCore import Qt, QRectF, QPoint
    from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QPushButton, QTextBrowser, QProgressBar, QFrame)

    BG = QColor(4, 16, 26, 238)
    EDGE = QColor(165, 0, 68, 190)
    TXT = "#e6f4fa"
    DIM = "#8fa9b5"
    ACC = "#ffcc33"
    BTN = ("QPushButton {{ background: transparent; color: {c}; border: 1px solid {b};"
           " border-radius: 3px; padding: 1px 8px; }}"
           " QPushButton:hover {{ background: rgba(255,255,255,30); }}"
           " QPushButton:disabled {{ color: #44606c; border-color: rgba(255,255,255,25); }}")

    class _Header(QWidget):
        def __init__(self, panel):
            super().__init__(panel)
            self._panel, self._off = panel, None
            self.setCursor(Qt.CursorShape.SizeAllCursor)

        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._off = ev.globalPosition().toPoint() - self._panel.frameGeometry().topLeft()
                ev.accept()

        def mouseMoveEvent(self, ev):
            if self._off is not None:
                self._panel.move(ev.globalPosition().toPoint() - self._off)
                ev.accept()

        def mouseReleaseEvent(self, ev):
            self._off = None

    class ResearchPanel(QWidget):
        def __init__(self):
            super().__init__(None)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint
                                | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowTitle("Kaka Deep Research")
            root = QVBoxLayout(self)
            root.setContentsMargins(14, 8, 12, 12)
            root.setSpacing(6)

            self.header = _Header(self)
            hl = QHBoxLayout(self.header)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            self.title = QLabel("DEEP RESEARCH")
            self.title.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
            self.title.setStyleSheet(f"color: {ACC}; background: transparent;")
            self.topic_lbl = QLabel("")
            self.topic_lbl.setFont(QFont("Segoe UI", 9))
            self.topic_lbl.setStyleSheet(f"color: {TXT}; background: transparent;")
            self.pdf_btn = QPushButton("PDF")
            self.dir_btn = QPushButton("Folder")
            self.hide_btn = QPushButton("-")
            for b in (self.pdf_btn, self.dir_btn, self.hide_btn):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setFixedHeight(22)
                b.setStyleSheet(BTN.format(c=TXT, b="rgba(255,255,255,70)"))
            self.hide_btn.setFixedWidth(26)
            self.pdf_btn.setToolTip("Report ka PDF kholo")
            self.dir_btn.setToolTip("Data\\Research folder kholo")
            self.hide_btn.setToolTip("Chhupao ('research report dikhao' se wapas)")
            self.pdf_btn.clicked.connect(self._open_pdf)
            self.dir_btn.clicked.connect(self._open_dir)
            self.hide_btn.clicked.connect(self.hide)
            hl.addWidget(self.title)
            hl.addWidget(self.topic_lbl, 1)
            hl.addWidget(self.pdf_btn)
            hl.addWidget(self.dir_btn)
            hl.addWidget(self.hide_btn)
            root.addWidget(self.header)

            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setFont(QFont("Segoe UI", 9))
            self.status.setStyleSheet(f"color: {ACC}; background: transparent;")
            root.addWidget(self.status)
            self.bar = QProgressBar()
            self.bar.setTextVisible(False)
            self.bar.setFixedHeight(4)
            self.bar.setStyleSheet(
                "QProgressBar { background: rgba(255,255,255,25); border: none; border-radius: 2px; }"
                " QProgressBar::chunk { background: #a50044; border-radius: 2px; }")
            root.addWidget(self.bar)

            self.view = QTextBrowser(self)
            self.view.setOpenExternalLinks(True)
            self.view.setOpenLinks(True)
            self.view.setFrameShape(QFrame.Shape.NoFrame)
            self.view.setFont(QFont("Segoe UI", 10))
            self.view.setStyleSheet(
                f"QTextBrowser {{ background: transparent; color: {TXT}; selection-background-color: #a50044; }}"
                " QScrollBar:vertical { width: 8px; background: transparent; }"
                " QScrollBar::handle:vertical { background: rgba(255,255,255,70); border-radius: 4px; min-height: 30px; }"
                " QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }")
            self.view.document().setDefaultStyleSheet(
                "h1 { font-size: 17pt; } h2 { font-size: 13pt; } h3 { font-size: 11pt; }"
                " a { text-decoration: none; }")
            root.addWidget(self.view, 1)
            self._pdf, self._md = "", ""
            self._update_buttons()

        def paintEvent(self, ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
            p.fillPath(path, BG)
            p.setPen(QPen(EDGE, 1.2))
            p.drawPath(path)
            p.end()

        def place(self):
            try:
                from PyQt6.QtGui import QGuiApplication
                scr = QGuiApplication.primaryScreen()
                g = scr.availableGeometry()
                x, y, w, h = geometry_for(g.left(), g.top(), g.width(), g.height())
                self.resize(w, h)
                self.move(QPoint(x, y))
            except Exception:
                self.resize(PANEL_W, 760)

        def _update_buttons(self):
            self.pdf_btn.setEnabled(bool(self._pdf and os.path.exists(self._pdf)))
            self.dir_btn.setEnabled(bool(self._md or self._pdf))

        def _open_pdf(self):
            open_path(self._pdf)

        def _open_dir(self):
            open_folder(self._pdf or self._md)

        def set_progress(self, st: dict):
            qs = st.get("questions") or []
            done = set(st.get("done") or [])
            self.topic_lbl.setText(str(st.get("topic") or "")[:60])
            stage = st.get("stage")
            if stage == "plan" and not qs:
                self.status.setText("Topic ko sub-sawalon mein tod raha hoon...")
                self.bar.setRange(0, 0)
            elif stage == "gather" or (stage == "plan" and qs):
                n = len(done)
                cur = min(n + 1, len(qs)) if qs else 0
                self.status.setText(f"Sub-question {cur}/{len(qs)} - pages padh raha hoon"
                                    + (f" ({st.get('sources', 0)} sources)" if st.get("sources") else ""))
                self.bar.setRange(0, max(1, len(qs) + 1))
                self.bar.setValue(n)
            elif stage == "synth":
                self.status.setText(st.get("msg") or "Report likh raha hoon...")
                self.bar.setRange(0, max(1, len(qs) + 1))
                self.bar.setValue(len(qs))
            if qs:
                rows = "".join(
                    f'<p style="margin:3px 0;"><span style="color:{"#7fd18b" if i in done else DIM};">'
                    f'{"&#10003;" if i in done else "&#8230;"}</span>&nbsp; {i + 1}. '
                    f'{_esc(q)}</p>' for i, q in enumerate(qs))
                self.view.setHtml(f'<div style="color:{TXT};"><h2 style="color:{ACC};">Plan</h2>{rows}'
                                  f'<p style="color:{DIM};">Har sawal ke liye search + asli pages + '
                                  f'Wikipedia padh raha hoon. Report thodi der mein yahin khulegi.</p></div>')

        def set_report(self, rep: dict, paths: Optional[dict]):
            import kaka_research as R
            self.topic_lbl.setText(str(rep.get("topic") or "")[:60])
            paths = paths or {}
            self._pdf, self._md = paths.get("pdf") or "", paths.get("md") or ""
            saved = paths.get("pdf") or paths.get("md") or ""
            n = len(rep.get("sources") or [])
            self.status.setText(f"Ho gaya - {n} sources." + (f"  Saved: {saved}" if saved else ""))
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.view.setHtml(R.to_html(rep, dark=True))
            if rep.get("deeper"):
                self.view.scrollToAnchor("deeper")     # the newest deeper section
            self._update_buttons()

        def set_error(self, msg: str):
            self.status.setText(msg)
            self.bar.setRange(0, 1)
            self.bar.setValue(0)

        def show_quiet(self):
            if not self.isVisible():
                self.show()
            self.raise_()

    _CLS["panel"] = ResearchPanel
    return ResearchPanel


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ensure():
    if _ST["panel"] is not None:
        return _ST["panel"]
    if _qt_app() is None:
        return None
    try:
        w = _panel_class()()
        w.place()
        _ST["panel"] = w
        return w
    except Exception as e:
        _log(f"SYS: Research panel ban nahi paya: {e}")
        return None


# ------------------------------------------------------------------ public API (any thread)
def progress(state: dict) -> None:
    def _go():
        w = _ensure()
        if w is None:
            return
        w.set_progress(state)
        if not _in_game():
            w.show_quiet()
    _in_ui(_go)


def show_report(rep: dict, paths: Optional[dict] = None) -> bool:
    _ST["rep"], _ST["paths"] = rep, paths

    def _go():
        w = _ensure()
        if w is None:
            return
        w.set_report(rep, paths)
        if not _in_game():
            w.show_quiet()
    return _in_ui(_go)


def show_error(msg: str) -> None:
    def _go():
        w = _ensure()
        if w is not None:
            w.set_error(msg)
            if not _in_game():
                w.show_quiet()
    _in_ui(_go)


def show_last() -> bool:
    if _ST["rep"] is None:
        try:
            import kaka_research as R
            rep = R.last()
        except Exception:
            rep = None
        if not rep:
            return False
        _ST["rep"], _ST["paths"] = rep, rep.get("paths")
    return show_report(_ST["rep"], _ST["paths"])


def hide() -> None:
    def _go():
        w = _ST["panel"]
        if w is not None:
            w.hide()
    _in_ui(_go)


def widget():
    """Qt thread only (tests): the panel widget, created if needed."""
    return _ensure()
