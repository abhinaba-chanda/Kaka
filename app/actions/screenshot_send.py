"""
actions/screenshot_send.py - Kaka update 19: "screenshot lo",
"screenshot lo aur Didi ko WhatsApp pe bhejo". Logic: core/kaka_shot.py.

Update-13 rule: ONE question ("Didi ko ye screenshot WhatsApp pe bhej doon?")
before anything goes out - none when his words already said "abhi bhej do /
seedha bhejo / bina pooche" (go_ahead=true).
"""
import os
import time

_PENDING: dict = {}          # screenshot taken, waiting for his haan
_TTL = 300.0


def screenshot_send(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_shot as K
        from core import kaka_services as ks
    except Exception as e:
        return f"Screenshot load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "take").lower().strip()
    receiver = str(p.get("receiver") or "").strip()
    mode = "window" if str(p.get("target") or "").lower() in ("window", "ye window") else "screen"
    go_ahead = str(p.get("go_ahead", "")).lower() in ("1", "true", "yes")
    confirm = str(p.get("confirm_send", "")).lower() in ("1", "true", "yes")
    try:
        if action == "confirm_enter":
            return K.confirm_enter(player)
        if action == "send" and confirm:
            pend = dict(_PENDING)
            if not pend or time.monotonic() - pend.get("at", 0) > _TTL:
                return ("Bhejne ke liye koi screenshot ruka nahi hai - phir se 'screenshot lo aur "
                        "<naam> ko bhejo' bolo.")
            if receiver and receiver.lower() != pend["receiver"].lower():
                pend["receiver"] = receiver
            _PENDING.clear()
            K.send_async(player, pend["receiver"], pend["path"])
            return (f"{pend['receiver']} ko screenshot bhej raha hoon - bas itna bolo, kuch mat poocho; "
                    "result khud aayega.")
        path, info = K.take(mode)
        if not path:
            return f"Screenshot nahi le paya: {info.get('error')}"
        name = os.path.basename(path)
        what = "window" if info.get("mode") == "window" else "poori screen"
        note = f" ({info['note']})" if info.get("note") else ""
        if action != "send" or not receiver:
            if action == "send" and not receiver:
                _PENDING.clear()
                _PENDING.update(path=path, receiver="", at=time.monotonic())
                return (f"Screenshot le liya ({what}, {name}). Poocho kisko bhejna hai, phir "
                        "action=send ke saath naam bhejo.")
            ks.timing("screenshot take", time.monotonic() - t0, what)
            return f"Screenshot le liya - {what}{note}, saved: Screenshots\\{name}."
        if go_ahead:
            K.send_async(player, receiver, path)
            return (f"Screenshot le liya, {receiver} ko WhatsApp pe bhej raha hoon (usne pehle hi "
                    "bola tha) - kuch mat poocho, result khud aayega.")
        _PENDING.clear()
        _PENDING.update(path=path, receiver=receiver, at=time.monotonic())
        return (f"[ASK_ONCE] Screenshot le liya ({what}, saved). Ab ek hi sawaal poocho: "
                f"\"{receiver} ko ye screenshot WhatsApp pe bhej doon?\" Haan pe isi tool ko "
                f"action=send, receiver='{receiver}', confirm_send=true ke saath call karo. "
                "Na pe kuch mat bhejo.")
    except Exception as e:
        return f"Screenshot mein dikkat aayi: {e}"


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "screenshot_send",
    "description": (
        "Take a screenshot and SAVE it (F:\\Kaka\\Data\\Screenshots), optionally send it on WhatsApp. "
        "'screenshot lo' -> action=take; 'ye window ka screenshot' -> target=window; "
        "'screenshot lo aur Didi ko WhatsApp pe bhejo' -> action=send receiver='Didi' (it asks him "
        "once; go_ahead=true only if he said 'abhi bhej do / seedha bhejo / bina pooche'); after his "
        "haan -> action=send confirm_send=true. action=confirm_enter only when it said the chat name "
        "could not be read and he then said yes. For 'screen pe kya hai' use screen_explain instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["take", "send", "confirm_enter"]},
            "target": {"type": "STRING", "enum": ["screen", "window"],
                       "description": "screen (default) or the window in front"},
            "receiver": {"type": "STRING", "description": "WhatsApp contact name, e.g. 'Didi'"},
            "go_ahead": {"type": "BOOLEAN", "description": "his command already said send without asking"},
            "confirm_send": {"type": "BOOLEAN", "description": "true after his haan to the one question"},
        },
        "required": [],
    },
    "handler": screenshot_send,
}
