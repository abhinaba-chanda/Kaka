"""
actions/clipboard_history.py - Kaka update 19: "jo copy kiya tha woh batao".

The tool Gemini calls; the listener and the filters live in
core/kaka_clipboard.py (started in the background 2 s after Kaka opens).
"""
import time

from core import kaka_services as _ks


def _start_clipboard():
    from core import kaka_clipboard as C
    C.start()


_start_clipboard.__name__ = "clipboard_start"
_ks.on_start(_start_clipboard)


def _short(t: str, n: int = 300) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n] + "..."


def clipboard_history(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_clipboard as C
        from core import kaka_services as ks
    except Exception as e:
        return f"Clipboard yaad load nahi hui: {e}"
    p = parameters or {}
    action = str(p.get("action") or "get").lower().strip()
    try:
        n = int(p.get("index") or 1)
    except Exception:
        n = 1
    try:
        if action == "clear":
            have = len(C.items())
            if not have:
                return "Clipboard yaad pehle se khaali hai."
            if not (p.get("go_ahead") or p.get("confirm")):
                return (f"[ASK_ONCE] Ek baar poocho: \"Clipboard ki saari {have} copy bhool jaun?\" "
                        "Haan pe isi tool ko action=clear aur go_ahead=true ke saath call karo.")
            k = C.clear()
            ks.timing("clipboard clear", time.monotonic() - t0)
            return f"Clipboard yaad saaf: {k} copy hata di. (Windows ka abhi wala clipboard waisa hi hai.)"
        items = C.items()
        if not items:
            return ("Abhi koi copy yaad nahi hai (Kaka chalu hone ke baad ka text hi yaad rehta hai; "
                    "password/OTP jaisa kuch yaad nahi rakhta).")
        if action == "list":
            lines = [f"{i}. {_short(x['text'], 80)}" for i, x in enumerate(items[:10], 1)]
            ks.popup("Clipboard (last copies)", "\n".join(f"{i}. {_short(x['text'], 400)}"
                                                          for i, x in enumerate(items, 1)))
            ks.timing("clipboard list", time.monotonic() - t0)
            return f"{len(items)} copy yaad hain, sabse nayi pehle: " + " | ".join(lines)
        it = C.get(n)
        if it is None:
            return f"Sirf {len(items)} copy yaad hain; {n} number wali nahi hai."
        if action in ("copy_back", "copy", "restore"):
            ok = _set_text(it["text"])
            ks.timing("clipboard copy_back", time.monotonic() - t0)
            return ("Wapas clipboard pe daal diya, ab Ctrl+V se paste kar lo." if ok else
                    "Clipboard pe wapas daal nahi paya - text popup mein dikha diya.")
        ks.popup(f"Copy #{n}", it["text"][:4000])
        ks.timing("clipboard get", time.monotonic() - t0, f"#{n}")
        extra = " (poora text HUD pe dikha diya)" if len(it["text"]) > 300 else ""
        which = "Sabse nayi copy" if n == 1 else f"{n} number wali copy"
        return f"{which}: \"{_short(it['text'])}\"{extra}. Isko padh ke sunao."
    except Exception as e:
        return f"Clipboard yaad mein dikkat aayi: {e}"


def _set_text(text: str) -> bool:
    try:
        from core import kaka_services as ks
        from core import kaka_clipboard as C
        C.add(text, own=True)
        done = {"ok": False}

        def _do():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            done["ok"] = True
        if ks.run_in_ui(_do):
            return True
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "clipboard_history",
    "description": (
        "His last ~20 COPIED texts (Ctrl+C), newest first; passwords/OTP/keys are never kept. "
        "'jo copy kiya tha woh batao' / 'pichhla copy padho' -> action=get index=1 (latest); "
        "'usse pehle wala' -> index=2; '3 copy pehle wala' -> index=3; 'saari copies dikhao' -> list; "
        "'woh wala wapas copy karo' -> copy_back; 'clipboard saaf karo' -> clear (asks once, "
        "go_ahead=true after his haan). Read the returned text aloud."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["get", "list", "copy_back", "clear"],
                       "description": "get (default), list, copy_back, clear"},
            "index": {"type": "INTEGER", "description": "1 = latest copy, 2 = the one before, ..."},
            "go_ahead": {"type": "BOOLEAN", "description": "true after he said haan to clearing"},
        },
        "required": [],
    },
    "handler": clipboard_history,
}
