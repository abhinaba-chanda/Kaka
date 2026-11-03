"""
KAKA theme — Iron Man / J.A.R.V.I.S. style layer for the Kaka HUD.

Added for Abhinaba (Nov 2026). Purely cosmetic and fail-safe: every function
here is called inside try/except by ui.py, so a drawing error can never take
the assistant down. Delete this file and the HUD simply falls back to
the plain look.

  paint_reactor(p, cx, cy, r, amp, t, speaking, state)  — arc-reactor glow behind the face
  paint_overlay(p, W, H, snap, state, speaking)         — gold corner frame, clock,
                                                          CPU / RAM / battery gauges
  style_title(label) / style_clock(label)               — gold glowing header text
  battery()                                              — (percent, plugged) or None, cached
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainterPath, QPen,
                         QRadialGradient)

GOLD = "#ffb300"
GOLD_LT = "#ffd54f"
BLUE = "#00d4ff"
BLUE_DIM = "#0a6f8f"
WHITE = "#e8fbff"
RED = "#ff4060"

_fonts: dict = {}


def _font(size: int, bold: bool = True, family: str = "Consolas") -> QFont:
    key = (size, bold, family)
    f = _fonts.get(key)
    if f is None:
        f = QFont(family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        f.setStyleHint(QFont.StyleHint.Monospace)
        _fonts[key] = f
    return f


def _c(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(max(0, min(255, int(a))))
    return c


# ── battery, cached (psutil call is cheap but not free) ──────────────────────
_bat_cache = {"t": 0.0, "v": None}


def battery():
    now = time.monotonic()
    if now - _bat_cache["t"] > 10.0:
        _bat_cache["t"] = now
        try:
            import psutil
            b = psutil.sensors_battery()
            _bat_cache["v"] = (float(b.percent), bool(b.power_plugged)) if b else None
        except Exception:
            _bat_cache["v"] = None
    return _bat_cache["v"]


# ── header text ──────────────────────────────────────────────────────────────
def style_title(label) -> None:
    try:
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        f = _font(20, True, "Consolas")
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        label.setFont(f)
        label.setStyleSheet(f"color: {GOLD}; background: transparent;")
        glow = QGraphicsDropShadowEffect(label)
        glow.setBlurRadius(22)
        glow.setOffset(0, 0)
        glow.setColor(_c(GOLD, 200))
        label.setGraphicsEffect(glow)
    except Exception:
        pass


def style_clock(label) -> None:
    try:
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        label.setStyleSheet(f"color: {GOLD_LT}; background: transparent;")
        glow = QGraphicsDropShadowEffect(label)
        glow.setBlurRadius(14)
        glow.setOffset(0, 0)
        glow.setColor(_c(GOLD, 160))
        label.setGraphicsEffect(glow)
    except Exception:
        pass


# ── the arc reactor behind the face ─────────────────────────────────────────
def paint_reactor(p, cx: float, cy: float, r: float, amp: float = 0.0,
                  t: float | None = None, speaking: bool = False,
                  state: str = "", muted: bool = False) -> None:
    if r < 20:
        return
    t = time.time() if t is None else t
    busy = state in ("THINKING", "PROCESSING")
    spin = 1.0 + (1.8 if busy else 0.0) + (1.0 if speaking else 0.0)
    main = _c(RED if muted else BLUE)
    gold = _c(RED if muted else GOLD)
    pulse = 0.5 + 0.5 * math.sin(t * 2.0)
    lift = min(1.0, 0.35 + 0.25 * pulse + 0.9 * amp + (0.2 if speaking else 0.0))

    p.save()
    p.setRenderHint(p.RenderHint.Antialiasing)

    # 1. soft blue-white glow
    g = QRadialGradient(QPointF(cx, cy), r * 1.25)
    g.setColorAt(0.00, _c(WHITE if not muted else RED, int(70 * lift)))
    g.setColorAt(0.25, _c(BLUE if not muted else RED, int(55 * lift)))
    g.setColorAt(0.60, _c(BLUE if not muted else RED, int(18 * lift)))
    g.setColorAt(1.00, _c(BLUE, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QPointF(cx, cy), r * 1.25, r * 1.25)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # 2. outer thin ring + inner ring
    for rr, a, w in ((1.10, 110, 1.2), (1.03, 60, 1.0), (0.70, 45, 1.0)):
        p.setPen(QPen(_c(main.name(), a), w))
        p.drawEllipse(QPointF(cx, cy), r * rr, r * rr)

    # 3. gold coil segments (the reactor's ring of ten blocks)
    n = 10
    ri, ro = r * 0.955, r * 1.00
    p.setPen(Qt.PenStyle.NoPen)
    rot = (t * 6.0 * spin) % 360.0
    for i in range(n):
        a0 = math.radians(rot + i * 36.0 + 4.0)
        a1 = math.radians(rot + i * 36.0 + 30.0)
        path = QPainterPath()
        path.moveTo(cx + math.cos(a0) * ri, cy + math.sin(a0) * ri)
        path.lineTo(cx + math.cos(a0) * ro, cy + math.sin(a0) * ro)
        path.lineTo(cx + math.cos(a1) * ro, cy + math.sin(a1) * ro)
        path.lineTo(cx + math.cos(a1) * ri, cy + math.sin(a1) * ri)
        path.closeSubpath()
        p.setBrush(_c(gold.name(), int(40 + 60 * lift)))
        p.drawPath(path)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # 4. two counter-rotating gold arcs outside
    box = QRectF(cx - r * 1.17, cy - r * 1.17, r * 2.34, r * 2.34)
    p.setPen(QPen(_c(GOLD_LT if not muted else RED, int(120 + 100 * lift)), 2.2,
                  cap=Qt.PenCapStyle.RoundCap))
    base = (-t * 22.0 * spin) % 360.0
    for k in range(3):
        p.drawArc(box, int((base + k * 120) * 16), int(48 * 16))
    box2 = QRectF(cx - r * 1.24, cy - r * 1.24, r * 2.48, r * 2.48)
    p.setPen(QPen(_c(main.name(), 120), 1.4))
    base2 = (t * 14.0 * spin) % 360.0
    for k in range(2):
        p.drawArc(box2, int((base2 + k * 180) * 16), int(70 * 16))

    # 5. tick ring
    ticks = 72
    p.setPen(QPen(_c(main.name(), 70), 1))
    for i in range(ticks):
        a = math.radians(i * 5.0)
        l0 = r * (1.30 if i % 6 == 0 else 1.33)
        l1 = r * 1.36
        p.drawLine(QPointF(cx + math.cos(a) * l0, cy + math.sin(a) * l0),
                   QPointF(cx + math.cos(a) * l1, cy + math.sin(a) * l1))
    p.restore()


# ── frame, clock and gauges over the whole canvas ───────────────────────────
def _gauge(p, cx, cy, rad, value, label, text=None, warn=85.0):
    v = max(0.0, min(100.0, float(value)))
    col = RED if v >= warn else GOLD
    p.setPen(QPen(_c(BLUE_DIM, 160), 3))
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    p.setPen(QPen(_c(col, 235), 4, cap=Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(cx - rad, cy - rad, rad * 2, rad * 2), 90 * 16, int(-v * 3.6 * 16))
    p.setPen(QPen(_c(BLUE, 90), 1))
    p.drawEllipse(QPointF(cx, cy), rad * 0.74, rad * 0.74)
    p.setPen(_c(WHITE, 240))
    p.setFont(_font(max(8, int(rad * 0.36))))
    p.drawText(QRectF(cx - rad, cy - rad * 0.5, rad * 2, rad), Qt.AlignmentFlag.AlignCenter,
               text if text is not None else f"{v:.0f}%")
    p.setPen(_c(GOLD_LT, 220))
    p.setFont(_font(max(7, int(rad * 0.26))))
    p.drawText(QRectF(cx - rad * 1.6, cy + rad + 2, rad * 3.2, rad * 0.6),
               Qt.AlignmentFlag.AlignCenter, label)


def paint_overlay(p, W: int, H: int, snap: dict | None = None, state: str = "",
                  speaking: bool = False) -> None:
    if W < 260 or H < 260:
        return
    p.save()
    p.setRenderHint(p.RenderHint.Antialiasing)

    # gold corner brackets
    m, arm = 10.0, min(W, H) * 0.07
    p.setPen(QPen(_c(GOLD, 200), 2.0))
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = m if sx > 0 else W - m
        y = m if sy > 0 else H - m
        p.drawLine(QPointF(x, y), QPointF(x + sx * arm, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * arm))
        p.drawLine(QPointF(x + sx * 5, y + sy * 5), QPointF(x + sx * arm * 0.45, y + sy * 5))

    # top-left: system name
    p.setPen(_c(GOLD, 230))
    p.setFont(_font(10))
    p.drawText(QRectF(22, 16, 260, 18), Qt.AlignmentFlag.AlignLeft, "KAKA // PERSONAL A.I.")
    p.setPen(_c(BLUE, 170))
    p.setFont(_font(8, False))
    blink = int(time.time() * 2) % 2 == 0
    status = {"THINKING": "PROCESSING REQUEST", "PROCESSING": "PROCESSING REQUEST",
              "LISTENING": "LISTENING", "SLEEPING": "STANDBY - SAY \"KAKA\""}.get(state, "ALL SYSTEMS ONLINE")
    if speaking:
        status = "SPEAKING"
    p.drawText(QRectF(22, 34, 300, 16), Qt.AlignmentFlag.AlignLeft,
               ("> " if blink else "  ") + status)

    # top-right: big clock + date
    now = time.localtime()
    p.setPen(_c(GOLD_LT, 245))
    p.setFont(_font(max(14, min(26, int(W * 0.03)))))
    p.drawText(QRectF(W - 262, 12, 240, 34), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
               time.strftime("%H:%M:%S", now))
    p.setPen(_c(BLUE, 190))
    p.setFont(_font(8, False))
    p.drawText(QRectF(W - 262, 46, 240, 16), Qt.AlignmentFlag.AlignRight,
               time.strftime("%A, %d %B %Y", now).upper())

    # gauges: CPU + RAM bottom-left, BATTERY bottom-right
    snap = snap or {}
    rad = max(22.0, min(40.0, min(W, H) * 0.055))
    gy = H - rad - 34
    try:
        _gauge(p, 26 + rad, gy, rad, snap.get("cpu", 0.0), "CPU")
        # The waveform (288 px) sits centred at the bottom; stack the two
        # left gauges when the canvas is too narrow for them side by side.
        if 26 + rad * 4.5 > (W - 288) / 2 - 6:
            _gauge(p, 26 + rad, gy - rad * 2.75, rad, snap.get("mem", 0.0), "RAM")
        else:
            _gauge(p, 26 + rad * 3.4, gy, rad, snap.get("mem", 0.0), "RAM")
        bat = battery()
        if bat is None:
            _gauge(p, W - 26 - rad, gy, rad, 100.0, "POWER", "AC", warn=101.0)
        else:
            pct, plugged = bat
            _gauge(p, W - 26 - rad, gy, rad, pct, "BATTERY" + (" +" if plugged else ""),
                   warn=101.0)
            if pct <= 20 and not plugged:
                p.setPen(QPen(_c(RED, 240), 2))
                p.drawEllipse(QPointF(W - 26 - rad, gy), rad + 5, rad + 5)
    except Exception:
        pass
    p.restore()


def apply_identity(app) -> None:
    """Kaka icon on the window and taskbar (instead of Python's), and its own
    taskbar group on Windows."""
    try:
        import os
        import sys
        from pathlib import Path
        from PyQt6.QtGui import QIcon
        ico = Path(__file__).resolve().parent / "config" / "kaka.ico"
        if ico.is_file():
            app.setWindowIcon(QIcon(str(ico)))
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kaka.Assistant")
    except Exception:
        pass
