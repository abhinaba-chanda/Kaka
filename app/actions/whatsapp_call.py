"""
Kaka action - WhatsApp voice / video call by voice (Kaka update 9; search fix update 11;
update 12: shell focus, whole-window focus ignored, call button search + header click).

"Rahul ko WhatsApp pe video call karo" / "Mummy ko call lagao" / "call kaat do"

His rule: Kaka ASKS before anything that reaches another person. So a call is
two steps:
  1. whatsapp_call(action="call", receiver, call_type) - opens WhatsApp Desktop,
     opens that person's chat (a phone number opens through whatsapp://send),
     CHECKS the right chat is open, and only then asks "Rahul ko video call
     lagaun?". Nothing rings yet.
  2. after a clear "haan": whatsapp_call(action="confirm") presses the header's
     "Video call" / "Voice call" button - found BY NAME through Windows UI
     Automation (WhatsApp for Windows has no keyboard shortcut for calls:
     faq.whatsapp.com keyboard-shortcut list, checked Nov 2026), never a blind
     click at screen coordinates - and checks a call window/controls appeared.
  "call kaat do" -> action="end": presses "End call" (no question needed).
"""
from __future__ import annotations

import os
import re
import threading
import time

from core import outcome

try:
    from core import desktop_driver as drv
except Exception as _e:      # pragma: no cover
    drv = None
try:
    from core import uia
except Exception:            # pragma: no cover
    uia = None
try:
    from core import winfocus
except Exception:            # pragma: no cover
    winfocus = None
try:
    from core import speed
except Exception:            # pragma: no cover
    speed = None
import contextlib


def _lap(step: str) -> None:
    """update 14: mark a step on the running action timer ([time] log line)."""
    if speed is not None:
        speed.lap(step)


def _reuse():
    """update 14: read WhatsApp's UI tree once for several searches."""
    f = getattr(uia, "reuse", None) if uia is not None else None
    return f() if f else contextlib.nullcontext()


def _invalidate() -> None:
    f = getattr(uia, "invalidate", None) if uia is not None else None
    if f:
        try:
            f()
        except Exception:
            pass

_PROCS = ("whatsapp", "whatsapp.root")
_PENDING: dict = {}
_TTL = 300.0
_VIDEO_RX = r"^\s*(start\s+)?video\s*call\s*$"
_VOICE_RX = r"^\s*(start\s+)?(voice|audio)\s*call\s*$"
_CALLMENU_RX = r"^\s*(call|make a call|start a call|start call)\s*$"
# update 12: any short button name that CONTAINS these words, English or Hindi
_VIDEO_ANY_RX = (r"video\s*call|\u0935\u0940\u0921\u093f\u092f\u094b\s*\u0915\u0949\u0932|"
                 r"\u09ad\u09bf\u09a1\u09bf\u0993\s*\u0995\u09b2")          # update 17: Bengali
_VOICE_ANY_RX = (r"(voice|audio)\s*call|\u0935\u0949\u0907\u0938\s*\u0915\u0949\u0932|"
                 r"\u0911\u0921\u093f\u092f\u094b\s*\u0915\u0949\u0932|"
                 r"\u09ad(\u09df|\u09af\u09bc)\u09c7\u09b8\s*\u0995\u09b2|"           # update 17: Bengali voice call
                 r"\u0985\u09a1\u09bf\u0993\s*\u0995\u09b2")                       # Bengali audio call
_CALL_ANY_RX = r"^\s*(call|calls|\u0915\u0949\u0932|\u0995\u09b2)\s*$"
_BTN_TYPES = ("Button", "Hyperlink", "MenuItem", "ListItem")
_END_RX = r"^\s*(end\s*call|end|hang\s*up|leave\s*call|leave|decline)\s*$"
_SEARCH_RX = r"search"


