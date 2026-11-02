import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from core import outcome
from core.lazyimport import LazyModule, installed
try:
    from core import speed as _speed
except Exception:            # pragma: no cover
    _speed = None

# Imported on first USE, not at launch — see core/lazyimport.py. PAUSE is 0.06
# here rather than computer_control's 0.05, as it always was: typing into
# somebody else's chat window wants a little more room than a hotkey does.
_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")


def _configure_pyautogui(module) -> None:
    module.FAILSAFE = True
    module.PAUSE    = 0.06


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else: 
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return outcome.failed(
            "send_message", f"{app_name} could not be opened, so nothing was "
            f"typed and no message was sent",
            platform=app_name, to=receiver,
        )

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    # UNVERIFIED, AND THAT IS THE HONEST ANSWER.
    #
    # Everything above is keystrokes with sleeps between them: press Win, type
    # the app name, Ctrl+F, type the contact, Enter, paste, Enter. Nothing
    # reads back which window received any of it. On a machine one second
    # slower than these sleeps assume, the whole sequence lands somewhere else
    # — and the old code still returned "Message sent to X via Y."
    #
    # There is no read-back to add here without a driver per application, and
    # `_send_whatsapp` shows what that costs to build. Until one exists for a
    # given app, the truthful thing is to say the keystrokes went out and the
    # result is unconfirmed, which is something the user can glance at their
    # screen and check.
    return outcome.unverified(
        "send_message",
        f"the message was typed into {app_name} blind - no confirmation is "
        f"available for this app, so it is NOT known to have been sent",
        platform=app_name, to=receiver,
    )

