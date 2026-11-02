r"""
actions/discord_remote.py - Kaka update 20 (feature 29): "discord remote on / off /
status". The bot itself lives in core/kaka_discord.py (background thread,
started 2 s after Kaka opens when a token exists).
"""
import time

from core import kaka_discord as _kd     # light: stdlib only; registers the starter


def discord_remote(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    D = _kd
    a = str((parameters or {}).get("action") or "status").lower().strip()
    try:
        if a in ("on", "start", "chalu"):
            D.save_settings(enabled=True)
            if not D.token():
                D._popup("Discord remote", D.SETUP_STEPS)
                return ("Discord remote on hai, par token nahi hai - API KEYS mein DISCORD BOT "
                        "TOKEN daalo. Steps screen pe dikha diye.")
            D.start()
            return "Discord remote on - ab phone se Discord pe Kaka bot ko DM karo."
        if a in ("off", "stop", "band"):
            D.save_settings(enabled=False)
            D.stop_bot(timeout=2.0)
            return "Discord remote band kar diya. 'Discord remote on' bolo to wapas chalu."
        if a in ("setup", "steps", "help"):
            D._popup("Discord remote", D.SETUP_STEPS)
            return "Discord bot banane ke steps screen pe dikha diye (7 steps)."
        line = D.status_line()
        if not D.token():
            D._popup("Discord remote", "Discord token nahi hai - API KEYS mein daalo.\n\n" + D.SETUP_STEPS)
        return line
    except Exception as e:
        return f"Discord remote mein dikkat: {e}"
    finally:
        try:
            from core import kaka_services as ks
            ks.timing("discord remote", time.monotonic() - t0, a)
        except Exception:
            pass


TOOL = {
    "name": "discord_remote",
    "description": ("Phone se Kaka chalane wala Discord BOT (remote control), NOT a Discord call. "
                    "'discord remote on/chalu' -> on; 'discord remote band/off' -> off; "
                    "'discord remote chal raha hai?' / 'discord bot status' -> status; "
                    "'discord bot kaise banaun' -> setup. No question."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["on", "off", "status", "setup"]}},
        "required": ["action"]},
    "handler": discord_remote,
}
