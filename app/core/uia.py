"""
core/uia.py - read and press buttons in a window through Windows UI Automation
(the accessibility tree screen readers use). Kaka update 9.

Used for WhatsApp calls: the header's "Video call" / "Voice call" buttons are
found BY NAME and pressed through UI Automation - never a blind click at fixed
screen coordinates. Uses comtypes (installed with Kaka's volume control);
available() says whether it works on this PC.

Update 10: also reads text boxes (Edit / Document controls), their text
(Value / Text pattern) and which element has the keyboard focus - so Kaka can
put the cursor in a chat's message box and CHECK the message landed there.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Optional

_IS_WIN = sys.platform.startswith("win")


def print(*args, **kwargs):  # noqa: A001 - update 16: "[uia]" lines also go to kaka-timing.txt
    import builtins
    builtins.print(*args, **kwargs)
    try:
        from core import timing_log as _tl
        _tl.maybe(" ".join(str(a) for a in args))
    except Exception:
        pass


@dataclass
class El:
    name: str
    ctype: str
    left: int
    top: int
    right: int
    bottom: int
    _invoke: Optional[Callable] = None
    _focus: Optional[Callable] = None
    focused: bool = False
    rid: tuple = ()
    offscreen: bool = False
    _value: Optional[Callable] = None
    giant: bool = False

    def value(self) -> Optional[str]:
        """The text in the control (None = this control's text can't be read)."""
        if not self._value:
            return None
        try:
            v = self._value()
            return None if v is None else str(v)
        except Exception as e:
            print(f"[uia] reading text failed on {self.name!r}: {e}")
            return None

    def centre(self) -> tuple:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def invoke(self) -> bool:
        try:
            return bool(self._invoke and (self._invoke() is not False))
        except Exception as e:
            print(f"[uia] invoke failed on {self.name!r}: {e}")
            return False

    def focus(self) -> bool:
        try:
            return bool(self._focus and (self._focus() is not False))
        except Exception as e:
            print(f"[uia] focus failed on {self.name!r}: {e}")
            return False


_STATE: dict = {"auto": None, "mod": None, "tried": False, "backend": ""}


def _comtypes():
    if _STATE["tried"]:
        return _STATE["auto"], _STATE["mod"]
    _STATE["tried"] = True
    if not _IS_WIN:
        return None, None
    try:
        import comtypes.client  # type: ignore
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as M  # type: ignore
        auto = comtypes.client.CreateObject(M.CUIAutomation, interface=M.IUIAutomation)
        _STATE.update(auto=auto, mod=M, backend="comtypes")
    except Exception as e:
        print(f"[uia] comtypes UI Automation unavailable: {e}")
    return _STATE["auto"], _STATE["mod"]


def available() -> bool:
    auto, _m = _comtypes()
    return auto is not None


_CT = {50000: "Button", 50004: "Edit", 50011: "MenuItem", 50007: "ListItem",
       50020: "Text", 50025: "Group", 50033: "Pane", 50026: "Hyperlink",
       50030: "Document", 50003: "ComboBox"}
_CT_ID = {v: k for k, v in _CT.items()}
EDITABLE = ("Edit", "Document")

# ------------------------------------------------------------ update 12
# His WhatsApp (Nov 2, 10:48) reported the keyboard focus as ONE Hyperlink
# covering the whole window (0,0,1920,1030) whose name was the whole page glued
# together ("WhatsApp Search or start a new chat Recent searches ... Send
# document Add contact Invite a friend"). Such an element says nothing about
# where the cursor is, and its name contains every word on the screen (chat
# names, "Send", "Search" ...). So a "giant" element - about as big as its
# window, or a name longer than GIANT_NAME characters - is UNREADABLE: never
# the search box, never the message box, never proof of whose chat is open.
GIANT_NAME = 120
LAST_GIANT: dict = {"el": None}


def _win_rect(hwnd) -> Optional[tuple]:
    if not _IS_WIN or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return None


def _front_rect() -> Optional[tuple]:
    """The foreground window's box, else the screen."""
    if not _IS_WIN:
        return None
    try:
        import ctypes
        u = ctypes.windll.user32
        r = _win_rect(u.GetForegroundWindow())
        if r and r[2] - r[0] > 50 and r[3] - r[1] > 50:
            return r
        return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        return None


