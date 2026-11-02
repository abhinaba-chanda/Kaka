"""
KAKA minimal look - a particle sphere on black (added for Abhinaba, Nov 2026).

"No fancy, cooler minimal design": pure black, faint white dots behind, and a
single 3D sphere made of a few thousand tiny dots - warm amber at the top,
fading to white/silver at the bottom, denser at the poles - slowly turning.

It reacts quietly to what Kaka is doing:
  idle      - slow turn, gentle breathing
  LISTENING - the sphere swells and its dots shimmer with your voice level
  SPEAKING  - ripples run over the surface with Kaka's own voice
  THINKING  - turns faster
  SLEEPING  - dims and slows down

Drawing: the dots are splatted into one image with numpy (no per-dot Qt
calls), so it stays well under 15 ms a frame in software on a laptop.
Every call from ui.py is wrapped in try/except; if anything here fails the
HUD falls back to the reactor core. HUD button (controls drawer) toggles
MINIMAL <-> REACTOR CORE (update 6: the only two looks).
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap

TOP = np.array([255.0, 138.0, 28.0])      # amber
MID = np.array([255.0, 196.0, 140.0])     # peach
BOT = np.array([236.0, 238.0, 245.0])     # white / silver


def _local_flag() -> str:
    """KAKA u4: brain + voice tag, '' for the default AUTO + BRIAN."""
    try:
        import kaka_keys
        return kaka_keys.combo_tag()
    except Exception:
        return ""


class MinimalSphere:
    N = 4200

    def __init__(self, n: int | None = None, seed: int = 7):
        n = int(n or self.N)
        rng = np.random.default_rng(seed)
        # Height biased toward the poles (the reference is dense top & bottom,
        # airy round the middle).
        u = rng.uniform(-1.0, 1.0, n)
        y = np.sign(u) * np.abs(u) ** 0.42
        th = rng.uniform(0, 2 * np.pi, n)
        rr = np.sqrt(np.clip(1.0 - y * y, 0.0, 1.0))
        self.x0, self.y0, self.z0 = rr * np.cos(th), y, rr * np.sin(th)
        self.lat = np.arcsin(np.clip(y, -1, 1))
        self.lon = th
        k = (1.0 - y) / 2.0                      # 0 at top, 1 at bottom
        c = np.where(k[:, None] < 0.5,
                     TOP + (MID - TOP) * (k[:, None] / 0.5),
                     MID + (BOT - MID) * ((k[:, None] - 0.5) / 0.5))
        self.col = c.astype(np.float32)
        self.size = rng.choice([1.0, 1.0, 1.0, 1.6], n).astype(np.float32)
        self.tw = rng.uniform(0, 2 * np.pi, n)   # per-dot twinkle phase
        self.rot = 0.0
        self.t = 0.0
        self.amp = 0.0
        self.swell = 0.0
        self.dim = 1.0
        self.state = ""
        self.speaking = False
        self._bg = None
        self._bg_key = None
        self.captions: list = []                 # [(kind, text, born)]

    # -- state -------------------------------------------------------------------
    def step(self, dt: float, amp: float, state: str = "", speaking: bool = False,
             muted: bool = False) -> None:
        dt = max(0.0, min(0.1, dt))
        self.t += dt
        st = (state or "").upper()
        self.state, self.speaking = st, speaking
        sleeping = muted or st in ("SLEEPING", "MUTED", "STANDBY")
        spin = 0.16
        if st in ("THINKING", "PROCESSING"):
            spin = 0.55
        elif sleeping:
            spin = 0.06
        self.rot += spin * dt
        self.amp += (float(amp or 0.0) - self.amp) * min(1.0, dt * 12)
        want_swell = 0.0
        if st == "LISTENING" and not speaking:
            want_swell = 0.05 + self.amp * 0.16
        self.swell += (want_swell - self.swell) * min(1.0, dt * 6)
        want_dim = 0.38 if sleeping else 1.0
        self.dim += (want_dim - self.dim) * min(1.0, dt * 3)

    def caption(self, kind: str, text: str) -> None:
        text = " ".join(str(text or "").split())
        if not text:
            return
        self.captions = [c for c in self.captions if c[0] != kind][-1:] + [(kind, text, time.monotonic())]

    # -- drawing -----------------------------------------------------------------
    def _background(self, W: int, H: int) -> QPixmap:
        key = (W, H)
        if self._bg is None or self._bg_key != key:
            pm = QPixmap(max(1, W), max(1, H))
            pm.fill(QColor(0, 0, 0))
            q = QPainter(pm)
            rng = np.random.default_rng(3)
            n = int(W * H / 2600)
            xs = rng.uniform(0, W, n)
            ys = rng.uniform(0, H, n)
            al = rng.uniform(18, 70, n)
            for x, y, a in zip(xs, ys, al):
                q.setPen(QPen(QColor(255, 255, 255, int(a)), 1.2 if a > 55 else 1.0))
                q.drawPoint(int(x), int(y))
            q.end()
            self._bg, self._bg_key = pm, key
        return self._bg

    def _sphere_image(self, R: float) -> QImage:
        S = int(R * 2.5) | 1
        c = S / 2.0
        # rotate about the vertical axis, then a small fixed tilt
        a = self.rot
        ca, sa = math.cos(a), math.sin(a)
        x = self.x0 * ca + self.z0 * sa
        z = -self.x0 * sa + self.z0 * ca
        y = self.y0
        tilt = 0.30
        ct, stt = math.cos(tilt), math.sin(tilt)
        y2 = y * ct - z * stt
        z2 = y * stt + z * ct
        # surface motion
        r = 1.0 + 0.012 * math.sin(self.t * 1.3) + self.swell
        if self.speaking:
            r = r + min(1.0, self.amp) * 0.045 * np.sin(self.lat * 5.0 + self.lon * 2.0 - self.t * 7.0)
        elif self.swell > 0.01:
            r = r + self.swell * 0.35 * np.sin(self.tw + self.t * 14.0)
        f = 3.2                                    # perspective
        s = f / (f + z2 * r)
        px = c + x * r * s * R
        py = c - y2 * r * s * R
        depth = (1.0 - z2) / 2.0                   # 1 = front, 0 = back
        b = (0.25 + 0.95 * depth ** 1.3) * self.dim
        b = b * (0.85 + 0.15 * np.sin(self.tw + self.t * 2.0))
        if self.speaking:
            b = b * (1.0 + self.amp * 0.6)
        ix = px.astype(np.int32)
        iy = py.astype(np.int32)
        ok = (ix > 0) & (iy > 0) & (ix < S - 1) & (iy < S - 1)
        ix, iy, b, col, sz = ix[ok], iy[ok], b[ok], self.col[ok], self.size[ok]
        v = col * b[:, None]
        big = sz > 1.2
        wsoft = np.where(big, 0.65, 0.30).astype(np.float32)
        base = iy * S + ix
        idx = np.concatenate([base, base + 1, base - 1, base + S, base - S])
        wts = np.concatenate([np.ones_like(wsoft), wsoft, wsoft, wsoft, wsoft])
        img = np.empty((S * S, 3), np.float32)
        for ch in range(3):
            img[:, ch] = np.bincount(idx, weights=np.tile(v[:, ch], 5) * wts, minlength=S * S)
        img = img.reshape(S, S, 3)
        np.clip(img, 0, 255, out=img)
        out = np.empty((S, S, 4), np.uint8)
        out[..., 0] = img[..., 2]                  # B
        out[..., 1] = img[..., 1]                  # G
        out[..., 2] = img[..., 0]                  # R
        out[..., 3] = img.max(axis=2)              # premultiplied: additive on black
        self._buf = out                            # keep the memory alive for QImage
        return QImage(out.data, S, S, S * 4, QImage.Format.Format_ARGB32_Premultiplied)

    def paint(self, p: QPainter, W: int, H: int, name: str = "KAKA",
              status: str = "") -> None:
        p.drawPixmap(0, 0, self._background(W, H))
        R = max(40.0, min(W, H) * 0.31)
        Ri = min(R, 240.0)                      # big windows: draw at 240 and scale up
        img = self._sphere_image(Ri)
        cx, cy = W / 2.0, H / 2.0 - H * 0.02
        k = R / Ri
        w, h = img.width() * k, img.height() * k
        if k > 1.01:
            p.save()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(QRectF(cx - w / 2, cy - h / 2, w, h), img)
            p.restore()
        else:
            p.drawImage(int(cx - w / 2), int(cy - h / 2), img)
        # tiny wordmark
        f = QFont("Segoe UI", 9)
        f.setWeight(QFont.Weight.Light)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        p.setFont(f)
        p.setPen(QPen(QColor(235, 235, 240, int(150 * max(0.5, self.dim))), 1))
        p.drawText(QRectF(0, 14, W, 20), Qt.AlignmentFlag.AlignHCenter, (name or "KAKA").upper())
        tag = _local_flag()
        if tag:                                 # KAKA u4: small grey LOCAL / voice tag
            from PyQt6.QtGui import QFontMetricsF
            wm = QFontMetricsF(f).horizontalAdvance((name or "KAKA").upper())
            ft = QFont("Segoe UI", 7)
            ft.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
            tw = QFontMetricsF(ft).horizontalAdvance(tag) + 12
            x0 = W / 2.0 + wm / 2.0 + 8
            box = QRectF(x0, 17, tw, 15)
            p.save()
            p.setFont(ft)
            p.setPen(QPen(QColor(150, 150, 150, 150), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QPen(QColor(170, 170, 170, 200), 1))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, tag)
            p.restore()
        if status:
            f2 = QFont("Segoe UI", 8)
            f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
            p.setFont(f2)
            p.setPen(QPen(QColor(255, 170, 90, 120), 1))
            p.drawText(QRectF(0, 34, W, 16), Qt.AlignmentFlag.AlignHCenter, status.upper())
        # captions at the bottom, fading out
        now = time.monotonic()
        live = [(k, t, born) for k, t, born in self.captions if now - born < 9.0]
        self.captions = live
        yb = H - 22
        f3 = QFont("Segoe UI", 10)
        p.setFont(f3)
        for kind, text, born in reversed(live):
            age = now - born
            a = 1.0 if age < 6.0 else max(0.0, 1.0 - (age - 6.0) / 3.0)
            col = QColor(255, 196, 140, int(210 * a)) if kind == "suna" else QColor(236, 238, 245, int(200 * a))
            if len(text) > 110:
                text = text[:107] + "..."
            p.setPen(QPen(col, 1))
            p.drawText(QRectF(20, yb - 18, W - 40, 20), Qt.AlignmentFlag.AlignHCenter, text)
            yb -= 22
