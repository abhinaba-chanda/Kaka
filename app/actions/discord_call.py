"""
Kaka action - Discord voice / video call by voice (Kaka update 11).

"Rahul ko Discord pe call karo" / "Rahul ko Discord pe video call karo" / "call kaat do"

Same shape as whatsapp_call (update 9), and the same rule: Kaka ASKS before
anything that reaches another person. A call is two steps:
  1. discord_call(action="call", receiver, call_type) - opens Discord desktop,
     brings it to the front, opens the DM with Ctrl+K (Discord's own Quick
     Switcher shortcut), and CHECKS the open DM is that person's (window title
     or the DM header read through UI Automation). Only then it asks "Rahul ko
     Discord pe call lagaun?". Nothing rings yet. If the DM can't be checked,
     no call is offered at all.
  2. after a clear "haan": discord_call(action="confirm") presses the DM
     header's "Start Voice Call" / "Start Video Call" button found by its
     accessible NAME (never a blind click at screen coordinates) and checks a
     "Disconnect" / "Leave Call" control appeared.
  "call kaat do" while a Discord call is on -> action="end": presses
  "Disconnect" by name (no question needed).
"""
from __future__ import annotations

import re
import threading
import time

from core import outcome

try:
    from core import desktop_driver as drv
except Exception:            # pragma: no cover
    drv = None
try:
    from core import uia
except Exception:            # pragma: no cover
    uia = None

_PROCS = ("discord",)
_TITLE = "Discord"
_PENDING: dict = {}
_STATE: dict = {"last_call_at": 0.0}
_TTL = 300.0
_VIDEO_RX = r"^\s*start\s+video\s+call\s*$"
_VOICE_RX = r"^\s*start\s+(voice|audio)\s+call\s*$"
_HANGUP_RX = r"^\s*(disconnect|leave\s+call|end\s+call|hang\s*up)\s*$"
_BTN = ("Button", "MenuItem", "Hyperlink")