def is_giant(e, rect=None) -> bool:
    """update 12: e covers (nearly) the whole window, or its name is a whole
    page glued together -> it tells nothing; treat it as unreadable."""
    if e is None:
        return False
    if len(str(e.name or "")) > GIANT_NAME and e.ctype != "Edit":
        return True                   # (an Edit's name is its label - a long one is still the box)
    w, h = e.right - e.left, e.bottom - e.top
    if w <= 0 or h <= 0:
        return False
    r = rect or _front_rect()
    if not r:
        return False
    rw, rh = max(1, r[2] - r[0]), max(1, r[3] - r[1])
    return w >= rw * 0.85 and h >= rh * 0.6


def giant_note() -> str:
    """For the activity log: the last giant focus Kaka ignored."""
    e = LAST_GIANT.get("el")
    if e is None:
        return ""
    n = str(e.name or "")
    n = n if len(n) <= 60 else n[:57] + "..."
    return f"{e.ctype or '?'} '{n}' @({e.left},{e.top},{e.right},{e.bottom}) - poori window jitna, ignore"


def _reader(el, M):
    """A function returning the element's text: Value pattern, then Text
    pattern, then LegacyIAccessible value. None when none of them answers."""
    def _val():
        got = None
        for pid, iface, how in ((10002, "IUIAutomationValuePattern", "value"),
                                (10014, "IUIAutomationTextPattern", "text"),
                                (10018, "IUIAutomationLegacyIAccessiblePattern", "value")):
            try:
                p = el.GetCurrentPattern(pid)
                if p is None:
                    continue
                q = p.QueryInterface(getattr(M, iface))
                v = q.DocumentRange.GetText(-1) if how == "text" else q.CurrentValue
            except Exception:
                continue
            if v is None:
                continue
            v = str(v)
            if v.strip():
                return v
            got = v if got is None else got
        return got
    return _val


def _rid(el) -> tuple:
    try:
        return tuple(el.GetRuntimeId())
    except Exception:
        return ()


