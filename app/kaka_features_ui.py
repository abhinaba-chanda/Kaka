"""KAKA v1.1: Settings > Customise > "v1.1 FEATURES" window.

One small dialog for the new switches, so the existing panels stay as they
were: personality mode, wake word, auto-update (+ check now), morning brief,
conversation memory (view / clear), diary (view). Everything is saved through
kaka_user.save_setting / the feature's own module; any failure only shows a
message in the dialog - it never breaks Kaka.
"""
from __future__ import annotations

import threading


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def open_dialog(window, C=None):
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                                 QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

    pri = getattr(C, "PRI", "#00d4ff")
    text = getattr(C, "TEXT", "#e0f7ff")
    dim = getattr(C, "TEXT_DIM", "#6b8a99")
    border = getattr(C, "BORDER", "#123040")

    dlg = QDialog(window)
    dlg.setWindowTitle("KAKA v1.1 FEATURES")
    dlg.setModal(False)
    dlg.setMinimumWidth(430)
    dlg.setStyleSheet(f"""
        QDialog {{ background: #05080b; }}
        QLabel {{ color: {text}; background: transparent; }}
        QCheckBox {{ color: {text}; spacing: 8px; }}
        QComboBox, QPlainTextEdit {{ background: #0a0a0a; color: {text};
            border: 1px solid {border}; border-radius: 3px; padding: 4px; }}
        QPushButton {{ background: transparent; color: {pri}; border: 1px solid {border};
            border-radius: 3px; padding: 6px 10px; }}
        QPushButton:hover {{ border: 1px solid {pri}; }}
    """)
    f9 = QFont("Courier New", 9)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(8)

    def lbl(t, fs=9, bold=False, color=None):
        w = QLabel(t)
        w.setFont(QFont("Courier New", fs, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        if color:
            w.setStyleSheet(f"color: {color};")
        w.setWordWrap(True)
        return w

    try:
        import kaka_v11
        ver = kaka_v11.version()
    except Exception:
        ver = "1.1"
    lay.addWidget(lbl(f"KAKA v{ver} - NEW FEATURES", 11, True, pri))
    status = lbl("", 8, False, dim)

    def say(msg):
        status.setText(msg)

    def rebuild():
        cb = getattr(window, "on_voice_change", None)
        if cb:
            try:
                cb()
            except Exception as e:
                say(f"Session rebuild failed: {e}")

    # -- personality
    lay.addWidget(lbl("PERSONALITY MODE  (voice: 'funny mode', 'bhai mode', 'strict teacher mode')", 8, False, dim))
    combo = QComboBox()
    combo.setFont(f9)
    try:
        import kaka_persona as P
        keys = [k for k, _l in P.labels()]
        for k, l in P.labels():
            combo.addItem(l, k)
        combo.setCurrentIndex(max(0, keys.index(P.current())))
    except Exception as e:
        combo.addItem(f"(unavailable: {e})", "")
    lay.addWidget(combo)

    def on_persona(_i):
        key = combo.currentData()
        if not key:
            return
        try:
            import kaka_persona as P
            ok, k, voice = P.set_persona(key)
            say(f"Mode: {P.PERSONAS[k]['label']}" + (f" (voice {voice})" if voice else "")
                if ok else "Mode save nahi hua.")
            rebuild()
        except Exception as e:
            say(f"Mode error: {e}")
    combo.currentIndexChanged.connect(on_persona)

    # -- switches
    def get_setting(key, default):
        return _safe(lambda: __import__("kaka_user").get_setting(key, default), default)

    def put_setting(key, value):
        return _safe(lambda: __import__("kaka_user").save_setting(key, value), False)

    wake = QCheckBox("Wake word 'Hey Kaka' (sleeps after 45 s of silence)")
    wake.setFont(f9)
    st = _safe(lambda: window.wake_get_state(), None)
    if st is None:
        from memory.config_manager import get_wake_word_enabled
        wake.setChecked(bool(_safe(get_wake_word_enabled, False)))
    else:
        wake.setChecked(bool(st.get("enabled")))
    lay.addWidget(wake)

    def on_wake(on):
        cb = getattr(window, "on_wake_toggle", None)
        if cb:
            r = _safe(lambda: cb(bool(on)), "error")
            if r and "not_ready" in str(r):
                say("Wake model install nahi hai - CONTROLS > WAKE WORD se install karo, ya Kaka-Features.bat chalao.")
                wake.blockSignals(True)
                wake.setChecked(False)
                wake.blockSignals(False)
            else:
                say("Wake word " + ("ON" if on else "OFF"))
        else:
            from memory.config_manager import save_wake_word_enabled
            _safe(lambda: save_wake_word_enabled(bool(on)))
            say("Wake word " + ("ON" if on else "OFF") + " (restart Kaka)")
    wake.toggled.connect(on_wake)

    au_row = QHBoxLayout()
    au = QCheckBox("Auto-update (checks GitHub once a day, ASKS first)")
    au.setFont(f9)
    au.setChecked(bool(get_setting("auto_update_enabled", True)))
    au.toggled.connect(lambda on: (put_setting("auto_update_enabled", bool(on)),
                                   say("Auto-update " + ("ON" if on else "OFF"))))
    au_row.addWidget(au)
    chk = QPushButton("CHECK NOW")
    chk.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
    au_row.addWidget(chk)
    lay.addLayout(au_row)

    def do_check():
        say("GitHub check ho raha hai...")
        box = {}

        def work():
            try:
                import kaka_selfupdate as U
                box["r"] = U.check(force=True)
            except Exception as e:
                box["r"] = {"status": "error", "local": "?", "remote": str(e)}

        def done():
            if th.is_alive():
                QTimer.singleShot(300, done)
                return
            r = box.get("r") or {}
            if r.get("status") == "newer":
                say(f"Naya version v{r['remote']} hai - HUD pe CONFIRM dabao to update ho jayega.")
                _safe(lambda: __import__("kaka_selfupdate").ask_banner(r["remote"]))
            elif r.get("status") == "current":
                say(f"Kaka latest hai (v{r['local']}).")
            else:
                say("GitHub tak nahi pahunch paya.")
        th = threading.Thread(target=work, daemon=True)
        th.start()
        QTimer.singleShot(300, done)
    chk.clicked.connect(do_check)

    mb = QCheckBox("Morning brief (first start of the day: weather, news, reminders, notes)")
    mb.setFont(f9)
    mb.setChecked(bool(_safe(lambda: __import__("core.kaka_feeds", fromlist=["x"]).is_enabled(), True)))
    mb.toggled.connect(lambda on: (_safe(lambda: __import__("core.kaka_feeds", fromlist=["x"]).set_enabled(bool(on))),
                                   say("Morning brief " + ("ON" if on else "OFF"))))
    lay.addWidget(mb)

    dl = QCheckBox("Remember our conversations (daily summary, 'kal kya bola tha')")
    dl.setFont(f9)
    dl.setChecked(bool(get_setting("daylog_enabled", True)))
    dl.toggled.connect(lambda on: (put_setting("daylog_enabled", bool(on)),
                                   say("Conversation memory " + ("ON" if on else "OFF"))))
    lay.addWidget(dl)

    # -- memory / diary viewers
    lay.addWidget(lbl("MEMORY  (stays on this PC only)", 8, False, dim))
    row = QHBoxLayout()
    b_view = QPushButton("VIEW MEMORY")
    b_clear = QPushButton("CLEAR MEMORY")
    b_diary = QPushButton("VIEW DIARY")
    for b in (b_view, b_clear, b_diary):
        b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        row.addWidget(b)
    lay.addLayout(row)

    def show_text(title, body):
        d = QDialog(dlg)
        d.setWindowTitle(title)
        d.resize(520, 460)
        d.setStyleSheet(dlg.styleSheet())
        v = QVBoxLayout(d)
        t = QPlainTextEdit(body or "(khaali)")
        t.setReadOnly(True)
        t.setFont(f9)
        v.addWidget(t)
        c = QPushButton("CLOSE")
        c.clicked.connect(d.close)
        v.addWidget(c)
        d.show()

    def view_memory():
        parts = []
        try:
            from core import kaka_facts as F
            lines = F.summary_lines()
            parts.append("BAATEIN JO YAAD HAIN (" + str(len(lines)) + "):\n" +
                         "\n".join("- " + l for l in lines))
        except Exception as e:
            parts.append(f"(facts unavailable: {e})")
        try:
            import kaka_daylog as L
            s = L.load_summaries()
            parts.append("ROZ KI BAATON KA SUMMARY:\n" + "\n".join(
                f"- {k}: {s[k].get('summary', '')}" for k in sorted(s, reverse=True)[:60]))
        except Exception as e:
            parts.append(f"(day summaries unavailable: {e})")
        show_text("Kaka memory", "\n\n".join(parts))

    def clear_memory():
        r = QMessageBox.question(dlg, "Clear memory",
                                 "Kaka ki saari yaad (personal baatein + roz ki baaton ka summary) "
                                 "delete kar doon? Ye wapas nahi aayegi.\nDiary aur notes safe rahenge.")
        if r != QMessageBox.StandardButton.Yes:
            say("Kuch delete nahi kiya.")
            return
        n = 0
        try:
            from core import kaka_facts as F
            F._save([])
            n += 1
        except Exception:
            pass
        try:
            import kaka_daylog as L
            L.clear_all()
            n += 1
        except Exception:
            pass
        say("Memory clear ho gayi." if n else "Clear nahi ho payi.")
        rebuild()

    def view_diary():
        try:
            import kaka_diary as D
            items = D.load()[-80:]
            show_text("Kaka diary", "\n".join(D.fmt(e) for e in reversed(items)))
        except Exception as e:
            show_text("Kaka diary", f"(unavailable: {e})")
    b_view.clicked.connect(view_memory)
    b_clear.clicked.connect(clear_memory)
    b_diary.clicked.connect(view_diary)

    lay.addWidget(status)
    close = QPushButton("CLOSE")
    close.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
    close.clicked.connect(dlg.close)
    lay.addWidget(close)
    dlg.show()
    dlg.raise_()
    window._kaka_v11_dialog = dlg            # keep a reference
    return dlg
