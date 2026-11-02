"""
kaka_keys_ui.py - the "API KEYS" panel in Kaka's settings drawer (KAKA EDITION).

Update 3: the MAIN Gemini key, Groq and two spare slots - each with SAVE / TEST
/ CLEAR and a USE button that makes Kaka ask that key first.
Update 5 (API only): more free providers, each with its own row and a
FREE KEY button that opens the page where the key is made; the list scrolls;
Kaka's voice switch (BRIAN / WINDOWS AWAAZ + TEST) sits at the bottom.
Only the last 4 characters of a key are ever shown.
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

import kaka_keys as K

# Fixed black / white / amber palette: this panel never follows the old cyan
# theme colours, whatever colour was saved.
BG = "rgba(4, 4, 4, 248)"
FIELD = "#0b0b0b"
LINE = "#2a2a2a"
LINE_HI = "#4a4a4a"
TEXT = "#ececec"
MED = "#a8a8a8"
DIM = "#6e6e6e"
AMBER = "#ffb070"
AMBER_GHO = "rgba(255, 176, 112, 30)"
OK = "#8fd694"
ERR = "#ff6b6b"


def row_choice(rid: str) -> str:
    """Panel row id -> kaka_keys.brain_first() value."""
    if rid.startswith("slot") and rid[4:].isdigit():
        return f"slot{int(rid[4:]) + 1}"      # KAKA update 8: any number of slots
    return rid


def make_overlay(base_cls, C=None):
    """Build the panel class on the app's own overlay base."""

    class KeysOverlay(base_cls):
        changed = pyqtSignal(str)              # a line for the activity log
        _tested = pyqtSignal(str, bool, str)   # row, ok, message (from a thread)
        _vtest_sig = pyqtSignal(bool, str)
        _OW = 548

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("KakaKeys")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setStyleSheet(f"""
                #KakaKeys {{ background: {BG}; border: 1px solid {LINE_HI}; border-radius: 6px; }}
                QLineEdit, QComboBox {{ background: {FIELD}; color: {TEXT};
                    border: 1px solid {LINE}; border-radius: 3px; padding: 1px 6px; }}
                QLineEdit:focus, QComboBox:focus {{ border: 1px solid {AMBER}; }}
                QComboBox QAbstractItemView {{ background: {FIELD}; color: {TEXT};
                    selection-background-color: {AMBER_GHO}; selection-color: {TEXT}; }}
                QPushButton {{ background: transparent; color: {TEXT};
                    border: 1px solid {LINE_HI}; border-radius: 3px; padding: 0 7px; }}
                QPushButton:hover {{ background: {AMBER_GHO}; border-color: {AMBER}; }}
                QPushButton:checked {{ color: {AMBER}; border-color: {AMBER}; background: {AMBER_GHO}; }}
                QPushButton:disabled {{ color: {DIM}; border-color: {LINE}; }}
                QScrollArea {{ background: transparent; border: none; }}
                QScrollArea > QWidget > QWidget {{ background: transparent; }}
                QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
                QScrollBar::handle:vertical {{ background: #3a3a3a; border-radius: 3px; min-height: 24px; }}
                QScrollBar::handle:vertical:hover {{ background: {AMBER}; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
                QToolTip {{ background: #111111; color: {TEXT}; border: 1px solid {LINE_HI}; }}
            """)
            self.setFixedWidth(self._OW)
            self._rows: dict[str, dict] = {}
            self._armed_clear = ""
            self._tested.connect(self._show_test)
            self._vtest_sig.connect(self._on_voice_test)

            outer = QVBoxLayout(self)
            outer.setContentsMargins(16, 10, 10, 10)
            outer.setSpacing(4)
            outer.addWidget(self._lbl("\u25c8  API KEYS", 11, True, TEXT))
            outer.addWidget(self._lbl("Free keys - paste, SAVE, TEST. USE = Kaka asks that key "
                                      "first; when its limit is over Kaka moves down the list by "
                                      "itself. Only the last 4 characters are shown.", 7, False, DIM))
            top = QHBoxLayout()
            top.setSpacing(6)
            top.addWidget(self._lbl("KAKA USES FIRST:", 8, True, MED))
            self._first_lbl = self._lbl("", 8, True, AMBER)
            top.addWidget(self._first_lbl, 1)
            self._auto_btn = self._btn("AUTO")
            self._auto_btn.setCheckable(True)
            self._auto_btn.setToolTip("Normal order: Gemini \u2192 Groq \u2192 Mistral \u2192 NVIDIA \u2192 "
                                      "OpenRouter \u2192 Cloudflare \u2192 SambaNova \u2192 Cohere \u2192 more keys")
            self._auto_btn.clicked.connect(lambda _=False: self._use("auto"))
            top.addWidget(self._auto_btn)
            top.addSpacing(6)
            outer.addLayout(top)
            self._sep(outer)

            inner = QWidget()
            inner.setObjectName("KakaKeysInner")
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(0, 2, 8, 2)
            lay.setSpacing(3)
            self._key_row(lay, "gemini", "GEMINI", None, provider="gemini",
                          note="Google - main key")
            self._sep(lay)
            self._key_row(lay, "groq", "GROQ", K.GROQ_VAR, provider="groq",
                          note="very fast")
            for p in K.NAMED:
                self._sep(lay)
                self._key_row(lay, p["id"], p["title"], p["var"], provider=p["id"],
                              note=p.get("note", ""), model=True)
            lay.addSpacing(4)
            lay.addWidget(self._lbl("MORE KEYS  (Gemini 2-5 \u00b7 Groq 2 \u00b7 6 custom: any OpenAI-compatible address)", 7, True, DIM))
            for i, s in enumerate(K.slots()):
                self._sep(lay)
                self._key_row(lay, f"slot{i}", K.slot_title(i, s["provider"]), s["var"], slot=(i, s))
            # KAKA update 20: Discord remote bot token (own user variable,
            # never config/api_keys.json); not a brain, so no USE button.
            lay.addSpacing(4)
            lay.addWidget(self._lbl("DISCORD REMOTE  (phone se Kaka chalao - bot token)", 7, True, DIM))
            self._sep(lay)
            self._key_row(lay, "discord", "DISCORD BOT TOKEN", K.DISCORD_VAR, provider="discord")
            self._rows["discord"]["use"].hide()
            lay.addSpacing(6)
            self._sep(lay)
            self._voice_section(lay)
            lay.addStretch(1)

            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll.setWidget(inner)
            self._inner = inner
            outer.addWidget(self._scroll, 1)
            self._sep(outer)
            close = QPushButton("CLOSE")
            close.setFixedHeight(24)
            close.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.clicked.connect(self.close)
            outer.addWidget(close)
            self._fit_height()
            self._show_first()
            self._voice_refresh()

        def _fit_height(self):
            """As tall as the window allows (the list scrolls inside)."""
            p = self.parentWidget()
            avail = (p.height() - 40) if p is not None and p.height() > 200 else 660
            want = self._inner.sizeHint().height() + 120
            self.setFixedHeight(max(360, min(avail, want, 760)))

        # -- pieces ----------------------------------------------------------
        def _lbl(self, txt, size=8, bold=False, color=None):
            w = QLabel(txt)
            w.setWordWrap(True)
            w.setFont(QFont("Courier New", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color or TEXT}; background: transparent; border: none;")
            return w

        def _sep(self, lay):
            f = QFrame()
            f.setFixedHeight(1)
            f.setStyleSheet(f"background: {LINE}; border: none;")
            lay.addWidget(f)

        def _btn(self, txt, w=None, h=22):
            b = QPushButton(txt)
            b.setFixedHeight(h)
            if w:
                b.setFixedWidth(w)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        def _has(self, row) -> bool:
            return bool(K.gemini_main_key()) if row["var"] is None else bool(K.get_secret(row["var"]))

        def _masked(self, row) -> str:
            m = K.masked_gemini() if row["var"] is None else K.masked(row["var"])
            return m.replace("saved ", "")

        def _key_row(self, lay, rid, title, var, provider="", slot=None, note="", model=False):
            row = {"var": var, "provider": provider, "slot": slot, "rid": rid}
            head = QHBoxLayout()
            head.setSpacing(5)
            dot = self._lbl("\u25cf", 8, True, DIM)
            dot.setFixedWidth(12)
            head.addWidget(dot)
            t = self._lbl(title, 9, True, TEXT)
            t.setWordWrap(False)
            head.addWidget(t)
            if note:
                n = self._lbl(note, 7, False, DIM)
                n.setWordWrap(False)
                head.addWidget(n)
            head.addStretch(1)
            status = self._lbl("", 7, False, DIM)
            status.setWordWrap(False)
            status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(status)
            page = K.key_page(provider) if slot is None else ""
            if page:
                g = self._btn("FREE KEY \u2197", 82)
                g.setToolTip("Opens " + page)
                g.clicked.connect(lambda _=False, u=page: QDesktopServices.openUrl(QUrl(u)))
                head.addWidget(g)
            use = self._btn("USE", 56)
            use.setCheckable(True)
            use.clicked.connect(lambda _=False, r=rid: self._use(row_choice(r)))
            head.addWidget(use)
            lay.addLayout(head)
            row.update(dot=dot, status=status, use=use)
            if slot is not None:
                i, s = slot
                prow = QHBoxLayout()
                prow.setSpacing(5)
                combo = QComboBox()
                for pid in K.PROVIDER_ORDER:
                    combo.addItem(K.PROVIDERS[pid][0], pid)
                combo.setCurrentIndex(K.PROVIDER_ORDER.index(s["provider"])
                                      if s["provider"] in K.PROVIDER_ORDER else len(K.PROVIDER_ORDER) - 1)
                combo.setFixedHeight(22)
                prow.addWidget(combo, 2)
                mdl = QLineEdit(s["model"])
                mdl.setPlaceholderText("model (optional)")
                mdl.setFixedHeight(22)
                prow.addWidget(mdl, 3)
                lay.addLayout(prow)
                base = QLineEdit(s["base_url"])
                base.setPlaceholderText("address, e.g. https://api.example.com/v1")
                base.setFixedHeight(22)
                lay.addWidget(base)
                row.update(combo=combo, model=mdl, base=base)
                combo.currentIndexChanged.connect(lambda _=0, r=row: self._provider_changed(r))
                self._provider_changed(row, fit=False)
            acc_var = K.named(provider).get("account_var") if provider and slot is None else ""
            if acc_var:
                # KAKA update 8: Cloudflare also needs the Account ID
                acc = QLineEdit()
                acc.setPlaceholderText("Account ID" + (" (saved " + K.get_secret(acc_var)[-4:] + ")"
                                                       if K.get_secret(acc_var) else ""))
                acc.setFixedHeight(22)
                acc.setFont(QFont("Courier New", 8))
                lay.addWidget(acc)
                row.update(account=acc, account_var=acc_var)
            krow = QHBoxLayout()
            krow.setSpacing(4)
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("paste new key here" if var is None else "paste key here")
            key.setFixedHeight(22)
            key.setFont(QFont("Courier New", 8))
            krow.addWidget(key, 3)
            row["key"] = key
            if model:
                cur = K.named_model(provider)
                mdl = QLineEdit(cur if cur != K.named(provider).get("model") else "")
                mdl.setPlaceholderText(K.named(provider).get("model", "model"))
                mdl.setToolTip("Model - leave empty for the default")
                mdl.setFixedHeight(22)
                mdl.setFont(QFont("Courier New", 7))
                krow.addWidget(mdl, 2)
                row["named_model"] = mdl
            for txt, fn in (("SAVE", self._save), ("TEST", self._test), ("CLEAR", self._clear)):
                b = self._btn(txt, 46)
                b.clicked.connect(lambda _=False, r=rid, f=fn: f(r))
                krow.addWidget(b)
            lay.addLayout(krow)
            msg = self._lbl("", 7, False, DIM)
            msg.hide()
            lay.addWidget(msg)
            row["msg"] = msg
            self._rows[rid] = row
            self._refresh(row)

        def _voice_section(self, lay):
            lay.addWidget(self._lbl("KAKA KI AWAAZ", 8, True, MED))
            vrow = QHBoxLayout()
            vrow.setSpacing(5)
            self._vbtns = {}
            for k, t in (("brian", "BRIAN (ElevenLabs)"), ("offline", "WINDOWS AWAAZ (offline)")):
                b = self._btn(t, None, 24)
                b.setCheckable(True)
                b.clicked.connect(lambda _=False, kk=k: self._voice_pick(kk))
                self._vbtns[k] = b
                vrow.addWidget(b, 1)
            self._vtest = self._btn("TEST", 54, 24)
            self._vtest.clicked.connect(self._voice_test)
            vrow.addWidget(self._vtest)
            lay.addLayout(vrow)
            self._voice_lbl = self._lbl("", 7, False, DIM)
            lay.addWidget(self._voice_lbl)

        def _voice_refresh(self):
            cur = K.voice_mode()
            for k, b in self._vbtns.items():
                b.setChecked(k == cur)
            self._voice_lbl.setText(
                "Brian bolega. Internet / ElevenLabs na chale toh us baar Windows awaaz."
                if cur == "brian" else "Windows ki apni awaaz - bina internet ke chalti hai.")
            self._voice_lbl.setStyleSheet(f"color: {DIM}; background: transparent; border: none;")

        def _voice_pick(self, k):
            try:
                K.set_voice_mode(k)
            except Exception as e:
                self._voice_lbl.setText(f"Could not save: {type(e).__name__}")
                return
            self._voice_refresh()
            self.changed.emit("SYS: Kaka ki awaaz: " + ("BRIAN." if k == "brian" else "WINDOWS AWAAZ."))

        def _voice_test(self):
            self._vtest.setEnabled(False)
            self._vtest.setText("...")
            mode = K.voice_mode()
            line = ("Haan ji, main Kaka hoon. Ye Brian ki awaaz hai." if mode == "brian"
                    else "Haan ji, main Kaka hoon. Ye Windows ki awaaz hai.")

            def work():
                try:
                    import asyncio
                    from providers import tts_kaka
                    pcm, name = asyncio.run(tts_kaka.test_line(line, mode))
                    if len(pcm) < 2000:
                        raise RuntimeError("koi awaaz nahi bani")
                    tts_kaka.play_pcm(pcm)
                    self._vtest_sig.emit(True, name or "?")
                except Exception as e:
                    self._vtest_sig.emit(False, f"{type(e).__name__}: {str(e)[:80]}")
            threading.Thread(target=work, daemon=True).start()

        def _on_voice_test(self, ok, text):
            self._vtest.setEnabled(True)
            self._vtest.setText("TEST")
            self._voice_lbl.setText(f"TEST: {text} bola \u2713" if ok else f"TEST fail: {text}")
            self._voice_lbl.setStyleSheet(f"color: {OK if ok else ERR}; background: transparent; border: none;")

        def _provider_changed(self, row, fit=True):
            pid = row["combo"].currentData()
            _label, dbase, dmodel = K.PROVIDERS[pid]
            row["base"].setVisible(pid in ("openai", "openrouter"))
            row["model"].setPlaceholderText(dmodel or ("model name (required)" if pid == "openai"
                                                       else "model (optional)"))

        def _provider_of(self, row):
            return row["combo"].currentData() if row.get("combo") else row["provider"]

        def _say(self, row, text, ok=None):
            m = row["msg"]
            m.setText(text)
            m.setStyleSheet(f"color: {OK if ok else (ERR if ok is False else MED)}; "
                            f"background: transparent; border: none;")
            m.show()

        def _refresh(self, row):
            has = self._has(row)
            row["status"].setText(self._masked(row) if has else "empty")
            row["status"].setStyleSheet(f"color: {TEXT if has else DIM}; "
                                        f"background: transparent; border: none;")

        def _name_of(self, choice) -> str:
            if choice == "auto":
                return "AUTO (normal order)"
            if choice == "gemini":
                return "GEMINI (MAIN)"
            if choice == "groq":
                return "GROQ"
            if choice.startswith("slot") and choice[4:].isdigit():
                return K.slot_title(int(choice[4:]) - 1)
            return K.named(choice).get("title", choice.upper())

        def _show_first(self):
            first = K.brain_first()
            self._auto_btn.setChecked(first == "auto")
            self._first_lbl.setText(self._name_of(first))
            for rid, row in self._rows.items():
                on = row_choice(rid) == first
                row["use"].setChecked(on)
                row["use"].setText("IN USE" if on else "USE")
                row["dot"].setStyleSheet(f"color: {AMBER if on else (MED if self._has(row) else '#3a3a3a')}; "
                                         f"background: transparent; border: none;")

        def kaka_sync_mode(self):
            self._show_first()
            self._voice_refresh()

        # -- actions ---------------------------------------------------------
        def _use(self, choice):
            rid = next((r for r in self._rows if row_choice(r) == choice), None)
            if rid and not self._has(self._rows[rid]):
                self._say(self._rows[rid], "Save a key here first, then press USE.", False)
                self._show_first()
                return
            try:
                K.set_brain_first(choice)
            except Exception as e:
                if rid:
                    self._say(self._rows[rid], f"Could not save: {type(e).__name__}", False)
                self._show_first()
                return
            self._show_first()
            self.changed.emit("SYS: Kaka now asks " + self._first_lbl.text().split(" (")[0]
                              + " first.")

        def _save(self, rid):
            row = self._rows[rid]
            typed = row["key"].text().strip()
            try:
                if row.get("slot") is not None:
                    pid = self._provider_of(row)
                    if pid == "openai" and not (row["base"].text().strip() and row["model"].text().strip()):
                        self._say(row, "Needs the address and the model name.", False)
                        return
                    K.save_slot(row["slot"][0], pid, row["base"].text(), row["model"].text())
                if row.get("named_model") is not None:
                    want = row["named_model"].text().strip()
                    if want != (K.named_model(rid) if K.named_model(rid) != K.named(rid).get("model") else ""):
                        K.set_named_model(rid, want)
                if row.get("account") is not None and row["account"].text().strip():
                    K.set_secret(row["account_var"], row["account"].text().strip())
                    row["account"].clear()
                    row["account"].setPlaceholderText("Account ID (saved " +
                                                      K.get_secret(row["account_var"])[-4:] + ")")
                if typed:
                    if row["var"] is None:
                        if len(typed) < 20:
                            self._say(row, "That doesn't look like a full Gemini key.", False)
                            return
                        K.set_gemini_main(typed)
                    else:
                        K.set_secret(row["var"], typed)
                elif not self._has(row):
                    self._say(row, "Paste a key first.", False)
                    return
            except Exception as e:
                self._say(row, f"Could not save: {type(e).__name__}", False)
                return
            row["key"].clear()
            self._armed_clear = ""
            self._refresh(row)
            self._show_first()
            self._say(row, "Saved. Kaka uses it from the next answer. Press TEST to check it.", True)
            self.changed.emit(f"SYS: API key saved ({self._masked(row)}).")

        def _test(self, rid):
            row = self._rows[rid]
            if row["var"] is None:
                key = row["key"].text().strip() or K.gemini_main_key()
            else:
                key = row["key"].text().strip() or K.get_secret(row["var"])
            pid = self._provider_of(row)
            base = row["base"].text().strip() if row.get("base") is not None else ""
            model = ""
            if row.get("named_model") is not None:
                model = row["named_model"].text().strip() or K.named_model(rid)
            elif row.get("model") is not None:
                model = row["model"].text().strip()
            if not key:
                self._say(row, "No key to test.", False)
                return
            self._say(row, "Testing\u2026")

            def work():
                try:
                    ok, msg = K.test_key(pid, key, base, model)
                except Exception as e:
                    ok, msg = False, type(e).__name__
                self._tested.emit(rid, ok, msg)
            threading.Thread(target=work, daemon=True).start()

        def _show_test(self, rid, ok, msg):
            row = self._rows.get(rid)
            if row is not None:
                self._say(row, msg, ok)

        def _clear(self, rid):
            row = self._rows[rid]
            if not self._has(row):
                self._say(row, "Nothing saved here.", None)
                return
            if row["var"] is None and self._armed_clear != rid:
                self._armed_clear = rid
                self._say(row, "This is Kaka's main key. Press CLEAR again to remove it.", False)
                return
            self._armed_clear = ""
            try:
                if row["var"] is None:
                    K.clear_gemini_main()
                else:
                    K.set_secret(row["var"], "")
                if K.brain_first() == row_choice(rid):
                    K.set_brain_first("auto")
            except Exception as e:
                self._say(row, f"Could not remove: {type(e).__name__}", False)
                return
            row["key"].clear()
            self._refresh(row)
            self._show_first()
            self._say(row, "Key removed.", None)
            self.changed.emit("SYS: API key removed.")

    return KeysOverlay
