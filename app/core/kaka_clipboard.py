r"""
core/kaka_clipboard.py - Kaka update 19: clipboard memory.

Keeps the last 20 TEXT clips (newest first) in memory and in
F:\Kaka\Data\clipboard.json. Never keeps:
  - anything over 20 KB
  - anything that looks like a password / OTP / API key (see is_secret)
  - text Kaka itself put on the clipboard (WhatsApp search names etc.)

Listening costs nothing while idle: Qt's own clipboard-changed signal (Windows
AddClipboardFormatListener under the hood) is used. Without a Qt app (tests,
other systems) a 1 s GetClipboardSequenceNumber poll is the fallback.
Nothing here raises.
"""
from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime

from core import kaka_services as ks

FILE = "clipboard.json"
MAX_ITEMS = 20
MAX_BYTES = 20 * 1024
_LOCK = threading.RLock()
_ITEMS: list = []           # [{"text", "at"}] newest first
_LOADED = {"on": False}
_STARTED = {"on": False, "how": ""}
_STATS = {"kept": 0, "skipped_secret": 0, "skipped_big": 0, "skipped_own": 0}

# ------------------------------------------------------------------ filters
_KEY_MARKERS = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|sk_(live|test)_|AIza[0-9A-Za-z_\-]{10,}|ghp_[A-Za-z0-9]{10,}|"
    r"gho_|github_pat_|xox[abpr]-|AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
    r"gsk_[A-Za-z0-9]{10,}|hf_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.|"
    r"ya29\.|Bearer\s+[A-Za-z0-9\-_.]{16,})")
_PASS_WORDS = re.compile(r"\b(password|passwd|pwd|passcode|pin|otp|api[_ ]?key|secret|token)\s*[:=]\s*\S+",
                         re.I)
