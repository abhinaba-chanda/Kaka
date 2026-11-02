"""
core/compose.py - put the cursor in a chat app's MESSAGE BOX and check it is
there, before Kaka types anything (Kaka update 10).

His bug (Nov 2): "WhatsApp ke type box mein cursor nahi gaya" - Kaka opened the
chat, but the keyboard focus stayed in WhatsApp's search box / nowhere, so the
message was pasted nowhere. Now, for WhatsApp / Telegram / Discord:

  1. find the message box through Windows UI Automation: a text box whose name
     is like "Type a message" / "Type a message to Rahul" / "Message @rahul" /
     "Write a message" (Hindi too), else the bottom-most text box in the chat
     pane - NEVER the search box;
  2. put the cursor there: UI Automation SetFocus, else a click on the middle
     of that box (only while the app is verified in front);
  3. last resort (no box found): one click where the message box always is -
     bottom-middle of the chat pane - again only with the app in front;
  4. after every try CHECK through UI Automation that the focused element is a
     text box that is not the search box. Not checked = nothing is typed.

Esc is NOT used: in WhatsApp for Windows Esc closes the open chat. WhatsApp's
keyboard-shortcut list (faq.whatsapp.com) has no "go to message box" key.
Every try is written to the activity log ("message box: ... OK / failed").
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Callable, Optional

try:
    from core import uia
except Exception:            # pragma: no cover
    uia = None
try:
    from core import desktop_driver as drv
except Exception:            # pragma: no cover
    drv = None

EDITABLE = ("Edit", "Document")
SEARCH_RX = re.compile(r"search|find|\u0916\u094b\u091c|\u0924\u0932\u093e\u0936|"
                       r"start (a )?new chat|new chat", re.I)
COMPOSE_RX = re.compile(
    r"type a message|type your message|write a message|send a message|"
    r"^\s*message\b|message\s+[@#]|message to\b|compose|"
    r"\u0938\u0902\u0926\u0947\u0936|\u0938\u0928\u094d\u0926\u0947\u0936|"
    r"\u092e\u0948\u0938\u0947\u091c|\u0938\u0902\u0926\u0947\u0936 \u091f\u093e\u0907\u092a",
    re.I)


def _log(log, text: str) -> None:
    print(f"[compose] {text}")
    if log:
        try:
            log(text)
        except Exception:
            pass


def win_rect(hwnd) -> Optional[tuple]:
    """(left, top, right, bottom) of the window's client area on screen."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        r = wintypes.RECT()
        if not u32.GetClientRect(hwnd, ctypes.byref(r)):
            return None
        pt = wintypes.POINT(0, 0)
        u32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y, pt.x + r.right, pt.y + r.bottom
    except Exception:
        return None


def dpi_scale(hwnd) -> float:
    try:
        import ctypes
        d = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return max(1.0, d / 96.0) if d else 1.0
    except Exception:
        return 1.0


def main_window(procs, title: str = "") -> int:
    """The app's biggest visible window (a call / popup window is smaller)."""
    try:
        ws = list(drv.app_windows(procs, title))
    except Exception:
        ws = []
    best, area = 0, -1
    for hwnd, _pid, _t in ws:
        r = win_rect(hwnd) or (0, 0, 0, 0)
        a = (r[2] - r[0]) * (r[3] - r[1])
        if a > area:
            best, area = hwnd, a
    return best


def _is_search(e) -> bool:
    return bool(e.name) and bool(SEARCH_RX.search(e.name))


def _chat_pane_left(rect) -> int:
    l, _t, r, _b = rect
    w = max(1, r - l)
    # WhatsApp/Telegram/Discord: the chat list + side rail is the left ~25-40%;
    # a narrow window shows the chat alone
    return l + int(w * 0.25) if w >= 700 else l


def _probe_compose(hwnd, rect):
    """update 17 (WhatsApp): the message box read at a few points at the bottom
    of the chat pane (ElementFromPoint) - no whole-window read; hard 1 s."""
    if not hasattr(uia, "probe_point") or not hasattr(uia, "run_capped"):
        return None
    l, t, r, b = rect
    s = dpi_scale(hwnd)
    x = (_chat_pane_left(rect) + r) // 2

    def _look():
        for k in (46, 36, 58):
            for e in uia.probe_point(x, b - int(k * s), 2):
                if getattr(e, "giant", False) or e.ctype not in EDITABLE or _is_search(e):
                    continue
                h = e.bottom - e.top
                if e.top >= t + (b - t) * 0.5 and 0 < h < 0.45 * max(1, b - t):
                    return e
        return None
    return uia.run_capped(_look, 1.0, None, "message box point read")