def _send_whatsapp(receiver: str, message: str) -> str:
    """WhatsApp goes through the verified driver when there is one.

    _desktop_send below is a sequence of keystrokes with sleeps between them:
    it presses Win, types the app name, presses Ctrl+F, types the contact,
    presses Enter twice and reports success — without ever reading back which
    window received any of that. When the machine is a second slower than the
    sleeps assume, the whole sequence lands somewhere else and the user is
    still told the message was sent.

    plugins/_whatsapp_core.py already drives WhatsApp properly for the calling
    plugins: it finds the real window, opens the conversation, checks the
    conversation is the right person's, types, and confirms the box emptied
    before it says "sent". Using it here costs nothing and removes the one
    failure that matters — a message reported as delivered that never left.

    The blind path stays as the fallback for a machine where the driver cannot
    run at all (the plugin file removed, a missing dependency, no WhatsApp).
    But once the driver HAS run, its answer stands: falling back after it
    refused to send is how a message ends up typed into the wrong
    conversation, which is worse than not sending it.
    """
    try:
        from plugins import _whatsapp_core as wa
    except Exception as e:
        print(f"[SendMessage] WhatsApp driver unavailable ({e}) — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    try:
        transport, why = wa.get()
    except Exception as e:
        print(f"[SendMessage] WhatsApp driver failed to start ({e}) — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    if transport is None:
        print(f"[SendMessage] {why} — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    sent, failure = transport.send_message_to(receiver, message)
    if sent:
        # The one platform that can say `done`: the driver opened the
        # conversation, proved it was the right person's, typed, and confirmed
        # the box emptied.
        return outcome.done("send_message", platform="WhatsApp",
                            to=receiver, verified=True)
    return outcome.failed("send_message", failure,
                          platform="WhatsApp", to=receiver)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return outcome.failed(
            "send_message", "Instagram could not be opened in the browser, so "
            "nothing was sent", platform="Instagram", to=receiver)

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return outcome.unverified(
        "send_message",
        "the message was typed into Instagram in the browser blind - the page "
        "was never read back, so it is NOT known to have been sent",
        platform="Instagram", to=receiver)


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return outcome.failed(
            "send_message", "Messenger could not be opened in the browser, so "
            "nothing was sent", platform="Messenger", to=receiver)


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return outcome.unverified(
        "send_message",
        "the message was typed into Messenger in the browser blind - the page "
        "was never read back, so it is NOT known to have been sent",
        platform="Messenger", to=receiver)

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)




# KAKA update 8 ----------------------------------------------------------------
# His rule: Kaka ASKS before sending. So a desktop message is done in two
# steps: (1) open the real app (WhatsApp / Discord / Telegram desktop), open the
# chat and TYPE the message - without pressing Enter; (2) only after he says
# "haan, bhej do" the model calls again with confirm_send=true and Enter is
# pressed in that same app.
import re as _re
import urllib.parse as _urlparse

_DESKTOP_APPS = {
    "whatsapp": ("WhatsApp", ("whatsapp", "whatsapp.root"), ("ctrl", "f")),
    "discord": ("Discord", ("discord",), ("ctrl", "k")),
    "telegram": ("Telegram", ("telegram",), ("ctrl", "f")),
}
_PENDING: dict = {}
_PENDING_TTL = 600.0


def _phone(receiver: str) -> str:
    digits = _re.sub(r"[^0-9+]", "", receiver or "")
    if len(_re.sub(r"\D", "", digits)) < 10 or _re.search(r"[A-Za-z]", receiver or ""):
        return ""
    d = _re.sub(r"\D", "", digits)
    if len(d) == 10:
        d = "91" + d            # Indian mobile without country code
    return d


def _platform_key(platform: str) -> str:
    p = (platform or "whatsapp").lower()
    for k in _DESKTOP_APPS:
        if k in p:
            return k
    return ""


def _wa_uia() -> bool:
    try:
        from core import uia as _u
        return _u.available()
    except Exception:
        return False


class _NoCursor(Exception):
    """The chat opened but the cursor could not be put (and checked) in the
    message box - nothing was typed (update 10)."""


def _wa_in_search(f, rect) -> bool:
    """update 11b: is the focused element WhatsApp's chat-list search box?"""
    from core import compose
    from actions import whatsapp_call as _wac
    if f is None:
        return False
    if f.name and _wac._SEARCH_NAME_RX.search(f.name):
        return True
    if f.ctype in _wac._EDITS and rect and not _wac._is_compose(f, rect):
        l, t, r, b = rect
        if f.left < l + (r - l) * 0.5 and f.top < t + (b - t) * 0.35:
            return True                   # a text box up in the chat list = the search
    return False


def _wa_soft_compose(procs, title, message, log, wlog, prefilled=False) -> tuple:
    """update 11b - WhatsApp message box, PROVEN path first.
    Update 7 on his PC: after Enter on the search result WhatsApp itself puts
    the cursor in the message box, and the paste lands there. So:
      - UIA cannot read the focus at all  -> trust WhatsApp, paste;
      - focus is a text box that is not the search box -> fine, paste;
      - UIA says the focus is STILL in the search box -> Tab key, UIA SetFocus,
        click on the box / bottom of the chat, re-check; if it is STILL the
        search box, nothing is typed (the message would land in the search);
      - anything else unclear -> paste anyway; he checks the screen.
    -> (fc, state); state 'ok' (text read back), 'unconfirmed' (pasted, not
    read back), 'prefilled' / 'prefilled_ok' (whatsapp://send link)."""
    from core import compose
    from core import desktop_driver as drv
    _u = getattr(compose, "uia", None)      # the same core.uia module compose uses
    fc = {"ok": False, "method": "", "box": None}

    def focused():
        try:
            return _u.focused_element() if (_u is not None and _wa_uia()) else None
        except Exception:
            return None

    def desc(e):
        if e is None:                         # update 12: say WHY it was unreadable
            g = ""
            try:
                g = _u.giant_note() if (_u is not None and hasattr(_u, "giant_note")) else ""
            except Exception:
                g = ""
            return f"(padh nahi paya: {g})" if g else "(padh nahi paya)"
        return f"{e.ctype or '?'} '{e.name or ''}' @({e.left},{e.top},{e.right},{e.bottom})"

    hwnd = compose.main_window(procs, title) if _wa_uia() else 0
    rect = compose.win_rect(hwnd) if hwnd else None
    if not drv.focus_app(procs, title):
        raise RuntimeError(f"{title} aage nahi aaya, isliye message type nahi kiya")
    f = focused()
    wlog(f"[msg] {title} focus naam+Enter ke baad: {desc(f)}")
    if f is None:
        fc["method"] = "focus padh nahi paya - WhatsApp ne cursor message box mein rakha (Update 7 jaisa)"
    elif f.ctype in compose.EDITABLE and not _wa_in_search(f, rect):
        fc.update(ok=True, method="cursor text box mein (search nahi)", box=f)
    else:
        if _wa_in_search(f, rect):
            wlog(f"[msg] {title}: cursor abhi bhi search box mein - Tab dabake message box pe la raha hoon")
            drv.press("tab")
            time.sleep(0.4)
            f = focused()
            wlog(f"[msg] {title} focus Tab ke baad: {desc(f)}")
        if f is not None and f.ctype in compose.EDITABLE and not _wa_in_search(f, rect):
            fc.update(ok=True, method="Tab key", box=f)
        else:
            r = compose.focus_compose(procs, title, log=log)
            if r.get("ok"):
                fc.update(ok=True, method=r.get("method") or "UIA", box=r.get("box"))
            else:
                f = focused()
                if f is not None and _wa_in_search(f, rect):
                    raise _NoCursor("cursor search box se nahi hata (Tab, UIA SetFocus, click - "
                                    "sab try kiya)")
                fc["method"] = f"cursor check nahi ho saka ({desc(f)}) - Update 7 jaisa paste"
    wlog(f"[msg] {title} message box: {fc['method']}")
    if prefilled:
        have = compose.read_box(fc["box"]) if _wa_uia() else None
        if have is not None and compose.has_text(have, message):
            wlog(f"[msg] {title}: link ne message box mein text bhar diya - check OK")
            return fc, "prefilled_ok"
        if not fc["ok"] or have is None:
            return fc, "prefilled"            # the link filled it; don't paste it twice
    if fc["ok"] and fc["box"] is not None:
        state, _v = compose.type_message(message, fc["box"], log=log, title=title)
        if state != "ok":
            wlog(f"[msg] {title}: text check pakka nahi ({state}) - screen pe dekhna hoga")
            state = "unconfirmed"
        return fc, state
    try:                                      # exactly Update 7: paste where WhatsApp put the cursor,
        drv.paste(message, restore=False)     # text left on the clipboard (update 12)
    except TypeError:
        drv.paste(message)
    # update 14: read the box every 0.1 s; go on as soon as the text is seen
    # (ceiling 0.5 s as before - also when the box cannot be read at all)
    v = None
    t_end = time.monotonic() + 0.5
    while True:
        time.sleep(0.1)
        try:
            v = compose.read_box(None) if _wa_uia() else None
        except Exception:
            v = None
        if (v is not None and compose.has_text(v, message)) or time.monotonic() >= t_end:
            break
    if v is not None and compose.has_text(v, message):
        wlog(f"[msg] {title}: message box mein text dikh gaya - check OK")
        return fc, "ok"
    wlog(f"[msg] {title}: message paste kiya, text check nahi ho saka - screen pe dekhna hoga")
    return fc, "unconfirmed"


def _short_text(message: str, n: int = 40) -> str:
    m = " ".join(str(message or "").split())
    return m if len(m) <= n else m[:n].rstrip() + "..."


def send_question(receiver: str, message: str, checked: bool) -> str:
    """update 13: the ONE question before a message goes - name + content,
    plus a screen hint only when the chat could not be confirmed."""
    q = f"{receiver} ko '{_short_text(message)}' bhej doon?"
    if not checked:
        q += " - screen pe check kar lo"
    return q


def _ask_or_send(player, say, wlog, title: str, receiver: str, message: str,
                 checked: bool, go_ahead: bool) -> None:
    """update 13: ask exactly one question - or, when his command already said
    'abhi bhej do / seedha bhejo / bina pooche', press send with no question."""
    if go_ahead:
        wlog(f"[msg] {title}: go-ahead pehle se tha (abhi/seedha/bina pooche) - "
             f"bina pooche bhej raha hoon")
        result = _confirm_desktop(player)
        wlog(f"[msg] {title}: go-ahead send -> {result[:120]}")
        say("[SENT_NO_ASK] He already said to send it without asking, so nothing is "
            "asked. Tell the user in ONE short Hinglish line what this result says "
            "happened (e.g. '" + receiver + " ko bhej diya'), no question: " + result)
        return
    q = send_question(receiver, message, checked)
    wlog(f"[msg] {title}: ek hi sawaal - {q}")
    say(f"[SEND_CHECK] The message is typed in {title} in the chat opened for "
        f"'{receiver}', NOT sent yet. Ask the user exactly ONE short Hinglish "
        f"question, this one: '{q}' - nothing else, no second question, no separate "
        f"name check. Any yes (haan / ha / yes / ok / kar do / bhej do / theek hai) "
        f"-> call send_message with confirm_send=true at once.")


def _prepare_desktop(player, key: str, receiver: str, message: str,
                     go_ahead: bool = False) -> None:
    from core import desktop_driver as drv
    from core import compose
    title, procs, search_keys = _DESKTOP_APPS[key]

    def say(text):
        if player and hasattr(player, "request_say"):
            try:
                player.request_say(text)
            except Exception:
                pass

    def wlog(text):
        print(f"[SendMessage] {text}")
        if player:
            try:
                player.write_log(text)
            except Exception:
                pass
    chat_checked = None            # True = the chat header shows this person
    try:
        drv.set_log(wlog)          # update 12: focus_app logs which way got the app in front
    except Exception:
        pass
    _tm = _speed.Timer("send_message", wlog) if _speed is not None else None
    try:
        _prepare_inner(player, key, receiver, message, go_ahead, drv, compose, title, procs,
                       search_keys, say, wlog)
    finally:
        if _tm is not None:
            _tm.done()


def _lap(step: str) -> None:
    if _speed is not None:
        _speed.lap(step)


def _prepare_inner(player, key, receiver, message, go_ahead, drv, compose, title, procs,
                   search_keys, say, wlog) -> None:
    chat_checked = None
    try:
        phone = _phone(receiver) if key == "whatsapp" else ""
        log = (lambda s: player.write_log(s)) if player else None
        if phone:
            ok, procs2, why = drv.ensure_app("WhatsApp", log=log, need_front=False)
            if not ok:
                raise RuntimeError(why)
            os.startfile(f"whatsapp://send?phone={phone}&text={_urlparse.quote(message)}")
            time.sleep(2.5)
            drv.focus_app(procs, title)
            chat_checked = True    # the link opens exactly that number's chat
        elif key == "whatsapp":
            # update 11b: the PROVEN Update-7 path (Ctrl+F, name, Enter) with UI
            # Automation only as a bonus. If the chat cannot be confirmed the
            # message is still typed and he is asked to look at the screen.
            # update 17: no "chat pehle se khula?" whole-window check (3.7-34 s
            # on his PC) - always the search; open_chat checks the name with ONE
            # targeted header read and refuses a clearly different chat
            from actions import whatsapp_call as _wac
            ok, chat_checked, why = _wac.open_chat(player, receiver)
            if not ok:
                raise RuntimeError(why)
            chat_checked = bool(chat_checked)
        elif not _wa_uia():
            # update 10: without UI Automation Kaka cannot check where the cursor
            # is, so it types nothing at all (not even the name in the search)
            raise RuntimeError(f"is PC pe Windows UI Automation nahi chal raha, isliye main "
                               f"{title} ke message box mein cursor check nahi kar sakta - "
                               f"kuch type nahi kiya")
        else:
            ok, procs2, why = drv.ensure_app(title, log=log)
            if not ok:
                raise RuntimeError(why)
            if not drv.focus_app(procs, title):
                raise RuntimeError(f"{title} aage nahi aaya, isliye kuch type nahi kiya")
            drv.hotkey(*search_keys)
            time.sleep(0.7)
            drv.hotkey("ctrl", "a")
            drv.paste(receiver)
            time.sleep(1.6)
            drv.press("enter")
            time.sleep(1.4)

        # ---- update 10: cursor in the MESSAGE BOX, checked, before typing
        # ---- update 11b: for WhatsApp the check is a BONUS, never a blocker
        if key == "whatsapp":
            fc, state = _wa_soft_compose(procs, title, message, log, wlog,
                                         prefilled=bool(phone))
            _lap("message box + paste")
        elif phone and not _wa_uia():
            state = "prefilled"            # whatsapp://send put the text in; nothing typed
            fc = {"ok": False, "method": "whatsapp link (text pehle se bhara)", "box": None}
        else:
            fc = compose.focus_compose(procs, title, log=log)
            if not fc["ok"]:
                raise _NoCursor("; ".join(fc.get("tried") or []) or "message box not found")
            if phone:
                have = compose.read_box(fc["box"])
                if have is not None and compose.has_text(have, message):
                    state = "ok"
                    wlog(f"[msg] {title}: link ne message box mein text bhar diya - check OK")
                else:
                    state, _v = compose.type_message(message, fc["box"], log=log, title=title)
            else:
                state, _v = compose.type_message(message, fc["box"], log=log, title=title)
        if state == "missing":
            _PENDING.clear()
            raise RuntimeError(f"maine {title} mein message paste kiya par wo message box mein "
                               f"nahi dikha, isliye bhejne ka nahi poochunga - screen dekh lo")
        _PENDING.clear()
        _PENDING.update(key=key, title=title, procs=procs, receiver=receiver,
                        message=message, at=time.monotonic(), checked=(state in ("ok", "prefilled_ok")),
                        uia=bool(fc.get("ok")))
        wlog(f"[msg] {title}: '{receiver}' ke chat mein message type kiya (abhi bheja NAHI)"
             f" - cursor: {fc.get('method')}, text check: {state}")
        if key == "whatsapp":
            # update 11b: confirmed -> the normal question; not confirmable ->
            # the message is typed anyway and HE checks the screen. Enter only
            # after his clear yes.
            # update 13: ONE question (name + text), the screen hint folded in
            wlog(f"[msg] {title}: chat check: "
                 f"{'OK' if chat_checked else 'nahi ho saka'}, text check: {state}")
            _ask_or_send(player, say, wlog, title, receiver, message,
                         chat_checked is True and state in ("ok", "prefilled_ok"), go_ahead)
            _lap("bhejna / sawaal")
            return
        _ask_or_send(player, say, wlog, title, receiver, message,
                     chat_checked is True and state == "ok", go_ahead)
    except _NoCursor as exc:
        print(f"[SendMessage] no cursor in the message box: {exc}")
        wlog(f"[msg] {title}: message box pe cursor nahi aaya ({exc}) - kuch type NAHI kiya")
        say(f"Tell the user in one short Hinglish line exactly this idea: '{title} mein "
            f"{receiver} ka chat khul gaya par message box pe cursor nahi la paya - ek baar "
            f"box pe click kar do, phir bolo'. Nothing was typed or sent. When he says he "
            f"clicked (ho gaya / ab karo), call send_message again with the SAME receiver "
            f"and message_text.")
    except Exception as exc:
        print(f"[SendMessage] prepare failed: {exc}")
        if player:
            player.write_log(f"[msg] {title}: {exc}")
        say(f"Tell the user in one short Hinglish sentence that the message to {receiver} "
            f"was NOT typed or sent because: {exc}. Do not open a website instead.")


def _confirm_desktop(player) -> str:
    _tm = _speed.Timer("send_message (haan ke baad)",
                       (lambda s: player.write_log(s)) if player else None) \
        if (_speed is not None and _speed.current() is None) else None
    try:
        return _confirm_desktop_inner(player)
    finally:
        if _tm is not None:
            _tm.done()


def _confirm_desktop_inner(player) -> str:
    from core import desktop_driver as drv
    from core import compose
    p = dict(_PENDING)
    if not p or time.monotonic() - p.get("at", 0) > _PENDING_TTL:
        _PENDING.clear()
        return outcome.failed("send_message", "there is no typed message waiting to be "
                              "sent - call send_message again without confirm_send to type it")
    title = p["title"]
    log = (lambda s: player.write_log(s)) if player else None
    try:
        drv.set_log(log)           # update 12
    except Exception:
        pass
    if not drv.focus_app(p["procs"], p.get("title", "")):
        return outcome.failed("send_message", f"{title} window could not be brought "
                              f"to the front, so Enter was NOT pressed", to=p["receiver"])
    # update 14: the 0.4 s settle only when the window had to be brought forward
    if (getattr(drv, "LAST_FOCUS", None) or {}).get("how") != "already in front":
        time.sleep(0.4)
    _lap("foreground")
    if p.get("uia") and _wa_uia():
        # update 10: Enter only with the cursor checked in the message box that
        # still holds this message; then check the box emptied
        fc = compose.focus_compose(p["procs"], title, log=log)
        if not fc["ok"] and p.get("key") == "whatsapp":
            # update 11b: WhatsApp is in front and he has looked at the screen
            # and said yes - Enter goes unless the cursor is clearly in search
            try:
                f = compose.uia.focused_element() if _wa_uia() else None
            except Exception:
                f = None
            hw = compose.main_window(p["procs"], title)
            if f is not None and _wa_in_search(f, compose.win_rect(hw) if hw else None):
                return outcome.failed("send_message", "the cursor is in WhatsApp's search box, "
                                      "not the message box, so Enter was NOT pressed - he can "
                                      "press Enter in the chat himself", to=p["receiver"])
            drv.press("enter")
            _PENDING.clear()
            if player:
                player.write_log(f"[msg] {title}: Enter dabaya - '{p['receiver']}' ko bhej diya (check nahi ho saka)")
            return outcome.unverified("send_message", f"Enter was pressed in {title} on the "
                                      f"typed message to {p['receiver']} - it should be sent; he "
                                      f"can see it on screen", to=p["receiver"], platform=title)
        if not fc["ok"]:
            return outcome.failed("send_message", f"the cursor could not be put back in "
                                  f"{title}'s message box, so Enter was NOT pressed - he can "
                                  f"click the box and say yes again, or press Enter himself",
                                  to=p["receiver"])
        have = compose.read_box(fc["box"])
        if (have is not None and not compose.has_text(have, p["message"])
                and (p.get("key") != "whatsapp" or p.get("checked"))):
            _PENDING.clear()
            return outcome.failed("send_message", f"the message is no longer in {title}'s "
                                  f"message box (changed or already sent), so Enter was NOT "
                                  f"pressed", to=p["receiver"])
        drv.press("enter")
        after = None
        for _ in range(13):                  # update 14: look every 0.15 s (ceiling ~2 s as before)
            time.sleep(0.15)
            after = compose.read_box(fc["box"])
            if after is not None and not compose.has_text(after, p["message"]):
                break
        _PENDING.clear()
        if (after is not None and not compose.has_text(after, p["message"])
                and p.get("key") == "whatsapp" and not p.get("checked")):
            if player:
                player.write_log(f"[msg] {title}: Enter dabaya - '{p['receiver']}' ko bhej diya")
            return outcome.unverified("send_message", f"Enter was pressed in {title} on the "
                                      f"typed message to {p['receiver']} - it should be sent; he "
                                      f"can see it on screen", to=p["receiver"], platform=title)
        if after is not None and not compose.has_text(after, p["message"]):
            if player:
                player.write_log(f"[msg] {title}: Enter dabaya, message box khaali - "
                                 f"'{p['receiver']}' ko bhej diya")
            return outcome.done("send_message", to=p["receiver"], platform=title,
                                verified=True, note="message box emptied after Enter")
        if after is not None:
            if player:
                player.write_log(f"[msg] {title}: Enter dabaya par message abhi bhi box mein hai")
            return outcome.failed("send_message", f"Enter was pressed but the message is still "
                                  f"in {title}'s message box - it was probably NOT sent; he "
                                  f"should look at the screen", to=p["receiver"])
        return outcome.unverified("send_message", f"Enter was pressed in {title}'s message "
                                  f"box; I could not read the box afterwards - he can see it "
                                  f"on screen", to=p["receiver"], platform=title)
    drv.press("enter")
    _PENDING.clear()
    if player:
        player.write_log(f"[msg] {title}: Enter dabaya - '{p['receiver']}' ko bhej diya")
    return outcome.unverified("send_message", f"Enter was pressed in {title} on the "
                              f"typed message to {p['receiver']} - it should be sent; he can "
                              f"see it on screen", to=p["receiver"], platform=title)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()
    confirm_send = str(params.get("confirm_send", "")).lower() in ("1", "true", "yes")
    go_ahead     = str(params.get("go_ahead", "")).lower() in ("1", "true", "yes")   # update 13

    if confirm_send and _PENDING and not message_text:
        return _confirm_desktop(player)
    if confirm_send and _PENDING and (not receiver or receiver == _PENDING.get("receiver")):
        return _confirm_desktop(player)

    if not receiver:
        return outcome.failed("send_message", "no recipient was given")
    if not message_text:
        return outcome.failed("send_message", "no message text was given",
                              to=receiver)
    if not _PYAUTOGUI:
        return outcome.failed(
            "send_message",
            "PyAutoGUI is not installed, so the desktop cannot be driven - "
            "run: pip install pyautogui",
            to=receiver, platform=platform,
        )

    key = _platform_key(platform)
    if key and os.name == "nt":
        if player:
            player.write_log(f"[msg] {platform} → {receiver}: typing "
                             + ("(go-ahead: bina pooche bhejega)" if go_ahead
                                else "(bhejne se pehle ek baar poochega)"))
        threading.Thread(target=_prepare_desktop, args=(player, key, receiver, message_text),
                         kwargs={"go_ahead": go_ahead},
                         daemon=True, name="msg-prepare").start()
        return outcome.started("send_message", platform=platform, to=receiver,
                               note=("typing and sending it (he already said go ahead) - "
                                     "ask nothing" if go_ahead else
                                     "opening the chat and typing it - say only that, ask "
                                     "nothing yet; the ONE send question arrives by itself"))
    if not confirm_send and not go_ahead:
        return outcome.ask("send_message",
                           f"Ask him exactly ONE short question: '{send_question(receiver, message_text, True)}' "
                           f"and on any yes call again with confirm_send=true.",
                           to=receiver, platform=platform)

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")   # the message itself IS the news

    threading.Thread(
        target=_send_and_report,
        args=(player, platform, receiver, message_text),
        daemon=True, name="send-message",
    ).start()

    # Sending opens an application, searches for a person and types — several
    # seconds during which the old code held the whole conversation still. The
    # answer goes out now and the result follows, which is the shape
    # whatsapp_call.py settled on for the same reason.
    return outcome.started("send_message", platform=platform, to=receiver)


def _send_and_report(player, platform: str, receiver: str,
                     message_text: str) -> None:
    """Do the sending, then say what actually happened — including when it did
    not happen, and including when nobody can tell.

    Every path here reports. A tool that says `started` and then goes quiet on
    failure has only moved the false success one step later, which is the whole
    thing this rewrite exists to stop.
    """
    try:
        result = _resolve_platform(platform)(receiver, message_text)
    except Exception as exc:
        print(f"[SendMessage] {type(exc).__name__}: {exc}")
        result = outcome.failed("send_message", f"it failed: {exc}",
                                to=receiver, platform=platform)

    # A prefix test on a fixed vocabulary, not a search for the word "sent".
    # The old check was `"sent" in text and "not sent" not in text`, which is
    # one careless rewording away from putting a tick next to a message that
    # never went.
    verified = result.startswith(f"status={outcome.DONE} ")

    # The full status line goes to the TERMINAL, where a developer reads it.
    # The activity log beside the assistant's own sentences is for the
    # conversation and for things that went wrong — a line reading
    # `status=done action=send_message verified=true` there is machinery
    # showing through, and it was reported as exactly that.
    print(f"[SendMessage] {'✅' if verified else '⚠️'} {result[:160]}")
    if player and outcome.is_problem(result):
        player.write_log(f"[msg] could not confirm the message to {receiver} — "
                         f"see the terminal")

    if verified:
        # It went, and the user asked for it to go. Announcing a success they
        # already expect is noise — the log and the panel carry the record.
        return

    if player and hasattr(player, "request_say"):
        try:
            player.request_say(
                "Tell the user, in one or two short sentences in their own "
                "language, exactly what this says happened — and if it says "
                "the message was not sent or not confirmed, say that plainly "
                "rather than claiming it was sent: " + result
            )
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": (
        "Sends a text message via WhatsApp, Discord, Telegram (their DESKTOP "
        "apps, never the website) or another messaging platform. Write "
        "'message_text' in the USER'S OWN LANGUAGE, exactly what they asked to "
        "be said ('Rahul ko WhatsApp pe bolo main late aaunga' -> receiver=Rahul, "
        "message_text='Main late aaunga'). The first call opens the chat and TYPES the "
        "message without asking anything; then it gives you ONE question with the "
        "name and the text ('Rahul ko 'Main late aaunga' bhej doon?') - ask only that, "
        "and on any yes (haan / ok / bhej do / kar do) call again with "
        "confirm_send=true (same receiver). If his command already said 'abhi bhej "
        "do', 'seedha bhejo', 'bina pooche', 'direct bhej do' -> go_ahead=true: it "
        "types AND sends with no question. A phone number works "
        "as receiver for WhatsApp. If it says the cursor could not reach the "
        "message box, tell him to click the box once; when he says 'ho gaya', "
        "call again with the SAME receiver and message_text. "
        "This tool answers immediately and works in the background. On "
        "status=started say only that you are opening the chat / typing it, never "
        "that it is sent. The result follows separately: status=done means it was "
        "confirmed delivered to the conversation, status=unverified means the "
        "keystrokes went out and NOBODY can confirm it arrived - say exactly "
        "that - and status=failed means it did not go. Never tell the user a "
        "message was sent unless the result said status=done."
    ),
    "tier": "core",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp (default), Discord, Telegram, Instagram..."
            },
            "confirm_send": {
                "type": "BOOLEAN",
                "description": "true ONLY after the message was typed and the user clearly said yes, send it"
            },
            "go_ahead": {
                "type": "BOOLEAN",
                "description": "true when his command itself said to send without asking ('abhi bhej do', 'seedha bhejo', 'bina pooche') - types and sends, no question"
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}


# update 17: the WhatsApp message path never uses the fast (cached) window read
# (it blocked ~30 s on his PC); every whole-window read left has a hard limit.
# The same for the other desktop apps (Telegram / Discord), whose fallbacks
# could reach the cached read too.
def _scoped(fn, key_of):
    import functools

    @functools.wraps(fn)
    def _w(*a, **k):
        try:
            from core import uia as _u
        except Exception:
            _u = None
        if _u is None or not hasattr(_u, "old_reader"):
            return fn(*a, **k)
        try:
            app = "whatsapp" if key_of(a, k) == "whatsapp" else ""
        except Exception:
            app = ""
        with _u.old_reader(app):
            return fn(*a, **k)
    return _w


_prepare_desktop = _scoped(_prepare_desktop, lambda a, k: a[1] if len(a) > 1 else k.get("key", ""))
_confirm_desktop = _scoped(_confirm_desktop, lambda a, k: _PENDING.get("key", ""))
