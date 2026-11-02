r"""
core/kaka_shot.py - Kaka update 19: screenshot (saved) + send it on WhatsApp.

take(mode)        full screen (default) or the window in front ("ye window"),
                  saved as F:\Kaka\Data\Screenshots\kaka-YYYYMMDD-HHMMSS.png
send(player,...)  the Update-17 WhatsApp path, reused, NOT changed:
                  whatsapp_call.open_chat (Ctrl+F, name, Enter + the targeted
                  header read that refuses a clearly different chat) ->
                  PNG on the clipboard (CF_DIB + "PNG") -> Ctrl+V -> wait for
                  WhatsApp's image preview -> Enter.
                  Wrong chat -> nothing pasted, nothing sent, he hears why.
                  Name not readable -> pasted, NOT sent: he checks the screen
                  and says "haan bhej do" (confirm_enter).
Every step logs a [time] line. Nothing here raises.
"""
from __future__ import annotations

import io
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from core import kaka_services as ks

_IS_WIN = sys.platform == "win32"
PREVIEW_WAIT = 4.0          # s - WhatsApp's image preview must show up within this
_PENDING: dict = {}         # pasted but not sent (name could not be read)
_PENDING_TTL = 180.0
_CAPTION_RX = re.compile(r"caption|\u0915\u0948\u092a\u094d\u0936\u0928|\u0995\u09cd\u09af\u09be\u09aa\u09b6\u09a8",
                         re.I)


# ------------------------------------------------------------------ capture + save
def shots_dir():
    return ks.data_path("Screenshots", "x").parent


def take(mode: str = "screen") -> tuple:
    """-> (path|None, info). PNG at full resolution."""
    t0 = time.monotonic()
    info = {"mode": "screen", "title": "", "grabber": "", "error": ""}
    try:
        from core import kaka_screen as S
    except Exception as e:
        info["error"] = f"screenshot module load nahi hua: {e}"
        return None, info
    try:
        screen = S._virtual_screen()
    except Exception as e:
        info["error"] = f"screen size nahi mila: {e}"
        return None, info
    rect = screen
    if mode == "window":
        try:
            hit = S.pick_window(S._windows_in_zorder(), screen)
        except Exception:
            hit = None
        if hit:
            info["mode"], info["title"], rect = "window", hit[0], hit[1]
        else:
            info["note"] = "window nahi mili, poori screen li"
    png, errs = None, []
    for name, fn in S.GRABBERS:
        try:
            png = fn(rect)
            if png:
                info["grabber"] = name
                break
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}: {str(e)[:60]}")
    if not png:
        info["error"] = "screenshot nahi le paaya (" + "; ".join(errs)[:200] + ")"
        return None, info
    path = shots_dir() / f"kaka-{datetime.now():%Y%m%d-%H%M%S}.png"
    n = 1
    while path.exists():
        n += 1
        path = shots_dir() / f"kaka-{datetime.now():%Y%m%d-%H%M%S}-{n}.png"
    try:
        path.write_bytes(png)
    except Exception as e:
        info["error"] = f"screenshot save nahi hua: {e}"
        return None, info
    info["bytes"] = len(png)
    info["size"] = f"{rect[2] - rect[0]}x{rect[3] - rect[1]}"
    ks.timing("screenshot", time.monotonic() - t0, f"{info['mode']}, {info['grabber']}")
    return str(path), info