def _say(player, text: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(text)
        except Exception:
            pass


def _log(player, text: str) -> None:
    print(f"[wa-call] {text}")
    if player:
        try:
            player.write_log(text)
        except Exception:
            pass


def _phone(receiver: str) -> str:
    try:
        from actions.send_message import _phone as p
        return p(receiver)
    except Exception:
        d = re.sub(r"\D", "", receiver or "")
        return ("91" + d if len(d) == 10 else d) if len(d) >= 10 else ""


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _windows() -> list:
    """[(hwnd, pid, title)] WhatsApp windows."""
    try:
        return list(drv.app_windows(_PROCS, "WhatsApp"))
    except Exception:
        return []


def _main_window():
    ws = _windows()
    if not ws:
        return None

    def area(w):
        r = _rect(w[0])
        return (r[2] - r[0]) * (r[3] - r[1]) if r else 0
    # the main window is the biggest (a call window is smaller)
    return sorted(ws, key=lambda w: -area(w))[0]


def _rect(hwnd):
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def _header_region(hwnd):
    r = _rect(hwnd)
    if not r:
        return None
    l, t, rr, b = r
    w, h = max(1, rr - l), max(1, b - t)
    return (l + int(w * 0.28), t, rr, t + max(150, int(h * 0.18)))


def _uia_ok() -> bool:
    return uia is not None and uia.available()


def chat_is_open(hwnd, receiver: str, phone: str = "") -> bool:
    """Does the chat header (top of the right pane) show this person?"""
    if not _uia_ok():
        return False
    want = _fold(receiver)
    digits = phone[-10:] if phone else ""
    reg = _header_region(hwnd)               # update 14: once, not per element
    for e in uia.elements(hwnd):
        if not e.name:
            continue
        if reg and not (reg[0] <= e.left <= reg[2] and reg[1] <= e.top <= reg[3]):
            continue
        n = _fold(e.name)
        if (want and len(want) >= 2 and want in n) or (digits and digits in re.sub(r"\D", "", e.name)):
            return True
    return False


def _desc(e) -> str:
    if e is None:
        g = ""
        try:
            g = uia.giant_note() if uia is not None and hasattr(uia, "giant_note") else ""
        except Exception:
            g = ""
        return f"(focus padh nahi paya: {g})" if g else "(focus padh nahi paya)"
    return f"{e.ctype or '?'} '{e.name or ''}' @({e.left},{e.top},{e.right},{e.bottom})"


def _flog(player, step: str) -> None:
    """update 11: log what has the keyboard focus at each step, so his next
    screenshot shows WhatsApp's real control names."""
    try:
        f = uia.focused_element() if _uia_ok() else None
    except Exception:
        f = None
    _log(player, f"[wa] focus {step}: {_desc(f)}")


_SEARCH_NAME_RX = re.compile(
    r"search|start (a )?new chat|meta ai|find|\u0916\u094b\u091c|\u0924\u0932\u093e\u0936", re.I)
_EDITS = ("Edit", "Document", "ComboBox")


def _is_compose(e, rect) -> bool:
    """Is e the chat's message box? (then a name must NEVER be typed into it)"""
    try:
        from core import compose
        if e.name and compose.COMPOSE_RX.search(e.name) and not _SEARCH_NAME_RX.search(e.name):
            return True
    except Exception:
        pass
    if rect:
        l, t, r, b = rect
        if e.top >= t + (b - t) * 0.6 and e.left >= l + (r - l) * 0.25:
            return True                       # bottom of the chat pane
    return False


def _search_candidates(hwnd, rect) -> list:
    """Text boxes that can be the chat-list search: by name, else any text box
    in the top-left (chat list) part of the window."""
    try:
        eds = list(uia.editables(hwnd)) + [e for e in uia.find(hwnd, r".", types=("ComboBox",))]
    except Exception:
        eds = []
    eds = [e for e in eds if not e.offscreen and not _is_compose(e, rect)]
    named = [e for e in eds if e.name and _SEARCH_NAME_RX.search(e.name)]
    if named:
        return named
    if rect:
        l, t, r, b = rect
        return [e for e in eds if e.left < l + (r - l) * 0.5 and e.top < t + (b - t) * 0.35]
    return []


def _search_focus_ok(rect) -> tuple:
    """-> (ok, focused). ok = the focus is a text box that is NOT the message box.
    Unreadable focus counts as ok=None (unknown)."""
    f = uia.focused_element() if _uia_ok() else None
    if f is None:
        return None, None
    if f.ctype not in _EDITS:
        return False, f
    if _is_compose(f, rect) and not (f.name and _SEARCH_NAME_RX.search(f.name)):
        return False, f
    return True, f


def _rect_of(hwnd):
    try:
        from core import compose
        return compose.win_rect(hwnd) or _rect(hwnd)
    except Exception:
        return _rect(hwnd)


def chat_verified(hwnd, receiver: str, phone: str = "") -> tuple:
    """-> (ok, how). Header shows the name, or the message box is named for him
    ("Type a message to Baba Jio")."""
    if chat_is_open(hwnd, receiver, phone):
        return True, "chat header"
    want = _fold(receiver)
    if _uia_ok() and len(want) >= 2:
        try:
            cands = list(uia.editables(hwnd))
            f = uia.focused_element()
            if f is not None:
                cands.append(f)
            for e in cands:
                if e.name and not _SEARCH_NAME_RX.search(e.name) and want in _fold(e.name):
                    return True, f"message box '{e.name}'"
        except Exception:
            pass
    return False, "header / message box mein naam nahi dikha"


def _clearly_compose(f, rect) -> bool:
    """update 11b: the focused element is, beyond doubt, the chat's MESSAGE box
    (a text box named like 'Type a message' or sitting at the bottom of the
    chat pane, and not named like a search box). Only then is the name NOT
    typed - everything else (search box, unknown, unreadable) goes ahead the
    way Update 7 did on his PC."""
    if f is None or f.ctype not in _EDITS:
        return False
    if f.name and _SEARCH_NAME_RX.search(f.name):
        return False
    return _is_compose(f, rect)


def _ascii(s: str) -> bool:
    try:
        str(s).encode("ascii")
        return True
    except Exception:
        return False


def _type_name(receiver: str) -> str:
    """update 12 - Update 7's exact keystrokes for the search box (worked on his
    PC): Ctrl+A, Delete, paste the name (left on the clipboard - WhatsApp reads
    it a moment after Ctrl+V), wait 1 s. -> how it was typed."""
    drv.hotkey("ctrl", "a")
    time.sleep(0.1)
    drv.press("delete")
    time.sleep(0.1)
    try:
        drv.paste(receiver, restore=False)
        how = "paste"
    except TypeError:                       # an older desktop_driver
        drv.paste(receiver)
        how = "paste"
    time.sleep(1.0)
    return how


def open_chat(player, receiver: str, video: bool = False) -> tuple:
    """-> (ok, verified, why). Opens WhatsApp and the person's chat.
    update 11b - the PROVEN path first (Update 7 on his PC: "accha sa search
    karke message kar pa raha tha"): WhatsApp to the front, Ctrl+F, name,
    Enter. UI Automation is only a bonus: it is never required to find or
    confirm the search box. The one hard guard: if UIA can read the focus and
    it is clearly the MESSAGE box, the name is not typed there.
    verified = the chat header / message box shows the name (False when UIA
    cannot tell - the caller then asks him to look at the screen)."""
    phone = _phone(receiver)
    log = (lambda s: _log(player, s)) if player else None
    try:
        drv.set_log(log)                 # update 12: focus_app says which way worked
    except Exception:
        pass
    if phone:
        ok, _p, why = drv.ensure_app("WhatsApp", log=log, need_front=False)
        if not ok:
            return False, False, why
        os.startfile(f"whatsapp://send?phone={phone}")
        # update 17: the chat header / message box is read point by point (no
        # whole-window read); go on as soon as it shows the number (ceiling 3 s)
        t_end = time.monotonic() + 3.0
        time.sleep(0.5)
        h = None
        while True:
            m = _main_window()
            if m and _uia_ok():
                h = header_read(None, m[0], False, receiver, phone)
                if h.verdict == "match":
                    break
            if time.monotonic() >= t_end:
                break
            time.sleep(0.3)
        drv.focus_app(_PROCS, "WhatsApp")
        _lap("chat (number link)")
        mw = _main_window()
        if h is not None and mw:
            _LAST_HDR.update(h=h, at=time.monotonic(), hwnd=mw[0])
        verified = h is not None and h.verdict == "match"
        return True, verified, ""
    ok, _p, why = drv.ensure_app("WhatsApp", log=log)
    _lap("foreground")
    if not ok:
        return False, False, why
    mw = _main_window()
    hwnd = mw[0] if mw else 0
    rect = _rect_of(hwnd) if hwnd else None
    _LAST_HDR.update(h=None, at=0.0, hwnd=0)
    # update 17: no "chat pehle se khula?" whole-window check (it cost 3.7-34 s
    # on his PC) - the proven Update-7 search always runs
    if not drv.focus_app(_PROCS, "WhatsApp"):
        return False, False, "WhatsApp aage nahi aaya, isliye kuch type nahi kiya"
    _lap("foreground")
    # 1. Ctrl+F - WhatsApp's own search key, proven on his PC (Update 7 timing)
    drv.hotkey("ctrl", "f")
    time.sleep(0.5)
    f = uia.focused_element() if _uia_ok() else None
    _flog(player, "Ctrl+F ke baad")

    def _plain_box(e):
        return e is not None and e.ctype in _EDITS and not _clearly_compose(e, rect)

    if f is not None and not _plain_box(f):
        # 2. bonus tries, ONLY when UIA clearly sees the focus somewhere else
        # (the message box, or not a text box at all). Never a refusal by itself.
        _log(player, "[wa] Ctrl+F ke baad cursor search box mein nahi dikha - UIA / Ctrl+Alt+/ try")
        if _uia_ok() and hwnd:
            try:
                cands = _search_candidates(hwnd, rect)
            except Exception:
                cands = []
            _log(player, f"[wa] search box (UIA): {len(cands)} mile" +
                 (f" - {_desc(cands[0])}" if cands else ""))
            if cands and cands[0].focus():
                time.sleep(0.3)
                f = uia.focused_element()
        if not _plain_box(f) and drv.focus_app(_PROCS, "WhatsApp"):
            drv.hotkey("ctrl", "alt", "/")
            time.sleep(0.6)
            f = uia.focused_element() if _uia_ok() else None
        _flog(player, "search box dobara try ke baad")
        if _clearly_compose(f, rect):
            # the one hard guard: never type a NAME into the message box
            return False, False, ("WhatsApp mein cursor message box mein hi atka raha, isliye "
                                  "naam wahan type nahi kiya (warna galat chat mein chala jata)")
    elif f is None:
        _log(player, "[wa] focus padh nahi paya - Ctrl+F pe bharosa (Update 7 jaisa)")
    # 3. the name, then Enter - exactly Update 7's keys and waits:
    #    Ctrl+A, Delete, paste (text left on the clipboard), 1 s, Enter, 0.8 s
    method = _type_name(receiver)
    f2 = uia.focused_element() if _uia_ok() else None
    if f2 is not None and f2.ctype in _EDITS and not _clearly_compose(f2, rect):
        have = None
        try:
            have = f2.value()
        except Exception:
            have = None
        if (have is not None and _fold(receiver) not in _fold(have) and _ascii(receiver)
                and hasattr(drv, "write")):
            # the paste left nothing in the search box - type the letters (update 12)
            _log(player, f"[wa] paste ke baad search box mein naam nahi dikha ('{have[:30]}') - "
                         f"letters type kar raha hoon")
            drv.hotkey("ctrl", "a")
            time.sleep(0.1)
            drv.press("delete")
            time.sleep(0.1)
            drv.write(receiver)
            time.sleep(1.0)
            method = "letter-by-letter type"
            f2 = uia.focused_element() if _uia_ok() else None
    _log(player, f"[wa] naam type kiya: {receiver} ({method})")
    if _clearly_compose(f2, rect):
        # typing jumped into the open chat's message box: take the name out, no Enter
        drv.hotkey("ctrl", "a")
        drv.press("delete")
        _flog(player, "naam message box mein gaya - hata diya")
        return False, False, ("naam search ki jagah khule chat ke message box mein chala gaya "
                              "tha - maine use hata diya, Enter nahi dabaya")
    drv.press("enter")
    time.sleep(0.8)
    _lap("search (Ctrl+F, naam, Enter)")
    _flog(player, "naam + Enter ke baad")
    if not _uia_ok():
        _log(player, f"[wa] '{receiver}' ka chat check: UI Automation nahi - screen pe dekhna hoga")
        return True, False, "UI Automation se check nahi ho saka"
    mw = _main_window()
    if not mw:
        return True, False, "WhatsApp window nahi mili (check nahi ho saka)"
    # update 17: ONE targeted read (header + message box), not the whole window
    h = header_read(player, mw[0], video, receiver)
    if h.hits == 0 and not h.timed_out:
        time.sleep(0.3)                          # the chat was still drawing
        h = header_read(player, mw[0], video, receiver)
    _LAST_HDR.update(h=h, at=time.monotonic(), hwnd=mw[0])
    _lap("header read (naam + call button)")
    if h.verdict == "mismatch":
        _log(player, f"[wa] chat '{h.chat}' khula, '{receiver}' nahi mila - aage kuch nahi kiya")
        return False, False, f"chat {h.chat} khula, {receiver} nahi mila"
    v = h.verdict == "match"
    how = (f"naam dikha: '{h.chat}'" if v else "naam pakka nahi padh paya")
    _log(player, f"[wa] '{receiver}' ka chat check: {'OK - ' + how if v else 'pakka nahi - ' + how}")
    return True, v, how


def _short(e) -> bool:
    return bool(e.name) and len(e.name) <= 40 and not getattr(e, "giant", False)


def _find_call_buttons(hwnd, video: bool) -> list:
    """update 12: the call button anywhere in the WHOLE window - exact names
    first (header), then any short button/link whose name contains "voice
    call" / "video call" (Hindi too)."""
    rx = _VIDEO_RX if video else _VOICE_RX
    reg = _header_region(hwnd)
    hits = uia.find(hwnd, rx, region=reg) or uia.find(hwnd, rx)
    if not hits:
        anyrx = _VIDEO_ANY_RX if video else _VOICE_ANY_RX
        hits = [e for e in uia.find(hwnd, anyrx, types=_BTN_TYPES) if _short(e)]
    hits.sort(key=lambda e: (e.top, -e.left))
    return hits


def _log_buttons(player, hwnd) -> list:
    """Write the first 40 button names WhatsApp shows to UI Automation, so his
    next screenshot shows what the call button is really called."""
    try:
        ctl = list(uia.controls(hwnd)) if hasattr(uia, "controls") else \
            list(uia.find(hwnd, r".", types=_BTN_TYPES))
    except Exception as exc:
        _log(player, f"[call] WhatsApp buttons padh nahi paya: {exc}")
        return []
    names = [f"{e.ctype[:1]}:'{(e.name or '')[:30]}'@{e.left},{e.top}" for e in ctl[:40]]
    _log(player, f"[call] WhatsApp mein {len(ctl)} buttons dikhe (pehle 40): " +
         (", ".join(names) if names else "KOI NAHI (UIA tree khali)"))
    return ctl


def _dpi_scale(hwnd) -> float:
    try:
        import ctypes
        d = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
        return d / 96.0 if d > 0 else 1.0
    except Exception:
        return 1.0


def _header_call_point(hwnd, video: bool, ctl=None) -> tuple:
    """update 12 - LAST resort, only after his "haan": where the call icon sits
    in the open chat's header, computed from the WhatsApp window: header row
    ~62 px below the window top (title strip + half the header), icons counted
    from the right edge (menu, search, voice call, video call; ~48 px apart).
    If UI Automation sees any button near that point (even unnamed), its
    centre is used instead. -> (x, y, how)"""
    r = _rect(hwnd)
    if not r:
        return 0, 0, "window rect nahi mila"
    l, t, rr, b = r
    s = _dpi_scale(hwnd)
    y = t + int(62 * s)
    x = rr - int((36 + (144 if video else 96)) * s)
    how = "header ka andaza (window ke right se)"
    best, bd = None, 30 * s
    for e in ctl or []:
        if getattr(e, "giant", False) or e.right <= e.left or e.bottom <= e.top:
            continue
        if (e.right - e.left) > 120 * s or (e.bottom - e.top) > 80 * s:
            continue
        cx, cy = (e.left + e.right) // 2, (e.top + e.bottom) // 2
        dist = max(abs(cx - x), abs(cy - y))
        if dist < bd:
            best, bd = e, dist
    if best is not None:
        x, y = (best.left + best.right) // 2, (best.top + best.bottom) // 2
        how = f"header button '{best.name or '(bina naam)'}' (andaze ke paas mila)"
    return int(x), int(y), how



# ------------------------------------------------------------ update 17
# His PC, Nov 2 2:49-2:53 PM (kaka-timing.txt): a call took 80 s, then ~19 s.
# Every step read the WHOLE WhatsApp window (14,000 elements, ~4 s each; the
# fast cached read even blocked ~30 s inside Windows). Now ONE targeted read:
# a few points in the open chat's header row and in the message box are read
# with ElementFromPoint (+ a few parents) - the call button by its name, and
# the chat's name for the accuracy check. Budget ~1 s, hard limit 1.5 s.
HEADER_BUDGET = 0.9        # s - probing stops here
HEADER_HARD = 1.5          # s - wall clock, the helper thread is abandoned after it
_COMPOSE_NAME_RX = re.compile(r"^\s*(type|write)\s+a\s+message\s+(to\s+)?(?P<n>.+?)\s*$", re.I)
_NOT_NAME_RX = re.compile(
    r"\b(online|typing|recording|last seen|click here|tap here|contact info|group info|"
    r"profile|details|search|menu|call|calls|video|voice|audio|whatsapp|business|chats|"
    r"status|communities|channels|settings|archived|unread|mute|muted|encrypted|"
    r"disappearing|messages?|members?|you|pinned|starred|admin|attach|emoji|send|"
    r"new chat|meta ai|filter|more options|back|close|minimi[sz]e|maximi[sz]e|restore)\b|"
    r"\d{1,2}:\d{2}|^\W*$", re.I)


class Header:
    """What the targeted header read saw."""

    def __init__(self):
        self.button = None        # the voice / video call button (El)
        self.menu = None          # a plain "Call" button (opens Voice / Video)
        self.buttons: list = []   # every button seen in the header row (named or not)
        self.names: list = []     # header texts that can be the chat's name
        self.compose = None       # the message box (El)
        self.compose_chat = ""    # "Type a message to <this>"
        self.verdict = "unknown"  # match | mismatch | unknown
        self.chat = ""            # the chat that is open (for "chat X khula, Y nahi mila")
        self.secs = 0.0
        self.points = 0
        self.hits = 0
        self.timed_out = False

    def line(self) -> str:
        b = (f"call button '{self.button.name}' @({self.button.left},{self.button.top})"
             if self.button is not None else
             (f"'{self.menu.name}' menu button" if self.menu is not None else
              f"call button naam se nahi mila ({len(self.buttons)} header buttons)"))
        c = (f"message box: '{self.compose_chat}'" if self.compose_chat else
             ("message box: (naam nahi)" if self.compose is not None else "message box nahi dikha"))
        n = ("header: " + ", ".join(f"'{x[:30]}'" for x in self.names[:3])) if self.names else "header naam nahi dikha"
        return (f"[uia] header read {self.secs:.1f}s ({self.points} points"
                + (", HARD LIMIT" if self.timed_out else "") + f"): {b} | {c} | {n} | naam check: {self.verdict}")


def _toks(s: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if t]


def _undouble(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def _latin(s: str) -> bool:
    letters = [c for c in str(s or "") if c.isalpha()]
    return len(letters) >= 2 and sum(1 for c in letters if c.isascii()) >= 0.8 * len(letters)


def name_matches(want: str, got: str, phone: str = "") -> bool:
    """Is `got` (a name WhatsApp shows) the person he asked for? Tolerant:
    'Ma Jio' = 'Maa Jio', 'Rahul' = 'Rahul Sharma', 'Baba Jio' = 'Baba ji'."""
    from difflib import SequenceMatcher
    if not got:
        return False
    dg = re.sub(r"\D", "", got)
    if phone and len(dg) >= 10 and phone[-10:] in dg:
        return True
    fw, fg = _fold(want), _fold(got)
    if len(fw) < 2 or not fg:
        return False
    if fw in fg or (len(fg) >= 3 and fg in fw):
        return True
    uw, ug = _undouble(fw), _undouble(fg)
    if uw in ug or (len(ug) >= 3 and ug in uw):
        return True
    tw = [t for t in _toks(want) if len(t) >= 2]
    tg = [t for t in _toks(got) if len(t) >= 2]

    def tok_ok(a):
        for b in tg:
            if a.startswith(b) or b.startswith(a):
                return True
            if SequenceMatcher(None, _undouble(a), _undouble(b)).ratio() >= 0.75:
                return True
        return False
    if tw and tg and all(tok_ok(a) for a in tw):
        return True
    return SequenceMatcher(None, uw, ug).ratio() >= 0.8


def clearly_other(want: str, got: str, phone: str = "") -> bool:
    """True only when `got` is CLEARLY a different person (both in English
    letters, no similarity) - unreadable / Hindi / unsure never counts."""
    from difflib import SequenceMatcher
    if not got or name_matches(want, got, phone):
        return False
    if phone:
        dg = re.sub(r"\D", "", got)
        return len(dg) >= 10 and phone[-10:] not in dg and len(re.sub(r"[\d\s+()-]", "", got)) == 0
    if not (_latin(want) and _latin(got)):
        return False
    return SequenceMatcher(None, _undouble(_fold(want)), _undouble(_fold(got))).ratio() < 0.6


def _verdict(h: "Header", receiver: str, phone: str = "") -> None:
    texts = ([h.compose_chat] if h.compose_chat else []) + \
        ([h.compose.name] if h.compose is not None and h.compose.name else []) + list(h.names)
    if any(name_matches(receiver, x, phone) for x in texts):
        h.verdict = "match"
        h.chat = next(x for x in texts if name_matches(receiver, x, phone))
        return
    if h.compose_chat and clearly_other(receiver, h.compose_chat, phone):
        h.verdict, h.chat = "mismatch", h.compose_chat
        return
    titles = [x for x in h.names if "," not in x and len(x.split()) <= 4]
    if not h.compose_chat and titles and clearly_other(receiver, titles[0], phone):
        h.verdict, h.chat = "mismatch", titles[0]
        return
    h.verdict = "unknown"


def _wrect(hwnd):
    return _rect(hwnd) or _rect_of(hwnd)


def _header_points(hwnd, h: "Header"):
    """Batches of (tag, x, y) to read, computed from the WhatsApp window."""
    r = _wrect(hwnd)
    if not r:
        return
    l, t, rr, b = r
    w = max(1, rr - l)
    s = _dpi_scale(hwnd)
    # 1. the message box (bottom of the chat pane): "Type a message to <name>"
    yield [("compose", l + int(w * 0.62), b - int(k * s)) for k in (46, 36, 58)]
    # 2. the header's call icons, counted from the right edge (update 12's
    #    position, which placed a call on his PC at (1764, 68))
    yield [("button", rr - int(k * s), t + int(62 * s)) for k in (132, 180, 84, 228)]
    # 3. the chat's name, right of the avatar at the left of the chat pane
    pane = h.compose.left if (h.compose is not None and h.compose.left > l) else l + int(w * 0.30)
    yield [("name", pane + int(kx * s), t + int(ky * s)) for ky in (56, 66) for kx in (95, 140, 200)]
    # 4. the call icons again, a little higher / lower (another WhatsApp layout)
    if h.button is None and h.menu is None:
        yield [("button", rr - int(kx * s), t + int(ky * s)) for ky in (48, 76) for kx in (132, 180)]


def _classify(h: "Header", hwnd, tag: str, els: list, video: bool) -> None:
    r = _wrect(hwnd) or (0, 0, 0, 0)
    l, t, rr, b = r
    s = _dpi_scale(hwnd)
    band = t + int(118 * s)
    exact = re.compile(_VIDEO_RX if video else _VOICE_RX, re.I)
    anyrx = re.compile(_VIDEO_ANY_RX if video else _VOICE_ANY_RX, re.I)
    menurx = re.compile(_CALLMENU_RX + "|" + _CALL_ANY_RX, re.I)
    for e in els:
        if getattr(e, "giant", False):
            continue
        nm = (e.name or "").strip()
        if tag == "compose":
            if e.ctype in ("Edit", "Document") and h.compose is None and not _SEARCH_NAME_RX.search(nm):
                h.compose = e
                m = _COMPOSE_NAME_RX.match(nm)
                if m:
                    h.compose_chat = m.group("n").strip()
            continue
        in_band = e.top >= t - 4 and e.bottom <= band and e.bottom > e.top
        if nm and len(nm) <= 40 and (exact.search(nm) or anyrx.search(nm)):
            if h.button is None or (exact.search(nm) and not exact.search(h.button.name or "")):
                h.button = e
            continue
        if nm and len(nm) <= 40 and menurx.search(nm) and e.ctype in _BTN_TYPES:
            if h.menu is None:
                h.menu = e
            continue
        if e.ctype in ("Button", "Hyperlink", "MenuItem") and in_band:
            if all((x.left, x.top, x.right, x.bottom) != (e.left, e.top, e.right, e.bottom) for x in h.buttons):
                h.buttons.append(e)
        if (tag == "name" and nm and 2 <= len(nm) <= 60 and in_band and e.ctype != "Edit"
                and e.left < rr - int(150 * s) and not _NOT_NAME_RX.search(nm)
                and nm not in h.names):
            h.names.append(nm)


def _header_read_inner(hwnd, video: bool, receiver: str, phone: str, budget: float) -> "Header":
    h = Header()
    t0 = time.monotonic()
    for batch in _header_points(hwnd, h):
        for tag, x, y in batch:
            if time.monotonic() - t0 > budget:
                break
            els = uia.probe_point(x, y, 3)
            h.points += 1
            h.hits += 1 if els else 0
            _classify(h, hwnd, tag, els, video)
            if tag == "compose" and h.compose is not None:
                break                               # one message box is enough
            if tag == "button" and h.button is not None:
                break
        if time.monotonic() - t0 > budget:
            break
        _verdict(h, receiver, phone)
        if h.button is not None and h.verdict != "unknown":
            break                                   # button + name known: done
    _verdict(h, receiver, phone)
    h.secs = time.monotonic() - t0
    return h


def header_read(player, hwnd, video: bool, receiver: str, phone: str = "",
                budget: float = None, hard: float = None) -> "Header":
    """ONE targeted read of the open chat: call button + chat name. Never
    reads the whole window; hard wall-clock limit."""
    budget = HEADER_BUDGET if budget is None else budget
    hard = HEADER_HARD if hard is None else hard
    t0 = time.monotonic()
    h = None
    if hwnd and _uia_ok() and hasattr(uia, "probe_point"):
        run = getattr(uia, "run_capped", None)
        if run is not None:
            h = run(lambda: _header_read_inner(hwnd, video, receiver, phone, budget), hard,
                    None, "header read")
        else:                                       # pragma: no cover
            h = _header_read_inner(hwnd, video, receiver, phone, budget)
    if h is None:
        h = Header()
        h.timed_out = bool(hwnd) and _uia_ok()
        h.secs = time.monotonic() - t0
    try:
        print(h.line())
        from core import timing_log as _tl         # update 16 file
        _tl.maybe(h.line())
    except Exception:
        pass
    if player:
        try:
            player.write_log(h.line())
        except Exception:
            pass
    return h


_LAST_HDR: dict = {"h": None, "at": 0.0, "hwnd": 0}


def _probe_pick(player, x0: int, y0: int, hwnd, video: bool, wait: float = 1.0) -> tuple:
    """After a 'Call' menu button / header click: a small Voice / Video menu may
    open just below. Read a few points there (no whole-window read). -> (pressed, name)"""
    s = _dpi_scale(hwnd)
    rx = re.compile(_VIDEO_ANY_RX if video else _VOICE_ANY_RX, re.I)
    pts = [(x0 + int(dx * s), y0 + int(dy * s)) for dy in (44, 80, 116) for dx in (0, -60, -120)]
    t_end = time.monotonic() + wait

    def _look():
        for x, y in pts:
            for e in uia.probe_point(x, y, 2):
                if e.name and len(e.name) <= 40 and rx.search(e.name) and not getattr(e, "giant", False):
                    return e
        return None
    while True:
        time.sleep(0.25)
        run = getattr(uia, "run_capped", None)
        e = run(_look, 1.0, None, "call menu read") if run else _look()
        if e is not None:
            return bool(e.invoke()), e.name
        if time.monotonic() >= t_end:
            return False, ""


def _start_ringing_check(player, receiver: str, kind: str, before: set, how: str,
                         clicked: bool, video: bool) -> threading.Thread:
    th = threading.Thread(target=_ringing_check, args=(player, receiver, kind, before, how,
                                                       clicked, video),
                          daemon=True, name="wa-call-ring")
    th.start()
    return th


RING_FIRST = 1.5           # s - first look after the press
RING_WINDOW = 5.0          # s - give up after this (background, nobody waits)


def _ringing_check(player, receiver: str, kind: str, before: set, how: str, clicked: bool,
                   video: bool) -> bool:
    """update 17: in the BACKGROUND after "call laga di": a new call window, or
    one End-call read (hard limit). Not seen -> Kaka says so and offers a retry."""
    t0 = time.monotonic()
    time.sleep(RING_FIRST)
    state = ""
    read_done = False
    end = time.monotonic() + max(0.0, RING_WINDOW - RING_FIRST)
    while True:
        try:
            new = [x for x in _windows() if x[0] not in before]
        except Exception:
            new = []
        if new:
            state = "call window khuli"
            break
        if not read_done and _uia_ok():
            read_done = True
            run = getattr(uia, "run_capped", None)

            def _end_btn():
                with uia.old_reader() if hasattr(uia, "old_reader") else contextlib.nullcontext():
                    return any(uia.find(hw, _END_RX) for hw, _p, _t in _windows())
            got = run(_end_btn, 4.5, False, "end-call read") if run else False
            if got:
                state = "End call button dikha"
                break
        if time.monotonic() >= end:
            break
        time.sleep(0.25)
    d = time.monotonic() - t0
    if state:
        _log(player, f"[time] whatsapp_call ringing check (background): {d:.1f}s - "
                     f"{kind} lag rahi hai ({state}; {how})")
        return True
    _log(player, f"[time] whatsapp_call ringing check (background): {d:.1f}s - call shuru hota "
                 f"nahi dikha ({how})")
    _say(player, f"[CALL_FOLLOWUP] The {kind} to {receiver} may not have started (no call "
                 f"window seen). Say exactly ONE short Hinglish line: '{receiver} ko call nahi "
                 f"lagi lagta, dobara try karun?' On a yes call whatsapp_call with "
                 f"action='call', receiver='{receiver}', call_type='{'video' if video else 'voice'}', "
                 f"go_ahead=true.")
    return False

def _press_call_button(hwnd, video: bool) -> tuple:
    """-> (pressed, how)."""
    hits = _find_call_buttons(hwnd, video)
    if not hits:
        # newer WhatsApp: one "Call" button that opens Voice / Video choices
        reg = _header_region(hwnd)
        menu = uia.find(hwnd, _CALLMENU_RX, region=reg) or \
            [e for e in uia.find(hwnd, _CALL_ANY_RX, types=_BTN_TYPES) if _short(e)]
        if menu:
            menu.sort(key=lambda e: -e.left)
            if menu[0].invoke():
                # update 14: the tree changed - read it fresh; look every 0.25 s
                # (ceiling 1 s as before) instead of one fixed 1 s wait
                t_end = time.monotonic() + 1.0
                hits = []
                while not hits:
                    time.sleep(0.25)
                    _invalidate()
                    hits = _find_call_buttons(hwnd, video)
                    if time.monotonic() >= t_end:
                        break
                for w in _windows():                  # the choices may be a popup
                    if not hits and w[0] != hwnd:
                        hits = _find_call_buttons(w[0], video)
    if not hits:
        return False, "button not found"
    return (hits[0].invoke(), f"'{hits[0].name}' button")


def _click_header_call(player, hwnd, video: bool, ctl) -> tuple:
    """update 12: click the header call icon (after haan only). -> (clicked, how)"""
    x, y, how = _header_call_point(hwnd, video, ctl)
    if not x:
        return False, how
    if not drv.is_in_front(_PROCS) and not drv.focus_app(_PROCS, "WhatsApp"):
        return False, "WhatsApp aage nahi aaya, click nahi kiya"
    _log(player, f"[call] WhatsApp call button naam se nahi mila - header pe click: ({x},{y}) - {how}")
    drv.click(x, y)
    # the click may have opened a small Voice / Video menu: pick by name -
    # update 17: a few points just below the click, not the whole window
    try:
        if _uia_ok() and hasattr(uia, "probe_point"):
            ok, nm = _probe_pick(player, x, y, hwnd, video, wait=0.5)
            if ok:
                _log(player, f"[call] menu se '{nm}' dabaya")
    except Exception:
        pass
    return True, f"header click ({x},{y}) - {how}"


def _call_running(before_hwnds: set) -> bool:
    for hwnd, _pid, _t in _windows():
        if hwnd not in before_hwnds:
            return True                        # a new call window
        if _uia_ok() and uia.find(hwnd, _END_RX):
            return True                        # End-call control on screen
    return False


def call_question(receiver: str, kind: str, checked: bool) -> str:
    """update 13: the ONE question before a call (no separate 'sahi banda hai?')."""
    q = f"{receiver} ko {kind} lagaun?"
    if not checked:
        q += " - screen pe check kar lo"
    return q


def _prepare(player, receiver: str, call_type: str, go_ahead: bool = False) -> None:
    kind = "video call" if call_type == "video" else "voice call"
    tm = speed.Timer("whatsapp_call", (lambda s: _log(player, s)) if player else None) \
        if speed is not None else None
    try:
        ok, verified, why = open_chat(player, receiver, video=(call_type == "video"))
        if not ok:
            h = _LAST_HDR.get("h")
            if h is not None and h.verdict == "mismatch":
                # update 17: the search opened somebody else's chat - no call
                _PENDING.clear()
                _log(player, f"[call] WhatsApp: chat '{h.chat}' khula, '{receiver}' nahi mila - "
                             f"call NAHI lagayi")
                _say(player, f"[CALL_WRONG_CHAT] Nothing was called. Say exactly ONE short "
                             f"Hinglish line: 'Chat {h.chat} khula, {receiver} nahi mila - call "
                             f"nahi lagayi.' Nothing else.")
                return
            raise RuntimeError(why)
        _PENDING.clear()
        _PENDING.update(receiver=receiver, call_type=call_type, at=time.monotonic(),
                        verified=verified, opened_by_kaka=True)
        _log(player, f"[call] WhatsApp: '{receiver}' ka chat khola"
                     f"{' (check kiya)' if verified else ' (check nahi ho paya)'} - call abhi NAHI lagayi")
        if go_ahead:
            # update 13: his command was already the go-ahead ("call lagao") -
            # the same confirm step as after a "haan", with no question.
            _log(player, f"[call] WhatsApp: go-ahead pehle se tha - bina pooche {kind} laga raha hoon")
            # update 17: the header read just now is used for the button too
            # (no settle wait, no second read)
            result = _confirm(player, timer=tm, hdr=_fresh_hdr())
            _log(player, f"[call] WhatsApp: go-ahead call -> {result[:120]}")
            _say(player, "[CALL_NO_ASK] His command was the go-ahead, so nothing is asked. "
                         "Tell the user in ONE short Hinglish line what this result says "
                         "happened (e.g. '" + receiver + " ko " + kind + " laga di'), "
                         "no question: " + result)
            return
        # update 13: ONE question, the screen hint folded in
        q = call_question(receiver, kind, bool(verified))
        _log(player, f"[call] WhatsApp: ek hi sawaal - {q}")
        if tm is not None:
            tm.done("sawaal poocha")
        _say(player, f"[CALL_CHECK] Nothing is ringing yet. Ask the user exactly ONE short "
                     f"Hinglish question, this one: '{q}' - nothing else, no separate name "
                     f"or screen question. Any yes (haan / ha / ok / lagao / laga do / kar do) "
                     f"-> call whatsapp_call with action='confirm' at once.")
    except Exception as exc:
        _log(player, f"[call] WhatsApp: {exc}")
        _say(player, f"Tell the user in one short Hinglish sentence that the WhatsApp "
                     f"{kind} to {receiver} was NOT started because: {exc}.")
    finally:
        if tm is not None:
            tm.done()


def _fresh_hdr(max_age: float = 5.0):
    h = _LAST_HDR.get("h")
    if h is None or time.monotonic() - _LAST_HDR.get("at", 0.0) > max_age:
        return None
    return h


def _confirm(player, timer=None, hdr=None) -> str:
    tm = timer
    own = False
    if tm is None and speed is not None and speed.current() is None:
        tm = speed.Timer("whatsapp_call (haan ke baad)", (lambda s: _log(player, s)) if player else None)
        own = True
    try:
        return _confirm_inner(player, hdr)
    finally:
        if own and tm is not None:
            tm.done()


def _confirm_inner(player, hdr=None) -> str:
    p = dict(_PENDING)
    if not p or time.monotonic() - p.get("at", 0) > _TTL:
        _PENDING.clear()
        return outcome.failed("whatsapp_call", "no call is waiting for a yes - call "
                              "whatsapp_call with action='call' first")
    video = p["call_type"] == "video"
    kind = "video call" if video else "voice call"
    receiver = p["receiver"]
    if not _uia_ok():
        return outcome.failed("whatsapp_call", f"I cannot read WhatsApp's buttons on this PC "
                              f"(UI Automation unavailable), so the {kind} was NOT started - "
                              f"tell him to click the {kind} icon at the top of the chat",
                              to=receiver)
    try:
        drv.set_log((lambda s: _log(player, s)) if player else None)
    except Exception:
        pass
    mw = _main_window()
    if not mw or not drv.focus_app(_PROCS, "WhatsApp"):
        return outcome.failed("whatsapp_call", "WhatsApp window could not be brought to the "
                              "front (direct, whatsapp: link, Store app and Start menu all "
                              "tried), so the call was NOT started", to=receiver)
    mw = _main_window() or mw                # the shell may have re-created it
    before = {w[0] for w in _windows()}
    _lap("foreground")
    # update 17: ONE targeted read of the header (call button + chat name);
    # straight after the search the read from open_chat is used as it is
    if hdr is None or _LAST_HDR.get("hwnd") not in (0, mw[0]):
        hdr = header_read(player, mw[0], video, receiver, _phone(receiver))
        _lap("header read (naam + call button)")
    if hdr.verdict == "mismatch":
        _PENDING.clear()
        _log(player, f"[call] WhatsApp: chat '{hdr.chat}' khula, '{receiver}' nahi mila - call NAHI lagayi")
        return outcome.failed("whatsapp_call", f"chat {hdr.chat} khula, {receiver} nahi mila - "
                              f"the {kind} was NOT started. Tell him exactly that in one short "
                              f"Hinglish line: 'Chat {hdr.chat} khula, {receiver} nahi mila'",
                              to=receiver)
    pressed, clicked, how = False, False, ""
    if hdr.button is not None:
        if hdr.button.invoke():
            pressed, how = True, f"'{hdr.button.name}' button (header)"
        else:
            how = f"'{hdr.button.name}' invoke nahi hua"
    elif hdr.menu is not None and hdr.menu.invoke():
        # newer WhatsApp: one "Call" button that opens Voice / Video choices
        x0, y0 = (hdr.menu.left + hdr.menu.right) // 2, (hdr.menu.top + hdr.menu.bottom) // 2
        ok, nm = _probe_pick(player, x0, y0, mw[0], video, wait=1.0)
        if ok:
            pressed, how = True, f"'{hdr.menu.name}' -> '{nm}'"
        else:
            how = f"'{hdr.menu.name}' menu khula par {kind} choice nahi mili"
            try:
                drv.press("esc")
            except Exception:
                pass
    _lap("call button")
    if not pressed:
        if p.get("opened_by_kaka"):
            # update 12: he said haan (or the command was the go-ahead) and Kaka
            # itself opened this chat; the name check passed or could not be read
            # -> the header icon, at a position computed from the WhatsApp window
            clicked, how = _click_header_call(player, mw[0], video, hdr.buttons)
            _lap("call button (header click)")
        if not clicked:
            return outcome.failed("whatsapp_call", f"the {kind} button was not found in WhatsApp "
                                  f"({how or 'header read empty'}) - NOT started; he can click the "
                                  f"{kind} icon at the top of {receiver}'s chat", to=receiver)
    _PENDING.clear()
    # update 17: the ringing check runs in the background - the result goes
    # back now; if no call window shows up Kaka says so and offers a retry
    _log(player, f"[call] WhatsApp {kind} button dabaya - {receiver} ({how}); ringing check background mein")
    _start_ringing_check(player, receiver, kind, before, how, clicked, video)
    return outcome.done("whatsapp_call", to=receiver, call=kind,
                        note=f"call laga di ({how}) - say '{receiver} ko {kind} laga di'; "
                             f"ringing is checked in the background")


def _end() -> str:
    if not _uia_ok():
        return outcome.failed("whatsapp_call", "I cannot read WhatsApp's buttons on this PC, "
                              "so I could not end the call - tell him to press the red button")
    for hwnd, _pid, _t in _windows():
        hits = uia.find(hwnd, _END_RX)
        hits.sort(key=lambda e: 0 if "end" in e.name.lower() or "hang" in e.name.lower() else 1)
        for e in hits:
            if e.invoke():
                time.sleep(1.5)
                still = any(uia.find(h, _END_RX) for h, _p, _t2 in _windows())
                if not still:
                    return outcome.done("whatsapp_call", call="ended")
                return outcome.unverified("whatsapp_call", "End call was pressed; the call "
                                          "controls are still on screen")
    return outcome.failed("whatsapp_call", "no WhatsApp call with an End-call button was found")


def whatsapp_call(parameters=None, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "call").lower().strip()
    receiver = str(p.get("receiver") or "").strip()
    ct = str(p.get("call_type") or "voice").lower()
    go_ahead = str(p.get("go_ahead") or "").lower() in ("1", "true", "yes")   # update 13
    call_type = "video" if "video" in ct else "voice"
    if drv is None:
        return outcome.failed("whatsapp_call", "the desktop driver is not available")
    if action in ("end", "hangup", "hang_up", "cut", "stop"):
        return _end()
    if action in ("confirm", "yes", "dial") or str(p.get("confirm") or "").lower() in ("1", "true", "yes"):
        if _PENDING:
            return _confirm(player)
    if not receiver:
        return outcome.failed("whatsapp_call", "no person was named to call")
    if not drv.can_drive():
        return outcome.failed("whatsapp_call", "keyboard control (pyautogui) is not installed")
    if not _uia_ok():
        return outcome.failed("whatsapp_call", "I cannot read WhatsApp's buttons on this PC "
                              "(Windows UI Automation unavailable), so I will not start a call "
                              "blind - he can call from WhatsApp himself", to=receiver)
    if player:
        try:
            player.write_log(f"[call] WhatsApp {call_type} call -> {receiver}: chat khol raha hoon "
                             + ("(go-ahead: bina pooche lagayega)" if go_ahead else "(ek baar poochega)"))
        except Exception:
            pass
    threading.Thread(target=_prepare, args=(player, receiver, call_type),
                     kwargs={"go_ahead": go_ahead}, daemon=True, name="wa-call").start()
    return outcome.started("whatsapp_call", to=receiver, call=call_type,
                           note=("opening the chat and calling (his command was the go-ahead) "
                                 "- say only that, ask nothing" if go_ahead else
                                 "opening the chat - say only that; the ONE call question "
                                 "arrives by itself"))


TOOL = {
    "name": "whatsapp_call",
    "description": (
        "WhatsApp VOICE or VIDEO CALL from the WhatsApp desktop app. "
        "'Rahul ko WhatsApp pe call karo', 'Mummy ko video call lagao', 'Rahul ko audio "
        "call lagao' -> action='call', receiver, call_type='voice'|'video' (default "
        "voice; 'video call' -> video). A direct command ('call lagao', 'call laga "
        "do', 'call karo', 'video call karo', 'abhi call karo') IS the go-ahead: pass "
        "go_ahead=true and it opens the chat and rings with NO question. Only for a "
        "vague wish leave go_ahead out: it opens the chat and gives you ONE question "
        "('Rahul ko video call lagaun?'); on any yes call again with action='confirm'. "
        "Never ask a separate name or 'sahi banda hai?' question. 'call kaat do' / "
        "'call band karo' / 'end call' -> action='end' (no question). For a TEXT "
        "message use send_message, not this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "call (default) | confirm | end"},
            "receiver": {"type": "STRING", "description": "Contact name as saved, or phone number"},
            "call_type": {"type": "STRING", "description": "voice (default) | video"},
            "go_ahead": {"type": "BOOLEAN", "description": "true for a direct call command ('call lagao / laga do / karo') - calls with no question"},
        },
    },
    "handler": whatsapp_call,
}


# update 17: the WhatsApp call never uses the fast (cached) window read - it
# blocked ~30 s inside Windows on his PC; every remaining whole-window read
# (End call, a rare fallback) has a hard wall-clock limit.
if uia is not None and hasattr(uia, "old_reader_fn"):
    _prepare = uia.old_reader_fn(_prepare, "whatsapp")
    _confirm = uia.old_reader_fn(_confirm, "whatsapp")
    _end = uia.old_reader_fn(_end, "whatsapp")