def _say(player, text: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(text)
        except Exception:
            pass


def _log(player, text: str) -> None:
    print(f"[dc-call] {text}")
    if player:
        try:
            player.write_log(text)
        except Exception:
            pass


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _uia_ok() -> bool:
    return uia is not None and uia.available()


def _windows() -> list:
    """[(hwnd, pid, title)] Discord windows."""
    try:
        return list(drv.app_windows(_PROCS, _TITLE))
    except Exception:
        return []


def _rect(hwnd):
    try:
        from core import compose
        r = compose.win_rect(hwnd)
        if r:
            return r
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def _main_window():
    ws = _windows()
    if not ws:
        return None

    def area(w):
        r = _rect(w[0])
        return (r[2] - r[0]) * (r[3] - r[1]) if r else 0
    return sorted(ws, key=lambda w: -area(w))[0]


def _header_region(hwnd):
    """Top strip of the conversation pane (right of the server + DM lists)."""
    r = _rect(hwnd)
    if not r:
        return None
    l, t, rr, b = r
    w, h = max(1, rr - l), max(1, b - t)
    try:
        from core import compose
        sc = compose.dpi_scale(hwnd)
    except Exception:
        sc = 1.0
    # server list (72 px) + DM list (240 px) sit on the left - skip them
    left = min(int(w * 0.5), max(int(w * 0.22), int(320 * sc)))
    return (l + left, t, rr, t + max(130, int(h * 0.16)))


def _in(e, reg) -> bool:
    return reg is None or (reg[0] <= e.left <= reg[2] and reg[1] <= e.top <= reg[3])


def dm_is_open(hwnd, title: str, receiver: str) -> tuple:
    """-> (verified, how). Is the open Discord DM this person's?"""
    want = _fold(receiver)
    if len(want) < 2:
        return False, "name too short to check"
    # Discord titles a DM "@rahul - Discord" (older) or "Discord | @rahul" (newer)
    t = str(title or "")
    for part in re.split(r"\s+[-|]\s+|\s*\|\s*", t):
        part = part.strip()
        if not part or part.lower() == "discord" or part.startswith("#"):
            continue          # "#general" is a server channel, not a DM
        if want in _fold(part):
            return True, f"window title '{t}'"
    if not _uia_ok():
        return False, "UI Automation unavailable"
    reg = _header_region(hwnd)
    for e in uia.elements(hwnd):
        if not e.name or e.ctype in ("Edit", "Document", "ComboBox"):
            continue          # never trust the text he/Kaka typed in a box
        if not _in(e, reg):
            continue
        n = _fold(e.name)
        if want in n and not n.startswith("message"):
            return True, f"DM header '{e.name}'"
    return False, f"title '{t}' / header did not show '{receiver}'"


def _quick_switcher_focused(hwnd) -> bool:
    """After Ctrl+K the cursor must be in the Quick Switcher box - NOT the
    chat's message box (else the name would be sent as a message)."""
    f = uia.focused_element()
    if f is None or f.ctype not in ("Edit", "ComboBox"):
        return False
    name = (f.name or "").strip().lower()
    if name.startswith("message"):
        return False                     # "Message @rahul" = the compose box
    r = _rect(hwnd)
    if r and f.top > r[1] + int((r[3] - r[1]) * 0.80):
        return False                     # bottom of the window = compose box
    return True


def open_dm(player, receiver: str) -> tuple:
    """-> (ok, verified, how_or_why)."""
    log = (lambda s: _log(player, s)) if player else None
    ok, _p, why = drv.ensure_app(_TITLE, log=log)
    if not ok:
        return False, False, why
    if not drv.focus_app(_PROCS, _TITLE):
        return False, False, "Discord aage nahi aaya, isliye kuch type nahi kiya"
    mw = _main_window()
    if not mw:
        return False, False, "Discord ki window nahi mili"
    hwnd = mw[0]
    v, how = dm_is_open(hwnd, mw[2], receiver)
    if v:
        _log(player, f"[call] Discord: '{receiver}' ka DM pehle se khula hai ({how})")
        return True, True, how
    drv.hotkey("ctrl", "k")              # Discord's documented Quick Switcher
    time.sleep(0.8)
    if not _quick_switcher_focused(hwnd):
        drv.press("esc")
        return False, False, ("Discord ka Quick Switcher (Ctrl+K) nahi khula, isliye naam "
                              "type nahi kiya (galat chat mein na chala jaye)")
    drv.hotkey("ctrl", "a")
    drv.paste(receiver)
    time.sleep(1.5)
    drv.press("enter")
    time.sleep(1.8)
    mw = _main_window() or mw
    v, how = dm_is_open(mw[0], mw[2], receiver)
    _log(player, f"[call] Discord: Ctrl+K se '{receiver}' khola - check: "
                 f"{'OK, ' + how if v else 'FAIL, ' + how}")
    return True, v, how


def _hangups() -> list:
    out = []
    if not _uia_ok():
        return out
    for hwnd, _pid, _t in _windows():
        out += uia.find(hwnd, _HANGUP_RX, types=_BTN)
    return out


def _press_call_button(hwnd, video: bool) -> tuple:
    rx = _VIDEO_RX if video else _VOICE_RX
    hits = uia.find(hwnd, rx, types=_BTN, region=_header_region(hwnd))
    if not hits:
        r = _rect(hwnd)
        reg = (r[0], r[1], r[2], r[1] + int((r[3] - r[1]) * 0.30)) if r else None
        hits = uia.find(hwnd, rx, types=_BTN, region=reg)
    if not hits:
        return False, "button not found"
    hits.sort(key=lambda e: (e.top, -e.left))
    return hits[0].invoke(), f"'{hits[0].name}' button"


def _prepare(player, receiver: str, call_type: str, go_ahead: bool = False) -> None:
    kind = "video call" if call_type == "video" else "call"
    try:
        ok, verified, how = open_dm(player, receiver)
        if not ok:
            raise RuntimeError(how)
        if not verified:
            _PENDING.clear()
            raise RuntimeError(f"Discord mein DM khula par pakka nahi ki wo {receiver} ka hai "
                               f"({how}), isliye call nahi lagaunga - screen pe {receiver} "
                               f"ka DM khol ke phir bolo")
        busy = bool(_hangups())
        _PENDING.clear()
        _PENDING.update(receiver=receiver, call_type=call_type, at=time.monotonic(), busy=busy)
        _log(player, f"[call] Discord: '{receiver}' ka DM khola (check: {how}) - call abhi NAHI lagayi")
        if go_ahead and not busy:
            # update 13: his command was the go-ahead - same confirm step, no question
            _log(player, f"[call] Discord: go-ahead pehle se tha - bina pooche {kind} laga raha hoon")
            result = _confirm(player)
            _log(player, f"[call] Discord: go-ahead call -> {result[:120]}")
            _say(player, "[CALL_NO_ASK] His command was the go-ahead, so nothing is asked. "
                         "Tell the user in ONE short Hinglish line what this result says "
                         "happened, no question: " + result)
            return
        q = f"{receiver} ko Discord pe {kind} lagaun?"
        if busy:
            # update 13: the one case a direct command still gets its one question -
            # a new call would drop the call he is already in
            q = f"aap pehle se ek Discord call/voice mein ho. {q}"
        _log(player, f"[call] Discord: ek hi sawaal - {q}")
        _say(player, f"[CALL_CHECK] Nothing is ringing yet. Ask the user exactly ONE short "
                     f"Hinglish question, this one: '{q}' - nothing else. Any yes (haan / ha / "
                     f"ok / lagao / laga do / kar do) -> call discord_call with "
                     f"action='confirm' at once.")
    except Exception as exc:
        _log(player, f"[call] Discord: {exc}")
        _say(player, f"Tell the user in one short Hinglish sentence that the Discord "
                     f"{kind} to {receiver} was NOT started because: {exc}.")


def _confirm(player) -> str:
    p = dict(_PENDING)
    if not p or time.monotonic() - p.get("at", 0) > _TTL:
        _PENDING.clear()
        return outcome.failed("discord_call", "no Discord call is waiting for a yes - call "
                              "discord_call with action='call' first")
    video = p["call_type"] == "video"
    kind = "video call" if video else "voice call"
    if not _uia_ok():
        return outcome.failed("discord_call", f"I cannot read Discord's buttons on this PC, so "
                              f"the {kind} was NOT started", to=p["receiver"])
    mw = _main_window()
    if not mw or not drv.focus_app(_PROCS, _TITLE):
        return outcome.failed("discord_call", "Discord window could not be brought to the "
                              "front, so the call was NOT started", to=p["receiver"])
    v, how = dm_is_open(mw[0], mw[2], p["receiver"])
    if not v:
        _PENDING.clear()
        return outcome.failed("discord_call", f"{p['receiver']}'s DM is no longer the open "
                              f"chat in Discord ({how}), so the call was NOT started",
                              to=p["receiver"])
    before = {(e.rid, e.left, e.top) for e in _hangups()}
    pressed, bhow = _press_call_button(mw[0], video)
    if not pressed:
        return outcome.failed("discord_call", f"Discord's '{'Start Video Call' if video else 'Start Voice Call'}' "
                              f"button was not found ({bhow}) - NOT started; he can click the "
                              f"call icon at the top of {p['receiver']}'s DM", to=p["receiver"])
    _PENDING.clear()
    _log(player, f"[call] Discord: {bhow} dabaya - {p['receiver']}")
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        now = {(e.rid, e.left, e.top) for e in _hangups()}
        if now - before:
            _STATE["last_call_at"] = time.time()
            _log(player, f"[call] Discord {kind} lag rahi hai - {p['receiver']}")
            return outcome.done("discord_call", to=p["receiver"], call=kind,
                                note="Disconnect control appeared - call started")
        time.sleep(0.5)
    return outcome.unverified("discord_call", f"{bhow} was pressed but no Disconnect / call "
                              f"panel was seen - he should check the screen",
                              to=p["receiver"], call=kind)


def _end(player=None) -> str:
    if not _uia_ok():
        return outcome.failed("discord_call", "I cannot read Discord's buttons on this PC, so "
                              "I could not end the call - tell him to press Disconnect")
    hits = _hangups()
    hits.sort(key=lambda e: 0 if "disconnect" in e.name.lower() else 1)
    for e in hits:
        if e.invoke():
            time.sleep(1.5)
            _log(player, f"[call] Discord: '{e.name}' dabaya")
            if not _hangups():
                return outcome.done("discord_call", call="ended")
            return outcome.unverified("discord_call", f"'{e.name}' was pressed; Discord's call "
                                      f"controls are still on screen")
    return outcome.failed("discord_call", "no Discord call with a Disconnect button was found "
                          "(if a WhatsApp call is on, use whatsapp_call action='end')")


def call_active() -> bool:
    return bool(_hangups())


def discord_call(parameters=None, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "call").lower().strip()
    receiver = str(p.get("receiver") or "").strip()
    ct = str(p.get("call_type") or "voice").lower()
    go_ahead = str(p.get("go_ahead") or "").lower() in ("1", "true", "yes")   # update 13
    call_type = "video" if "video" in ct else "voice"
    if drv is None:
        return outcome.failed("discord_call", "the desktop driver is not available")
    if action in ("end", "hangup", "hang_up", "cut", "stop", "disconnect"):
        return _end(player)
    if action in ("confirm", "yes", "dial") or str(p.get("confirm") or "").lower() in ("1", "true", "yes"):
        if _PENDING:
            return _confirm(player)
        return outcome.failed("discord_call", "no Discord call is waiting for a yes - call "
                              "discord_call with action='call' first")
    if not receiver:
        return outcome.failed("discord_call", "no person was named to call")
    if not drv.can_drive():
        return outcome.failed("discord_call", "keyboard control (pyautogui) is not installed")
    if not _uia_ok():
        return outcome.failed("discord_call", "I cannot read Discord's buttons on this PC "
                              "(Windows UI Automation unavailable), so I will not start a call "
                              "blind - he can call from Discord himself", to=receiver)
    _log(player, f"[call] Discord {call_type} call -> {receiver}: DM khol raha hoon "
                 + ("(go-ahead: bina pooche lagayega)" if go_ahead else "(ek baar poochega)"))
    threading.Thread(target=_prepare, args=(player, receiver, call_type),
                     kwargs={"go_ahead": go_ahead}, daemon=True, name="dc-call").start()
    return outcome.started("discord_call", to=receiver, call=call_type,
                           note=("opening the DM and calling (his command was the go-ahead) - "
                                 "say only that, ask nothing" if go_ahead else
                                 "opening the DM - say only that; the ONE call question "
                                 "arrives by itself"))


TOOL = {
    "name": "discord_call",
    "description": (
        "DISCORD voice or video CALL from the Discord desktop app - ONLY when he names "
        "Discord. 'Rahul ko Discord pe call karo', 'Discord pe Rahul ko video call lagao' "
        "-> action='call', receiver, call_type='voice'|'video' (default voice). A direct "
        "command ('call karo / call lagao / laga do') IS the go-ahead: pass go_ahead=true "
        "and it opens and checks Rahul's DM and rings with NO question. Without go_ahead "
        "it gives you ONE question ('Rahul ko Discord pe call lagaun?'); on any yes call "
        "again with action='confirm'. 'call kaat do' / 'call band "
        "karo' while a DISCORD call is on -> action='end' (no question). Calls without the "
        "word Discord go to whatsapp_call. For a text message use send_message."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "call (default) | confirm | end"},
            "receiver": {"type": "STRING", "description": "Discord name / username of the person"},
            "call_type": {"type": "STRING", "description": "voice (default) | video"},
            "go_ahead": {"type": "BOOLEAN", "description": "true for a direct call command - calls with no question"},
        },
    },
    "handler": discord_call,
}


# update 17: never the fast (cached) window read here (it blocked ~30 s inside
# Windows on his PC for WhatsApp) - the old reader, with a hard time limit.
if uia is not None and hasattr(uia, "old_reader_fn"):
    _prepare = uia.old_reader_fn(_prepare)
    _confirm = uia.old_reader_fn(_confirm)
    _end = uia.old_reader_fn(_end)
    call_active = uia.old_reader_fn(call_active)
