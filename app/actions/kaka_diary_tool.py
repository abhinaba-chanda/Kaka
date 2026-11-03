"""
actions/kaka_diary_tool.py - KAKA v1.1: his dated diary by voice.
Logic + store: app/kaka_diary.py (Data/diary.json, local only).
"""
import time


def kaka_diary(parameters=None, player=None) -> str:
    try:
        import kaka_diary as D
        from core import kaka_services as ks
    except Exception as e:
        return f"Diary load nahi hui: {e}"
    t0 = time.monotonic()
    p = parameters or {}
    action = str(p.get("action") or "add").strip().lower()
    text = str(p.get("text") or "").strip()
    when = str(p.get("when") or "").strip()
    try:
        if action in ("add", "write", "likho"):
            if not text:
                return "Diary mein kya likhna hai, wo bolo."
            e = D.add(text)
            if not e:
                return "Diary mein save nahi ho paya (Data folder mein likh nahi saka)."
            ks.timing("diary add", time.monotonic() - t0)
            return "Diary mein likh diya (" + e["date"] + " " + e["time"] + "). One short line."
        if action in ("read", "search", "find", "list"):
            if when and D.parse_when(when) is None:
                return "Kaunsa din, wo samajh nahi aaya (aaj / kal / parso / 5 November bolo)."
            items = D.search(text, when) if (text or when) else D.recent(5)
            if not items:
                what = (f"'{text}' " if text else "") + (f"{when} " if when else "")
                return f"Diary mein {what}kuch nahi mila.".replace("  ", " ")
            lines = [D.fmt(e) for e in items[-10:]]
            ks.popup("Diary", "\n".join(lines))
            more = len(items) - 10
            return (f"{len(items)} entry mili. Read them to him naturally: " + " | ".join(lines)
                    + (f" (aur {more} purani, screen pe)" if more > 0 else ""))
        if action in ("delete", "remove", "hatao"):
            items = D.search(text, when) if (text or when) else D.recent(1)
            if not items:
                return "Aisi koi diary entry nahi mili."
            if len(items) > 1 and not p.get("all"):
                return ("[ASK_ONCE] Ek se zyada entry milti hain: "
                        + " | ".join(D.fmt(e) for e in items[-4:])
                        + ". Ek line mein poocho kaunsi delete karni hai (ya sab).")
            if not p.get("go_ahead"):
                desc = D.fmt(items[-1]) if len(items) == 1 else f"{len(items)} entries"
                return (f"[ASK_ONCE] Ek baar poocho: \"Diary se '{desc}' delete kar doon?\" "
                        "Haan pe same action=delete, same text/when aur go_ahead=true"
                        + (", all=true" if len(items) > 1 else "") + ".")
            n = D.remove([e["id"] for e in items])
            return f"Diary se {n} entry delete kar di." if n else "Delete nahi ho payi."
    except Exception as e:
        return f"Diary mein dikkat: {e}"
    return "Diary: add, read ya delete bolo."


TOOL = {
    "name": "kaka_diary",
    "description": (
        "His dated DIARY / journal (separate from the quick notes panel): "
        "'diary mein likho aaj gym gaya' -> action=add, text=<his words>. "
        "'kal kya likha tha' / 'kal ki diary padho' -> action=read, when=kal. "
        "'diary padho' -> action=read. 'diary mein Rahul dhoondo' -> action=read, text=Rahul. "
        "'5 November ki diary' -> when='5 November'. 'kal wali diary entry delete karo' -> "
        "action=delete (asks once; go_ahead=true only after his haan). "
        "Use the notes tool for to-do jots ('note karo')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["add", "read", "delete"]},
            "text": {"type": "STRING", "description": "Entry text (add) or keywords (read/delete)"},
            "when": {"type": "STRING", "description": "aaj / kal / parso / '5 November' / YYYY-MM-DD"},
            "go_ahead": {"type": "BOOLEAN", "description": "true only after he said haan to deleting"},
            "all": {"type": "BOOLEAN", "description": "delete all matching entries (after his haan)"},
        },
        "required": [],
    },
    "handler": kaka_diary,
}