_OTP_TEXT = re.compile(r"\b(otp|one[- ]time|verification code|code is|is your .{0,20}code)\b", re.I)
_URL = re.compile(r"^(https?://|www\.)\S+$", re.I)
_URL_SECRET = re.compile(r"[?&](key|api_key|apikey|token|access_token|sig|signature|password|secret)=",
                         re.I)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def is_secret(text: str) -> bool:
    """True when the clip looks like a password, OTP or API key."""
    t = (text or "").strip()
    if not t:
        return False
    if _KEY_MARKERS.search(t) or _PASS_WORDS.search(t):
        return True
    if re.fullmatch(r"\d{4,8}", t) or re.fullmatch(r"\d{3}[ -]\d{3}", t):
        return True                                   # bare OTP
    if len(t) < 200 and _OTP_TEXT.search(t) and re.search(r"\b\d{4,8}\b", t):
        return True                                   # "Your OTP is 482913"
    if _URL.match(t):
        return bool(_URL_SECRET.search(t))
    if " " in t or "\n" in t or "\t" in t:
        return False                                  # normal sentences
    if "\\" in t or re.match(r"^[A-Za-z]:[\\/]", t) or t.startswith("/"):
        return False                                  # file paths
    if re.fullmatch(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", t):
        return False                                  # e-mail address
    classes = sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if len(t) >= 20 and classes >= 2 and _entropy(t) >= 3.5 and re.search(r"\d", t):
        return True                                   # long random token
    if len(t) >= 32 and re.fullmatch(r"[A-Fa-f0-9]+", t):
        return True                                   # hex key / hash
    if 8 <= len(t) <= 40 and classes >= 4:
        return True                                   # "Abcd@1234" style password
    return False


def accept(text, own: bool = False) -> str:
    """-> "" when kept, else the reason it was skipped."""
    if own:
        return "own"
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        return "big"
    if is_secret(text):
        return "secret"
    return ""


# ------------------------------------------------------------------ store
def _path():
    return ks.data_path(FILE)


def _load() -> None:
    if _LOADED["on"]:
        return
    _LOADED["on"] = True
    data = ks.read_json(_path(), [])
    items = []
    if isinstance(data, list):
        for i in data:
            if isinstance(i, dict) and isinstance(i.get("text"), str) and not accept(i["text"]):
                items.append({"text": i["text"], "at": str(i.get("at") or "")})
    _ITEMS[:] = items[:MAX_ITEMS]


def _save_async() -> None:
    snap = list(_ITEMS)
    threading.Thread(target=lambda: ks.write_json(_path(), snap), daemon=True,
                     name="kaka-u19-clip-save").start()


def add(text: str, own: bool = False, save: bool = True) -> str:
    """Offer a clip. -> "" if kept, else why skipped."""
    why = accept(text, own)
    if why:
        key = {"secret": "skipped_secret", "big": "skipped_big", "own": "skipped_own"}.get(why)
        if key:
            _STATS[key] += 1
        return why
    with _LOCK:
        _load()
        if _ITEMS and _ITEMS[0]["text"] == text:
            return "same"
        _ITEMS[:] = [i for i in _ITEMS if i["text"] != text]
        _ITEMS.insert(0, {"text": text, "at": datetime.now().replace(microsecond=0).isoformat()})
        del _ITEMS[MAX_ITEMS:]
        _STATS["kept"] += 1
        if save:
            _save_async()
    return ""


def items() -> list:
    with _LOCK:
        _load()
        return [dict(i) for i in _ITEMS]


def get(n: int = 1):
    """n = 1 latest, 2 the one before ... -> dict or None."""
    with _LOCK:
        _load()
        if 1 <= n <= len(_ITEMS):
            return dict(_ITEMS[n - 1])
    return None


def clear() -> int:
    with _LOCK:
        _load()
        n = len(_ITEMS)
        _ITEMS.clear()
        ks.write_json(_path(), [])
    return n


# ------------------------------------------------------------------ own-copy check
def _owner_is_us() -> bool:
    """True when the clipboard owner window belongs to Kaka's own process
    (text Kaka pasted itself: WhatsApp names, messages, typing)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.GetClipboardOwner.restype = wintypes.HWND
        hwnd = u32.GetClipboardOwner()
        if not hwnd:
            return False
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def _on_qt_change() -> None:
    """Qt thread: read the text only (cheap); filtering + saving is quick."""
    try:
        from PyQt6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is None or not md.hasText():
            return
        text = md.text()
        add(text, own=_owner_is_us())
    except Exception as e:
        ks._print(f"[clipboard] read failed: {e}")


def _poll_loop() -> None:
    """Fallback without Qt: 1 s sequence-number poll (a number compare when idle)."""
    try:
        import ctypes
        u32 = ctypes.windll.user32
    except Exception:
        return
    last = u32.GetClipboardSequenceNumber()
    while True:
        time.sleep(1.0)
        try:
            seq = u32.GetClipboardSequenceNumber()
            if seq == last:
                continue
            last = seq
            text = _read_text_win32()
            if text:
                add(text, own=_owner_is_us())
        except Exception:
            pass


def _read_text_win32() -> str:
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = wintypes.HANDLE
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(13)            # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def start() -> str:
    if _STARTED["on"]:
        return _STARTED["how"]
    _STARTED["on"] = True
    with _LOCK:
        _load()

    def _hook():
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().dataChanged.connect(_on_qt_change)
            _STARTED["how"] = "qt"
            ks.log("SYS: Clipboard yaad chalu (last 20 text, password/OTP skip)")
        except Exception as e:
            ks._print(f"[clipboard] qt hook failed: {e}")
            _start_poll()
    if not ks.run_in_ui(_hook):
        _start_poll()
    return _STARTED["how"] or "qt"


def _start_poll() -> None:
    if sys.platform != "win32":
        _STARTED["how"] = "none"
        return
    _STARTED["how"] = "poll"
    threading.Thread(target=_poll_loop, name="kaka-u19-clip-poll", daemon=True).start()


ks.on_start(start)