def _wrap(el, M, frt=None) -> "El":
    try:
        name = el.CurrentName or ""
    except Exception:
        name = ""
    try:
        r = el.CurrentBoundingRectangle
        box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        box = (0, 0, 0, 0)
    try:
        ct = _CT.get(int(el.CurrentControlType), str(el.CurrentControlType))
    except Exception:
        ct = ""
    try:
        off = bool(el.CurrentIsOffscreen)
    except Exception:
        off = False

    def _inv(el=el):
        try:
            p = el.GetCurrentPattern(10000)                 # Invoke
            if p is not None:
                p.QueryInterface(M.IUIAutomationInvokePattern).Invoke()
                return True
        except Exception:
            pass
        p = el.GetCurrentPattern(10018)                     # LegacyIAccessible
        if p is not None:
            p.QueryInterface(M.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
            return True
        return False

    def _foc(el=el):
        el.SetFocus()
        return True
    rid = _rid(el)
    focused = bool(frt) and bool(rid) and rid == tuple(frt)
    return El(name, ct, *box, _invoke=_inv, _focus=_foc, focused=focused, rid=rid,
              offscreen=off, _value=_reader(el, M))


# ------------------------------------------------------------ update 14: speed
# Reading a window used to cost 5-6 cross-process calls PER element (name,
# box, type, offscreen, id - and the name twice). WhatsApp's tree has
# thousands of elements, so one search could take seconds, and the call flow
# searched the same tree up to 6 times. Now:
#   - one FindAllBuildCache call brings every element WITH its name / box /
#     type / offscreen / id (UI Automation's own cache request) - the loop
#     below then runs locally;
#   - find() asks only for the control types it wants;
#   - inside `with uia.reuse():` the same window is read once and every
#     find()/controls() in that block reuses it (the call-button search);
#   - every walk has a time cap, so a giant tree cannot stall Kaka.
# If the cache request is not supported, the old per-element reading is used.
WALK_CAP_FAST = 2.5        # s - local loop over cached elements
WALK_CAP_SLOW = 4.0        # s - old per-element reading
LAST_WALK: dict = {"n": 0, "secs": 0.0, "mode": "", "capped": False}
_SCOPE = threading.local()
_FAST: dict = {"ok": True}


@contextmanager
def reuse():
    """Read each window once inside this block (no keys are pressed in it)."""
    prev = getattr(_SCOPE, "snaps", None)
    _SCOPE.snaps = {} if prev is None else prev
    try:
        yield
    finally:
        if prev is None:
            _SCOPE.snaps = None


def invalidate() -> None:
    """Forget the windows read so far in this reuse() block (after a click)."""
    snaps = getattr(_SCOPE, "snaps", None)
    if snaps:
        snaps.clear()


def _type_condition(auto, types):
    if not types:
        return None
    ids = [_CT_ID.get(t) for t in types]
    if any(i is None for i in ids):
        return None
    try:
        conds = [auto.CreatePropertyCondition(30003, i) for i in ids]
        c = conds[0]
        for nxt in conds[1:]:
            c = auto.CreateOrCondition(c, nxt)
        return c
    except Exception:
        return None


def _wrap_cached(el, M, frt=None) -> "El":
    name = el.CachedName or ""
    r = el.CachedBoundingRectangle
    box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    ct = _CT.get(int(el.CachedControlType), str(el.CachedControlType))
    try:
        off = bool(el.CachedIsOffscreen)
    except Exception:
        off = False
    rid = ()
    try:
        v = el.GetCachedPropertyValue(30000)
        rid = tuple(int(x) for x in v) if v is not None else ()
    except Exception:
        rid = ()
    if not rid:
        rid = _rid(el)
    return _make_el(el, M, name, box, ct, off, rid, frt)


def _make_el(el, M, name, box, ct, off, rid, frt) -> "El":
    def _inv(el=el):
        try:
            p = el.GetCurrentPattern(10000)                 # Invoke
            if p is not None:
                p.QueryInterface(M.IUIAutomationInvokePattern).Invoke()
                return True
        except Exception:
            pass
        p = el.GetCurrentPattern(10018)                     # LegacyIAccessible
        if p is not None:
            p.QueryInterface(M.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
            return True
        return False

    def _foc(el=el):
        el.SetFocus()
        return True
    focused = bool(frt) and bool(rid) and tuple(rid) == tuple(frt)
    return El(name, ct, *box, _invoke=_inv, _focus=_foc, focused=focused, rid=rid,
              offscreen=off, _value=_reader(el, M))


def _walk_fast(hwnd: int, limit: int, types=None):
    """[El] for every element under hwnd (giant ones flagged), read with one
    cache request; None when the cache request is not available here."""
    auto, M = _comtypes()
    if auto is None or not _FAST["ok"]:
        return None
    t0 = time.perf_counter()
    try:
        cr = auto.CreateCacheRequest()
        for prop in (30005, 30001, 30003, 30022, 30000):  # name, box, type, offscreen, id
            cr.AddProperty(prop)
        try:      # update 15: same view as the old reader (raw tree, not Control view)
            cr.TreeFilter = auto.CreateTrueCondition()
            cr.TreeScope = 1                                # TreeScope_Element (each found one)
        except Exception:
            pass
    except Exception as e:
        print(f"[uia] fast read not available on this PC ({e}) - old way")
        _FAST["ok"] = False
        return None
    try:
        root = auto.ElementFromHandle(hwnd)
        cond = _type_condition(auto, types) or auto.CreateTrueCondition()
        arr = root.FindAllBuildCache(4, cond, cr)          # TreeScope_Descendants
        total = int(arr.Length)
    except Exception as e:
        _FAST["fails"] = _FAST.get("fails", 0) + 1
        if _FAST["fails"] >= 3:
            _FAST["ok"] = False
        print(f"[uia] fast read failed ({e}) - old way")
        return None
    try:
        fid = auto.GetFocusedElement()
        frt = tuple(fid.GetRuntimeId()) if fid is not None else None
    except Exception:
        frt = None
    wr = _win_rect(hwnd)
    out = []
    capped = False
    bad = 0
    for i in range(min(total, limit)):
        if time.perf_counter() - t0 > WALK_CAP_FAST:
            capped = True
            break
        el = arr.GetElement(i)
        try:
            w = _wrap_cached(el, M, frt)
        except Exception:
            bad += 1
            try:
                w = _wrap(el, M, frt)                       # this one element the old way
            except Exception:
                continue
        if is_giant(w, wr):
            w.giant = True
        out.append(w)
    _FAST["fails"] = 0
    LAST_WALK.update(n=len(out), secs=time.perf_counter() - t0, mode="fast", capped=capped)
    if capped:
        print(f"[uia] window read capped at {WALK_CAP_FAST}s ({len(out)}/{total} elements)")
    return out


def elements(hwnd: int, rx=None, limit: int = 4000, include_giant: bool = False,
             types=None) -> list:
    """Every element under the window (name matching rx if given). Update 12:
    whole-window "giant" elements are left out unless include_giant.
    Update 14: read with one cache request (fast), reused inside reuse(),
    time-capped; `types` limits the control types asked for."""
    auto, M = _comtypes()
    if auto is None or not hwnd:
        return []
    snaps = getattr(_SCOPE, "snaps", None)
    allel = None
    if snaps is not None and hwnd in snaps:
        allel = snaps[hwnd]
    elif old_scope():
        # update 17: WhatsApp call / message, Discord - never the fast (cached)
        # read; the old reader with a hard wall-clock limit
        allel = run_capped(lambda: _elements_slow(hwnd, None, limit, include_giant=True),
                           WALK_CAP_SLOW + HARD_GRACE, [], "window read")
        if allel and snaps is not None:
            snaps[hwnd] = allel
    else:
        want = None if snaps is not None else types
        allel = _walk_fast(hwnd, limit, want)
        if allel is not None and _looks_empty(allel, want):
            allel = _fast_was_empty(hwnd, limit, allel, want)
        if allel and snaps is not None:
            snaps[hwnd] = allel          # update 15: an empty read is never reused
    if allel is None:
        return _elements_slow(hwnd, rx, limit, include_giant)
    out = []
    for w in allel:
        if rx is not None and not rx.search(w.name or ""):
            continue
        if types and w.ctype not in types:
            continue
        if w.giant and not include_giant:
            continue                   # update 12: a whole-window element tells nothing
        out.append(w)
    return out


# ------------------------------------------------------------ update 15
# His PC, Nov 2 2:07 PM: the fast read (update 14) gave "0 buttons (UIA tree
# khali)" for a WhatsApp window that had a chat open - and the call button was
# not found, though the update-13 reader had found it. So: a fast read with no
# elements (or no buttons) for a real window is NOT believed - the old reader
# (one element at a time) reads it again at once, the activity log says which
# reader found what, and after it happens twice the old reader is used for the
# rest of the session (update 13 behaviour, which worked on his PC).
_BUTTONISH = ("Button", "Hyperlink", "MenuItem")
FALLBACK_CAP = WALK_CAP_SLOW      # s


def _log_walk(text: str) -> None:
    print(f"[uia] {text}")
    try:
        from core import desktop_driver as _d
        _d._flog(text)
    except Exception:
        pass


def _looks_empty(els: list, types=None) -> bool:
    real = [e for e in els if not e.giant]
    if not real:
        return True
    wants_buttons = (not types) or any(t in _BUTTONISH for t in types)
    return wants_buttons and not any(e.ctype in _BUTTONISH for e in real)


def _fast_was_empty(hwnd: int, limit: int, fast: list, types=None) -> list:
    t0 = time.perf_counter()
    slow = _elements_slow(hwnd, None, limit, include_giant=True, cap=FALLBACK_CAP)
    nb_f = sum(1 for e in fast if e.ctype in _BUTTONISH and not e.giant)
    nb_s = sum(1 for e in slow if e.ctype in _BUTTONISH and not e.giant)
    better = len([e for e in slow if not e.giant]) > len([e for e in fast if not e.giant]) or nb_s > nb_f
    if better:
        _FAST["empty"] = _FAST.get("empty", 0) + 1
        if _FAST["empty"] >= 2:
            _FAST["ok"] = False
        LAST_WALK.update(mode="slow (fast read was empty)")
        _log_walk(f"[uia] fast read: {len(fast)} elements / {nb_f} buttons - old reader: "
                  f"{len(slow)} elements / {nb_s} buttons in {time.perf_counter() - t0:.1f}s"
                  + (" - old reader from now on" if not _FAST["ok"] else ""))
        return slow
    _log_walk(f"[uia] fast read: {len(fast)} elements / {nb_f} buttons - old reader also "
              f"{len(slow)} / {nb_s} (window really has none)")
    return fast if len(fast) >= len(slow) else slow


def _elements_slow(hwnd: int, rx=None, limit: int = 4000, include_giant: bool = False,
                   cap: float = 0.0) -> list:
    """The update-12 reading (one element at a time), now with a time cap."""
    cap = cap or WALK_CAP_SLOW
    auto, M = _comtypes()
    if auto is None or not hwnd:
        return []
    out = []
    wr = _win_rect(hwnd)
    t0 = time.perf_counter()
    capped = False
    try:
        root = auto.ElementFromHandle(hwnd)
        arr = root.FindAll(4, auto.CreateTrueCondition())     # TreeScope_Descendants
        try:
            fid = auto.GetFocusedElement()
            frt = fid.GetRuntimeId() if fid is not None else None
        except Exception:
            frt = None
        n = min(arr.Length, limit)
        for i in range(n):
            if time.perf_counter() - t0 > cap:
                capped = True
                print(f"[uia] window read capped at {cap}s ({i}/{arr.Length})")
                break
            el = arr.GetElement(i)
            try:
                name = el.CurrentName or ""
            except Exception:
                continue
            if rx is not None and not rx.search(name):
                continue
            w = _wrap(el, M, frt)
            if is_giant(w, wr):
                w.giant = True
                if not include_giant:
                    continue           # update 12: a whole-window element tells nothing
            out.append(w)
    except Exception as e:
        print(f"[uia] reading the window failed: {e}")
    LAST_WALK.update(n=len(out), secs=time.perf_counter() - t0, mode="slow", capped=capped)
    return out


def focused_element(raw: bool = False) -> Optional[El]:
    """The element that has the keyboard focus right now (with its box, id and
    text reader - update 10). Update 12: a whole-window "giant" element counts
    as unreadable (None) unless raw=True; giant_note() describes it."""
    auto, M = _comtypes()
    if auto is None:
        return None
    try:
        el = auto.GetFocusedElement()
        if el is None:
            return None
        e = _wrap(el, M)
        e.focused = True
    except Exception:
        return None
    if not raw and is_giant(e):
        # update 12: his WhatsApp's whole-window Hyperlink - unreadable
        e.giant = True
        LAST_GIANT["el"] = e
        print(f"[uia] focus is a whole-window element - unreadable: {giant_note()}")
        return None
    LAST_GIANT["el"] = None
    return e


def editables(hwnd: int, limit: int = 3000) -> list:
    """Text boxes (Edit / Document controls) under the window - update 10.
    Asks UI Automation for just those control types (fast even in a long chat);
    falls back to reading everything and filtering. Update 17: inside
    old_reader() it runs with a hard wall-clock limit."""
    if old_scope():
        return run_capped(lambda: _editables(hwnd, limit), EDIT_HARD, [], "text boxes read")
    return _editables(hwnd, limit)


def _editables(hwnd: int, limit: int = 3000) -> list:
    auto, M = _comtypes()
    if auto is None or not hwnd:
        return []
    out = []
    try:
        root = auto.ElementFromHandle(hwnd)
        try:
            frt = auto.GetFocusedElement().GetRuntimeId()
        except Exception:
            frt = None
        try:
            cond = auto.CreateOrCondition(auto.CreatePropertyCondition(30003, _CT_ID["Edit"]),
                                          auto.CreatePropertyCondition(30003, _CT_ID["Document"]))
            arr = root.FindAll(4, cond)
        except Exception as e:
            print(f"[uia] typed search failed ({e}) - reading everything")
            return [x for x in elements(hwnd, limit=limit) if x.ctype in EDITABLE]
        wr = _win_rect(hwnd)
        for i in range(min(arr.Length, limit)):
            w = _wrap(arr.GetElement(i), M, frt)
            if is_giant(w, wr):
                continue               # update 12: the whole page, not a text box
            out.append(w)
    except Exception as e:
        print(f"[uia] reading text boxes failed: {e}")
    return out


def find(hwnd: int, pattern: str, types=("Button", "MenuItem", "ListItem", "Hyperlink"),
         region=None) -> list:
    """Elements whose NAME matches pattern (regex, case-insensitive); region is
    (left, top, right, bottom) the element's top-left must be inside."""
    rx = re.compile(pattern, re.I)
    res = []
    for e in elements(hwnd, rx, types=types):
        if types and e.ctype not in types:
            continue
        if region is not None:
            l, t, r, b = region
            if not (l <= e.left <= r and t <= e.top <= b):
                continue
        res.append(e)
    return res


def controls(hwnd: int, types=("Button", "Hyperlink", "MenuItem"), limit: int = 4000) -> list:
    """update 12: every button-like element under the window, named or not
    (giant ones left out) - for the activity log and the call-button fallback."""
    return [e for e in elements(hwnd, limit=limit, types=types) if not types or e.ctype in types]


# ------------------------------------------------------------ update 17
# His PC, Nov 2 2:49 PM (kaka-timing.txt): the fast read (ONE cache request for
# the whole WhatsApp tree - 38,000 elements) blocked INSIDE Windows for ~30 s,
# twice in one call; its 2.5 s cap is only checked after Windows answers. And
# every old-reader read of the whole window costs ~4 s (14,000 elements). So:
#   - old_reader(): inside it (WhatsApp call / message, Discord) the fast read
#     is never used, and every whole-window read runs in a helper thread with a
#     HARD wall-clock limit - Kaka stops waiting and goes on; the thread is
#     abandoned (it ends by itself);
#   - probe(): the chat header is read point by point (ElementFromPoint + a few
#     parents) - a few dozen elements instead of 14,000, well under 1 s.
HARD_GRACE = 1.0           # s on top of the old reader's own 4 s cap
EDIT_HARD = 5.0            # s - text-box search
HARD: dict = {"abandoned": 0, "last": ""}


def old_scope() -> bool:
    return bool(getattr(_SCOPE, "old", 0))


def scope_app() -> str:
    return getattr(_SCOPE, "app", "") or ""


@contextmanager
def old_reader(app: str = ""):
    """No fast (cached) window read in this block (this thread); whole-window
    reads get a hard time limit."""
    prev_old, prev_app = getattr(_SCOPE, "old", 0), getattr(_SCOPE, "app", "")
    _SCOPE.old = prev_old + 1
    if app:
        _SCOPE.app = app
    try:
        yield
    finally:
        _SCOPE.old = prev_old
        _SCOPE.app = prev_app


def old_reader_fn(fn, app: str = ""):
    """Decorator form of old_reader() (for thread entry points)."""
    import functools

    @functools.wraps(fn)
    def _w(*a, **k):
        with old_reader(app):
            return fn(*a, **k)
    _w._kaka_old_reader = True
    return _w


def run_capped(fn, timeout: float, default=None, what: str = "read"):
    """fn() in a helper thread; its result, or `default` once `timeout` seconds
    of WALL time have passed (the thread is left to finish on its own - a UI
    Automation call cannot be interrupted). Never raises."""
    box: dict = {}
    app = scope_app()

    def _run():
        _SCOPE.old = 1                     # the helper never uses the fast read either
        _SCOPE.app = app
        try:
            box["v"] = fn()
        except Exception as e:             # pragma: no cover - logged below
            box["e"] = e
    th = threading.Thread(target=_run, daemon=True, name="kaka-uia-capped")
    t0 = time.perf_counter()
    th.start()
    th.join(max(0.05, float(timeout)))
    if th.is_alive():
        HARD["abandoned"] += 1
        HARD["last"] = what
        print(f"[uia] {what}: {time.perf_counter() - t0:.1f}s hard limit - chhod diya "
              f"(Windows ne waqt pe jawab nahi diya), aage badh raha hoon")
        return default
    if "e" in box:
        print(f"[uia] {what} failed: {box['e']}")
        return default
    return box.get("v", default)


def _wrap_light(el, M) -> "El":
    """name, box, type only (3 calls) - for point probes."""
    try:
        name = el.CurrentName or ""
    except Exception:
        name = ""
    try:
        r = el.CurrentBoundingRectangle
        box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        box = (0, 0, 0, 0)
    try:
        ct = _CT.get(int(el.CurrentControlType), str(el.CurrentControlType))
    except Exception:
        ct = ""
    return _make_el(el, M, name, box, ct, False, (), None)


def _el_at(auto, M, x: int, y: int):
    try:
        pt = M.tagPOINT(int(x), int(y))
    except Exception:
        from ctypes import wintypes
        pt = wintypes.POINT(int(x), int(y))
    return auto.ElementFromPoint(pt)


def probe_point(x: int, y: int, up: int = 3) -> list:
    """[El] at screen point (x, y): the element there, then up to `up` parents
    (giant / whole-window parents are flagged and end the walk)."""
    auto, M = _comtypes()
    if auto is None:
        return []
    out = []
    try:
        el = _el_at(auto, M, x, y)
    except Exception as e:
        print(f"[uia] point ({x},{y}) padh nahi paya: {e}")
        return []
    try:
        walker = auto.ControlViewWalker
    except Exception:
        walker = None
    for i in range(up + 1):
        if not el:                       # None, or a NULL COM pointer (no parent)
            break
        try:
            w = _wrap_light(el, M)
        except Exception:
            break
        if is_giant(w):
            w.giant = True
            out.append(w)
            break
        out.append(w)
        if walker is None or i == up:
            break
        try:
            el = walker.GetParentElement(el)
        except Exception:
            break
    return out


def probe(points, up: int = 3, budget: float = 0.9, stop=None) -> tuple:
    """Read the elements at a few screen points. points: [(tag, x, y)] (or a
    callable giving the next batch - see whatsapp_call.header_read). stop(found)
    -> True ends early. -> ([(tag, x, y, [El])], seconds). Run it through
    run_capped() for a hard limit."""
    t0 = time.perf_counter()
    found = []
    for tag, x, y in points:
        if time.perf_counter() - t0 > budget:
            break
        found.append((tag, x, y, probe_point(x, y, up)))
        try:
            if stop is not None and stop(found):
                break
        except Exception:
            pass
    return found, time.perf_counter() - t0