def find_compose(hwnd, rect=None) -> tuple:
    """-> (element or None, how). Never returns a search box."""
    if uia is None or not hwnd:
        return None, "UI Automation not available"
    rect = rect or win_rect(hwnd)
    if rect and getattr(uia, "scope_app", None) and uia.scope_app() == "whatsapp":
        box = _probe_compose(hwnd, rect)      # update 17: a few points, not the whole window
        if box is not None:
            return box, f"neeche ka text box (point read): '{box.name or '(bina naam)'}'"
    eds = []
    for e in uia.editables(hwnd):
        if e.ctype not in EDITABLE or _is_search(e) or e.offscreen:
            continue
        w, h = e.right - e.left, e.bottom - e.top
        if w <= 4 or h <= 4:
            continue
        if rect:
            l, t, r, b = rect
            if e.right < l or e.left > r or e.bottom < t or e.top > b:
                continue
            if h > 0.45 * max(1, b - t):        # the whole web page / message list
                continue
        eds.append(e)
    named = [e for e in eds if e.name and COMPOSE_RX.search(e.name)]
    if named:
        named.sort(key=lambda e: -e.top)
        return named[0], f"naam se mila: '{named[0].name}'"
    if rect:
        l, t, r, b = rect
        pane = _chat_pane_left(rect)
        low = [e for e in eds if e.left >= pane - 4 and e.top >= t + (b - t) * 0.5]
    else:
        low = list(eds)
    if low:
        low.sort(key=lambda e: -e.bottom)
        return low[0], f"sabse neeche wala text box: '{low[0].name or '(bina naam)'}'"
    return None, f"message box nahi mila ({len(eds)} text box dekhe)"


def _same(f, box) -> bool:
    """Is the focused element f the message box (or a part inside it)?"""
    if f.rid and box.rid and tuple(f.rid) == tuple(box.rid):
        return True
    if f.right <= f.left or f.bottom <= f.top:
        return False
    return (f.left >= box.left - 4 and f.top >= box.top - 4 and
            f.right <= box.right + 4 and f.bottom <= box.bottom + 4)


def focus_ok(target=None, rect=None) -> tuple:
    """-> (ok, what has focus). ok = the focused element is a text box that is
    not the search box, and is the target box (or, with no target, sits in the
    bottom of the chat pane)."""
    if uia is None:
        return False, "UI Automation not available"
    f = uia.focused_element()
    if f is None:
        return False, "unknown (focus could not be read)"
    what = f"{f.ctype or '?'} '{f.name}'"
    if _is_search(f):
        return False, what + " (search box)"
    if target is not None and _same(f, target):
        return True, what               # the box itself, or the text part inside it
    if f.ctype not in EDITABLE:
        return False, what
    if target is not None:
        return False, what
    if rect:
        l, t, r, b = rect
        if f.top < t + (b - t) * 0.5 or f.left < _chat_pane_left(rect) - 4:
            return False, what + " (chat pane ke neeche nahi)"
    return True, what


