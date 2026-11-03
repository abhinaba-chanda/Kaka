"""KAKA v1.1: the on-screen Activity report (Screen Activity Tracker).

A small dark panel in Kaka's HUD style: total active time, the top apps /
sites as bars, and for the week a bar per day. Its own top-level window,
draggable, never steals focus, closes with X / Esc or by itself after 2 min.
Sized for 1920x1080 at 125 % (logical 1536 x 864): 600 x <= 640.
"""
from __future__ import annotations

import datetime as _dt

PANEL_W = 600
ROW_H = 30
MAX_ROWS = 10
AUTO_CLOSE_MS = 120_000
_ST = {"panel": None}

PRI = "#00d4ff"
TEXT = "#e0f7ff"
DIM = "#6b8a99"
BORDER = "#123040"
BG = "#05080b"
TRACK = "#141b20"


def _colors():
    """Follow the HUD's live colours (ui.C, recoloured by the theme)."""
    global PRI, TEXT, DIM, BORDER
    try:
        import sys
        ui = sys.modules.get("ui")
        if ui is None:
            return
        C = ui.C
        PRI = getattr(C, "PRI", PRI)
        TEXT = getattr(C, "TEXT", TEXT)
        DIM = getattr(C, "TEXT_DIM", DIM)
        BORDER = getattr(C, "BORDER", BORDER)
    except Exception:
        pass


def _title(rep: dict) -> str:
    k = rep.get("kind")
    try:
        if k == "week":
            a = _dt.date.fromisoformat(rep["from"]).strftime("%d %b")
            b = _dt.date.fromisoformat(rep["to"]).strftime("%d %b")
            return f"ACTIVITY - IS HAFTE  ({a} - {b})"
        d = _dt.date.fromisoformat(rep["to"]).strftime("%d %b %Y")
        return ("ACTIVITY - KAL  (" if k == "yesterday" else "ACTIVITY - AAJ  (") + d + ")"
    except Exception:
        return "ACTIVITY"


