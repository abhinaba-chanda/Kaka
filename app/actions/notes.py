r"""
actions/notes.py - Kaka update 19: his notes by voice ("note karo ...").

Notes are his to-do / jot list, shown on the always-on-top desktop panel
(core/kaka_notes_panel.py); the store is core/kaka_notes.py
(F:\Kaka\Data\notes.json). Facts about him (birthdays, pasand) are a
different tool: kaka_memory.

Import is cheap (no PyQt / ctypes here); everything is imported lazily.
"""
import time

MAX_SPOKEN = 10


def _numbered(items, limit=MAX_SPOKEN) -> str:
    lines = []
    for n, it in enumerate(items[:limit], 1):
        lines.append(f"{n}. {it['text']}" + (" (ho gaya)" if it.get("done") else ""))
    more = len(items) - limit
    if more > 0:
        lines.append(f"... aur {more} (panel mein sab dikh rahe hain)")
    return "\n".join(lines)


def _panel():
    try:
        from core import kaka_notes_panel as P
        return P
    except Exception:
        return None


def _pick(N, p):
    """Matches for remove / done: index first, else words."""
    try:
        idx = int(p.get("index") or 0)
    except Exception:
        idx = 0
    text = str(p.get("text") or "").strip()
    if not idx and text.isdigit() and len(text) < 4:
        idx, text = int(text), ""
    return N.find(idx, "" if idx else text), idx


def notes(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_notes as N
        from core import kaka_services as ks
    except Exception as e:
        return f"Notes system load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "add").strip().lower()
    try:
        if action == "add":
            text = str(p.get("text") or "").strip()
            if not text:
                return "Kya note karna hai? Poocho aur phir action=add, text=<uski baat> ke saath call karo."
            item = N.add(text)
            if not item:
                return "Note save nahi hua - text khaali tha."
            num = 1
            for n, it in enumerate(N.items(), 1):
                if it["id"] == item["id"]:
                    num = n
                    break
            P = _panel()
            tail = ""
            if P is not None and P.is_visible():
                shown = P.show()          # makes sure it is on screen (no-op if it already is)
                if "nahi khul paya" in shown:
                    tail = " (Panel abhi nahi khul paya, par note save hai.)"
                elif ks.in_game_mode():
                    tail = " Game band hote hi notes panel mein dikhega."
                else:
                    tail = " Screen pe notes panel mein dikh raha hai."
            elif P is not None:
                tail = " (Notes panel chhupa hai - 'notes dikhao' bolo.)"
            ks.timing("notes add", time.monotonic() - t0)
            return f"Note save: '{item['text']}' (number {num}).{tail}"

        if action == "list":
            items = N.items()
            ks.timing("notes list", time.monotonic() - t0)
            if not items:
                return "Koi note nahi hai abhi. 'note karo ...' bolke likhwa sakte ho."
            open_n = len([i for i in items if not i.get("done")])
            return (f"{len(items)} notes ({open_n} baaki), newest pehle:\n" + _numbered(items))

        if action in ("show_panel", "hide_panel", "toggle_panel"):
            P = _panel()
            if P is None:
                return "Notes panel load nahi hua - par notes save hain, 'mere notes padho' bolo."
            fn = {"show_panel": P.show, "hide_panel": P.hide, "toggle_panel": P.toggle}[action]
            msg = fn()
            ks.timing(f"notes {action}", time.monotonic() - t0)
            if action == "show_panel":
                items = N.items()
                if items:
                    msg += f"\n{len(items)} notes:\n" + _numbered(items)
                else:
                    msg += " Abhi koi note nahi hai."
            return msg

        if action in ("remove", "done"):
            if not N.items():
                return "Koi note hai hi nahi."
            match, idx = _pick(N, p)
            if not match:
                listing = _numbered(N.items())
                return ("Wo note nahi mila. Ye notes hain:\n" + listing +
                        "\nPoocho kaunsa number.")
            if len(match) > 1:
                names = "; ".join(f"'{m['text']}'" for m in match[:3])
                return (f"Kai notes milte hain: {names}. Poocho kaunsa number "
                        "(panel / 'mere notes padho' wala number), phir index ke saath call karo.")
            it = match[0]
            if action == "done":
                N.set_done(it["id"], True)
                ks.timing("notes done", time.monotonic() - t0)
                return f"Ticked: '{it['text']}' ho gaya."
            if not (p.get("go_ahead") or p.get("confirm")):
                return (f"[ASK_ONCE] Ek baar poocho: \"'{it['text']}' wala note hata doon?\" "
                        "Haan pe isi tool ko action=remove, same index/text aur go_ahead=true ke saath call karo.")
            gone = N.remove(ids=[it["id"]])
            ks.timing("notes remove", time.monotonic() - t0)
            if not gone:
                return "Note hata nahi paya - shayad pehle hi hat chuka hai."
            return f"Note hata diya: '{gone[0]['text']}'. {N.count()} notes bache."

        return "Notes: add, list, remove, done, show_panel, hide_panel, toggle_panel mein se ek bolo."
    except Exception as e:
        return f"Notes mein dikkat aayi: {e}"


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "notes",
    "description": (
        "His notes / to-do jot list, always visible in a small panel on his screen. "
        "'note karo <x>' / 'likh lo <x>' / 'note kar lo' -> action=add, text=<x> (no question). "
        "'mere notes padho' -> list. 'saare notes dikhao' -> show_panel. "
        "'notes dikhao' / 'notes chhupao' -> show_panel / hide_panel. "
        "'note 2 ho gaya' -> done, index=2. 'note 2 hatao' -> remove, index=2 (asks once; "
        "go_ahead=true after his haan). NOT for facts about him like birthdays or pasand "
        "(that is kaka_memory) and NOT for timed reminders (reminder)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["add", "list", "remove", "done",
                                "show_panel", "hide_panel", "toggle_panel"],
                       "description": "add (default), list, remove, done, show_panel, hide_panel, toggle_panel"},
            "text": {"type": "STRING",
                     "description": "Note text for add (his words), or which note by words for remove/done"},
            "index": {"type": "INTEGER", "description": "Note number from the list / panel (1 = newest)"},
            "go_ahead": {"type": "BOOLEAN", "description": "true after he said haan to removing"},
        },
        "required": [],
    },
    "handler": notes,
}


def _register_panel_start():
    """Ask the service desk to start the desktop panel ~2 s after Kaka opens."""
    try:
        from core import kaka_services as ks

        def _start_notes_panel():
            from core import kaka_notes_panel as P
            P.start()
        ks.on_start(_start_notes_panel)
    except Exception:
        pass


_register_panel_start()