def focus_compose(procs, title: str, log=None, hwnd: int = 0) -> dict:
    """Put the keyboard cursor in the message box and VERIFY it.
    -> {"ok", "method", "box", "tried": [...], "hwnd", "rect"}"""
    res = {"ok": False, "method": "", "box": None, "tried": [], "hwnd": 0, "rect": None}
    tried = res["tried"]
    if uia is None or not uia.available():
        tried.append("UI Automation is not available on this PC - cursor check impossible")
        _log(log, f"[msg] {title} message box: UI Automation nahi chal raha - type nahi karunga")
        return res
    try:
        drv.can_drive() and getattr(drv.pyautogui, "size", None)   # DPI-aware before reading rects
    except Exception:
        pass
    hwnd = hwnd or main_window(procs, title)
    if not hwnd:
        tried.append(f"{title} window not found")
        return res
    if not drv.is_in_front(procs) and not drv.focus_app(procs, title):
        tried.append(f"{title} could not be brought to the front")
        _log(log, f"[msg] {title} aage nahi aaya - message box pe cursor nahi rakha")
        return res
    rect = win_rect(hwnd)
    res.update(hwnd=hwnd, rect=rect)

    def done(method, box):
        res.update(ok=True, method=method, box=box)
        _log(log, f"[msg] {title} message box pe cursor: {method} - OK")
        return res

    # update 11: after Enter on a search result the app usually puts the cursor
    # in the message box itself - an editable control at the bottom of the chat
    # pane that is not the search box is accepted as it is
    f0 = uia.focused_element()
    _log(log, f"[msg] {title} focus abhi: " + (f"{f0.ctype or '?'} '{f0.name}' @({f0.left},{f0.top},{f0.right},{f0.bottom})" if f0 is not None else "(padh nahi paya)"))
    if f0 is not None and f0.ctype in EDITABLE and not _is_search(f0) and rect:
        l, t, r, b = rect
        hh = f0.bottom - f0.top
        if f0.top >= t + (b - t) * 0.5 and f0.left >= _chat_pane_left(rect) - 4 and 0 < hh < 0.45 * max(1, b - t):
            return done("Enter ke baad cursor pehle se message box mein", f0)
    box, how = None, ""
    for i in range(3):                     # Chromium builds its tree on first ask
        box, how = find_compose(hwnd, rect)
        if box is not None:
            break
        time.sleep(0.8)
    _log(log, f"[msg] {title} message box: {how}")
    ok, what = focus_ok(box, rect)
    if ok:
        return done("pehle se wahi tha", box or uia.focused_element())
    if box is not None:
        # 1. UI Automation SetFocus
        if box.focus():
            time.sleep(0.3)
            ok, what = focus_ok(box, rect)
            if ok:
                return done("UIA SetFocus", box)
            tried.append(f"UIA SetFocus: focus went to {what}")
        else:
            tried.append("UIA SetFocus: refused")
        _log(log, f"[msg] {title}: {tried[-1]}")
        # 2. click the middle of that box (app must be in front)
        if drv.is_in_front(procs):
            x, y = box.centre()
            drv.click(x, y)
            time.sleep(0.35)
            ok, what = focus_ok(box, rect)
            if ok:
                return done(f"box pe click ({x},{y})", box)
            tried.append(f"click on box ({x},{y}): focus is {what}")
        else:
            tried.append("click on box: skipped, app not in front")
        _log(log, f"[msg] {title}: {tried[-1]}")
    # 3. last resort: where the message box always is
    if rect and drv.is_in_front(procs):
        l, t, r, b = rect
        s = dpi_scale(hwnd)
        x = (_chat_pane_left(rect) + r) // 2
        y = b - int(34 * s)
        drv.click(x, y)
        time.sleep(0.4)
        ok, what = focus_ok(None, rect)
        if ok:
            f = uia.focused_element()
            return done(f"window ke neeche click ({x},{y})", f)
        tried.append(f"click bottom of chat ({x},{y}): focus is {what}")
    else:
        tried.append("bottom click: skipped (" + ("app not in front" if rect else "no window size") + ")")
    _log(log, f"[msg] {title}: {tried[-1]}")
    _log(log, f"[msg] {title} message box pe cursor NAHI aaya - kuch type nahi kiya")
    return res


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s or ""))
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip().casefold()


def has_text(box_text: Optional[str], text: str) -> bool:
    a, b = norm(box_text), norm(text)
    if not b:
        return False
    return b in a or (len(b) > 40 and b[:40] in a)


def read_box(box=None) -> Optional[str]:
    """Text in the message box: the focused element first (it is the box when
    focus_compose said OK), then the box we found."""
    f = uia.focused_element() if uia is not None else None
    v = f.value() if f is not None and f.ctype in EDITABLE and not _is_search(f) else None
    if v is None and box is not None:
        v = box.value()
    return v


def type_message(text: str, box=None, log=None, title: str = "") -> tuple:
    """Paste the message into the focused message box (Hindi-safe) and check it
    is there. -> (state, box_text) with state 'ok' | 'missing' | 'unreadable'."""
    drv.hotkey("ctrl", "a")              # replace an old draft in THIS box only
    drv.paste(text)
    v = None
    for _ in range(12):                    # update 14: every 0.12 s (ceiling ~1.4 s as before)
        time.sleep(0.12)
        v = read_box(box)
        if v is not None and has_text(v, text):
            _log(log, f"[msg] {title}: message box mein text dikh gaya - check OK")
            return "ok", v
    if v is None:
        _log(log, f"[msg] {title}: message box ka text padh nahi paya (check nahi ho saka)")
        return "unreadable", None
    _log(log, f"[msg] {title}: message box mein text NAHI dikha (box mein: '{str(v)[:40]}')")
    return "missing", v