def build(rep: dict, parent=None):
    """The panel widget for this report (not shown). Qt thread only."""
    from PyQt6.QtCore import Qt, QRectF, QTimer, QPoint
    from PyQt6.QtGui import QColor, QFont, QPainter, QPen
    from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
    import kaka_activity as A
    _colors()

    items = list(rep.get("items") or [])[:MAX_ROWS]
    total = float(rep.get("total_s") or 0.0)
    per_day = list(rep.get("per_day") or [])

    class Bars(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(max(1, len(items)) * ROW_H + 6)

        def paintEvent(self, _e):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w = self.width()
            name_w, time_w = 150, 92
            bar_x, bar_w = name_w + 8, max(40, w - name_w - time_w - 16)
            top = max((v for _k, v in items), default=1.0) or 1.0
            if not items:
                p.setPen(QColor(DIM))
                p.setFont(QFont("Courier New", 9))
                p.drawText(QRectF(0, 0, w, ROW_H), Qt.AlignmentFlag.AlignVCenter,
                           "Abhi koi data nahi - thodi der baad dekho.")
                return
            for i, (k, v) in enumerate(items):
                y = i * ROW_H + 3
                p.setPen(QColor(TEXT))
                p.setFont(QFont("Courier New", 9, QFont.Weight.Bold if i == 0 else QFont.Weight.Normal))
                name = k if len(k) <= 18 else k[:17] + "."
                p.drawText(QRectF(0, y, name_w, ROW_H - 6), Qt.AlignmentFlag.AlignVCenter, name)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(TRACK))
                p.drawRoundedRect(QRectF(bar_x, y + 5, bar_w, ROW_H - 16), 3, 3)
                col = QColor(PRI)
                col.setAlpha(max(70, 255 - i * 20))
                p.setBrush(col)
                frac = max(0.02, float(v) / top)
                p.drawRoundedRect(QRectF(bar_x, y + 5, bar_w * frac, ROW_H - 16), 3, 3)
                p.setPen(QColor(PRI if i == 0 else TEXT))
                p.setFont(QFont("Courier New", 9))
                pct = f"{int(round(100 * float(v) / total))}%" if total else ""
                p.drawText(QRectF(w - time_w, y, time_w, ROW_H - 6),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           f"{A.fmt_short(v)} {pct:>4}")
            p.end()

    class Days(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(118)

        def paintEvent(self, _e):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            n = max(1, len(per_day))
            w, h = self.width(), self.height()
            col = w / n
            top = max((v for _d, v in per_day), default=1.0) or 1.0
            for i, (day, v) in enumerate(per_day):
                x = i * col
                bh = (h - 42) * (float(v) / top) if v else 0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(TRACK))
                p.drawRoundedRect(QRectF(x + col * 0.22, 16, col * 0.56, h - 42), 3, 3)
                p.setBrush(QColor(PRI))
                if bh:
                    p.drawRoundedRect(QRectF(x + col * 0.22, 16 + (h - 42) - bh, col * 0.56, bh), 3, 3)
                p.setPen(QColor(TEXT))
                p.setFont(QFont("Courier New", 8))
                p.drawText(QRectF(x, 0, col, 15), Qt.AlignmentFlag.AlignCenter,
                           A.fmt_short(v) if v else "-")
                p.setPen(QColor(DIM))
                try:
                    lab = _dt.date.fromisoformat(day).strftime("%a")
                except Exception:
                    lab = day[-2:]
                p.drawText(QRectF(x, h - 22, col, 20), Qt.AlignmentFlag.AlignCenter, lab)
            p.end()

    class Panel(QFrame):
        def __init__(self):
            super().__init__(parent)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                                Qt.WindowType.WindowStaysOnTopHint |
                                Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.setObjectName("kakaActivity")
            self.setStyleSheet(f"""
                QFrame#kakaActivity {{ background: {BG}; border: 1px solid {PRI};
                    border-radius: 6px; }}
                QLabel {{ color: {TEXT}; background: transparent; border: none; }}
                QPushButton {{ background: transparent; color: {PRI}; border: 1px solid {BORDER};
                    border-radius: 3px; padding: 2px 8px; }}
                QPushButton:hover {{ border: 1px solid {PRI}; }}
            """)
            self.setFixedWidth(PANEL_W)
            self._drag = None
            v = QVBoxLayout(self)
            v.setContentsMargins(18, 12, 18, 14)
            v.setSpacing(6)
            head = QHBoxLayout()
            t = QLabel(_title(rep))
            t.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            t.setStyleSheet(f"color: {PRI};")
            head.addWidget(t)
            head.addStretch(1)
            x = QPushButton("X")
            x.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            x.setFixedWidth(30)
            x.clicked.connect(self.close)
            head.addWidget(x)
            v.addLayout(head)
            sub = QLabel(f"TOTAL ACTIVE  {A.fmt_short(total)}" +
                         (f"     APPS / SITES  {len(rep.get('items') or [])}" if items else ""))
            sub.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            v.addWidget(sub)
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {BORDER}; border: none;")
            v.addWidget(line)
            v.addWidget(Bars())
            if rep.get("kind") == "week" and per_day:
                d = QLabel("ROZ KA ACTIVE TIME")
                d.setFont(QFont("Courier New", 8))
                d.setStyleSheet(f"color: {DIM};")
                v.addWidget(d)
                v.addWidget(Days())
            foot = QLabel("Sirf is PC pe save hota hai - koi screenshot nahi.  "
                          "'tracker band karo' se off.")
            foot.setFont(QFont("Courier New", 8))
            foot.setStyleSheet(f"color: {DIM};")
            foot.setWordWrap(True)
            v.addWidget(foot)
            self.adjustSize()
            QTimer.singleShot(AUTO_CLOSE_MS, self.close)

        def keyPressEvent(self, e):
            if e.key() == Qt.Key.Key_Escape:
                self.close()

        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e):
            if self._drag is not None:
                self.move(e.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, _e):
            self._drag = None

    return Panel()


def _place(w) -> None:
    try:
        from PyQt6.QtWidgets import QApplication
        g = QApplication.primaryScreen().availableGeometry()
        x = g.left() + (g.width() - w.width()) // 2
        y = g.top() + max(20, (g.height() - w.height()) // 2)
        w.move(x, y)
    except Exception:
        pass


def show(rep: dict) -> bool:
    """Show (or replace) the panel. Safe from any thread."""
    def _do():
        try:
            old = _ST.get("panel")
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            w = build(rep)
            _place(w)
            w.show()
            w.raise_()
            _ST["panel"] = w
        except Exception as e:
            print(f"[activity] panel failed: {e}")
    try:
        from core import kaka_services as ks
        return bool(ks.run_in_ui(_do))
    except Exception:
        return False