# ------------------------------------------------------------------ clipboard image
def dib_from_png(png: bytes) -> Optional[bytes]:
    """PNG -> CF_DIB bytes (BMP without its 14-byte file header)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        bio = io.BytesIO()
        im.save(bio, "BMP")
        return bio.getvalue()[14:]
    except Exception:
        return None


def set_clipboard_image(png: bytes) -> tuple:
    """Windows clipboard <- CF_DIB (+ registered "PNG"). -> (ok, why)."""
    if not _IS_WIN:
        return False, "Windows nahi hai"
    dib = dib_from_png(png)
    if not dib:
        return False, "image convert nahi hui (PIL nahi mila)"
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.RegisterClipboardFormatW.restype = wintypes.UINT
        u32.OpenClipboard.argtypes = [wintypes.HWND]

        def _put(fmt, data):
            h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
            if not h:
                return False
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(fmt, h):
                k32.GlobalFree(h)
                return False
            return True
        opened = False
        for _ in range(10):
            if u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            return False, "clipboard kisi aur program ne pakda hua hai"
        try:
            u32.EmptyClipboard()
            ok = _put(8, dib)                                # CF_DIB
            fmt_png = u32.RegisterClipboardFormatW("PNG")
            if fmt_png:
                _put(fmt_png, png)
        finally:
            u32.CloseClipboard()
        return (True, "") if ok else (False, "clipboard pe image nahi gayi")
    except Exception as e:
        return False, f"clipboard error: {e}"


# ------------------------------------------------------------------ WhatsApp send
def _say(player, text: str) -> None:
    try:
        if player is not None and hasattr(player, "request_say"):
            player.request_say(text)
            return
    except Exception:
        pass
    ks.say_async(text)


def _log(player, text: str) -> None:
    try:
        if player is not None:
            player.write_log(text)
            return
    except Exception:
        pass
    ks.log(text)


def _focus_info(uia) -> tuple:
    """-> (name, value) of the focused element, ('', None) if unreadable (1 s cap)."""
    try:
        f = uia.run_capped(lambda: uia.focused_element(), 1.0, None, "focus read")
        if f is None:
            return "", None
        v = uia.run_capped(lambda: f.value(), 0.8, None, "focus text")
        return str(f.name or ""), v
    except Exception:
        return "", None


def _in_preview(name: str) -> bool:
    return bool(_CAPTION_RX.search(name or ""))


def send(player, receiver: str, path: str) -> str:
    """Runs in a background thread. Returns (and says) what happened."""
    t_all = time.monotonic()
    tag = "[shot]"
    try:
        from actions import whatsapp_call as wa
        from core import desktop_driver as drv
        from core import uia
    except Exception as e:
        msg = f"WhatsApp wala hissa load nahi hua ({e}) - screenshot nahi bheja, file saved hai."
        _say(player, "Tell him in one short Hinglish line: " + msg)
        return msg
    try:
        png = open(path, "rb").read()
    except Exception as e:
        msg = f"screenshot file nahi khuli ({e}), kuch nahi bheja"
        _say(player, "Tell him in one short Hinglish line: " + msg)
        return msg
    scope = uia.old_reader("whatsapp") if hasattr(uia, "old_reader") else None
    try:
        if scope is not None:
            scope.__enter__()
        t0 = time.monotonic()
        ok, verified, why = wa.open_chat(player, receiver)
        ks.timing("shot: chat khola", time.monotonic() - t0,
                  f"{'naam match' if verified else ('naam pakka nahi' if ok else 'nahi khula')}")
        if not ok:
            msg = f"{receiver} ko screenshot nahi bheja: {why}"
            _log(player, f"{tag} {msg}")
            _say(player, "Tell him in one short Hinglish line (nothing was sent): " + msg)
            return msg
        # anything already typed in the message box would go out as the caption
        _n0, v0 = _focus_info(uia) if uia.available() else ("", None)
        if v0 and v0.strip() and not _in_preview(_n0):
            msg = (f"{receiver} ke chat mein pehle se kuch likha hua hai ('{v0.strip()[:30]}'), "
                   f"isliye screenshot paste nahi kiya - pehle wo hata do")
            _log(player, f"{tag} {msg}")
            _say(player, "Tell him in one short Hinglish line (nothing was sent): " + msg)
            return msg
        t1 = time.monotonic()
        cok, cwhy = set_clipboard_image(png)
        if not cok:
            msg = f"screenshot clipboard pe nahi gaya ({cwhy}) - kuch nahi bheja"
            _log(player, f"{tag} {msg}")
            _say(player, "Tell him in one short Hinglish line: " + msg)
            return msg
        time.sleep(0.15)
        if not drv.focus_app(wa._PROCS, "WhatsApp"):
            msg = "WhatsApp aage nahi aaya, screenshot paste nahi kiya"
            _say(player, "Tell him in one short Hinglish line: " + msg)
            return msg
        drv.hotkey("ctrl", "v")
        seen = False
        t_end = time.monotonic() + PREVIEW_WAIT
        time.sleep(0.8)
        while uia.available() and time.monotonic() < t_end:
            n1, _v1 = _focus_info(uia)
            if _in_preview(n1):
                seen = True
                break
            time.sleep(0.3)
        if not uia.available():
            time.sleep(1.2)
        ks.timing("shot: paste + preview", time.monotonic() - t1,
                  "preview dikha" if seen else "preview UIA se nahi dikha")
        if not verified:
            _PENDING.clear()
            _PENDING.update(receiver=receiver, path=path, at=time.monotonic())
            msg = (f"{receiver} ka chat khola aur screenshot paste kiya, par chat ka naam pakka nahi "
                   f"padh paya - abhi bheja NAHI")
            _log(player, f"{tag} {msg}")
            _say(player, f"[SEND_CHECK] {msg}. Ask him exactly ONE short Hinglish question: "
                         f"'Screen pe dekh lo - {receiver} ka chat hai? Bhej doon?' On any yes call "
                         f"screenshot_send with action=confirm_enter.")
            return msg
        return _enter_and_check(player, receiver, seen, t_all, drv, wa, uia)
    except Exception as e:
        msg = f"screenshot bhejne mein dikkat ({e}) - shayad nahi gaya, screen dekh lo"
        _log(player, f"{tag} {msg}")
        _say(player, "Tell him in one short Hinglish line: " + msg)
        return msg
    finally:
        if scope is not None:
            try:
                scope.__exit__(None, None, None)
            except Exception:
                pass


def _enter_and_check(player, receiver, seen, t_all, drv, wa, uia) -> str:
    t2 = time.monotonic()
    drv.press("enter")
    time.sleep(1.0)
    gone = None
    if seen and uia.available():
        n2, _v2 = _focus_info(uia)
        gone = not _in_preview(n2)
    ks.timing("shot: Enter", time.monotonic() - t2,
              "preview band = gaya" if gone else ("check nahi ho saka" if gone is None else "preview abhi bhi khula"))
    ks.timing("shot: total", time.monotonic() - t_all)
    if gone is False:
        msg = f"{receiver} ko Enter dabaya par preview abhi bhi khula hai - screen dekh lo"
        _say(player, "Tell him in one short Hinglish line: " + msg)
        return msg
    msg = f"{receiver} ko screenshot bhej diya" + ("" if gone else " (screen pe ek baar dekh lo)")
    _log(player, f"[shot] {msg}")
    _say(player, "[SENT_NO_ASK] Tell him in ONE short Hinglish line, no question: " + msg)
    return msg


def confirm_enter(player) -> str:
    p = dict(_PENDING)
    _PENDING.clear()
    if not p or time.monotonic() - p.get("at", 0) > _PENDING_TTL:
        return "Koi paste kiya hua screenshot bhejne ke liye ruka nahi hai."
    try:
        from actions import whatsapp_call as wa
        from core import desktop_driver as drv
        from core import uia
    except Exception as e:
        return f"WhatsApp wala hissa load nahi hua: {e}"
    if not drv.focus_app(wa._PROCS, "WhatsApp"):
        return "WhatsApp aage nahi aaya, isliye Enter NAHI dabaya."
    time.sleep(0.3)
    return _enter_and_check(player, p["receiver"], True, time.monotonic(), drv, wa, uia)


def send_async(player, receiver: str, path: str) -> None:
    threading.Thread(target=send, args=(player, receiver, path), daemon=True,
                     name="kaka-u19-shot-send").start()
