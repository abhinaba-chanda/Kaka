r"""
actions/kaka_memory.py - Kaka update 18: "yaad rakhna ...", "kya yaad hai
mere baare mein", "ye bhool jao". Stored in F:\Kaka\Data\memory.json and
given to Kaka at every session start. Logic in core/kaka_facts.py.
"""
import time


def kaka_memory(parameters: dict, player=None) -> str:
    t0 = time.monotonic()
    try:
        from core import kaka_facts as F
        from core import kaka_services as ks
    except Exception as e:
        return f"Memory load nahi hui: {e}"
    p = parameters or {}
    action = str(p.get("action") or "remember").lower()
    try:
        if action in ("day", "conversation", "kal"):   # KAKA v1.1: "kal kya bola tha"
            try:
                import kaka_daylog
                txt = kaka_daylog.recall(str(p.get("when") or p.get("fact") or "kal"))
            except Exception as e:
                txt = f"Purani baatein padh nahi paya: {e}"
            ks.timing("memory day", time.monotonic() - t0)
            return txt + " (Tell him this naturally, in detail if he asks.)"
        if action in ("list", "recall", "show"):
            lines = F.summary_lines()
            ks.timing("memory list", time.monotonic() - t0)
            if not lines:
                return "Abhi tak koi personal baat save nahi hai. 'Yaad rakhna ...' bolo."
            ks.popup("Kaka ko yaad hai", "\n".join("- " + l for l in lines[-40:]))
            return f"{len(lines)} baatein yaad hain: " + "; ".join(lines[-15:])
        if action in ("forget", "bhool"):
            q = str(p.get("fact") or p.get("query") or "").strip()
            if not q or q.lower() in ("ye", "yeh", "this", "that", "wo", "woh", "last"):
                f = F.last()
                match = [f] if f else []
            else:
                match = F.find(q)
            if not match:
                return "Aisi koi baat save nahi mili."
            if len(match) > 1 and not p.get("all"):
                return ("Ek se zyada baatein milti hain: " + "; ".join(m["text"] for m in match[:4])
                        + ". Poocho kaunsi bhoolni hai.")
            if not p.get("go_ahead"):
                return (f"[ASK_ONCE] Ek baar poocho: \"'{match[0]['text']}' bhool jaun?\" "
                        "Haan pe action=forget, same fact aur go_ahead=true.")
            n = F.forget([m["id"] for m in match])
            ks.timing("memory forget", time.monotonic() - t0)
            return f"Bhool gaya ({n})."
        # remember
        fact = str(p.get("fact") or "").strip()
        if not fact:
            return "Kya yaad rakhna hai, wo bolo."
        item = F.add(fact, str(p.get("category") or ""), str(p.get("person") or ""),
                     str(p.get("date") or ""))
        ks.timing("memory add", time.monotonic() - t0)
        if item.get("category") == "birthday":
            if item.get("date"):
                return (f"Yaad rakh liya: {F._bday_text(item)}. Ek line mein offer karo: "
                        "'ek din pehle reminder laga doon?' (khud mat lagao; haan bole toh reminder tool).")
            return "Yaad rakh liya, par date samajh nahi aayi - date bata do."
        return "Yaad rakh liya."
    except Exception as e:
        return f"Memory mein dikkat: {e}"


TOOL = {
    "name": "kaka_memory",
    "description": (
        "His personal facts, saved forever: 'yaad rakhna Didi ka birthday 5 March hai', "
        "'mujhe biryani pasand hai' -> action=remember, fact=<short sentence>. "
        "'kya yaad hai mere baare mein' -> action=list. 'ye bhool jao' / '<x> wali baat bhool jao' "
        "-> action=forget (asks once; go_ahead=true after haan). "
        "'kal kya baat hui thi' / 'kal kya bola tha' / 'parso kya discuss kiya' / 'what did we "
        "talk about yesterday' -> action=day, when=kal / parso / '5 November' / aaj."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["remember", "list", "forget", "day"]},
            "when": {"type": "STRING", "description": "For action=day: kal / parso / aaj / '5 November'"},
            "fact": {"type": "STRING", "description": "The fact in a short sentence (remember) or which one (forget)"},
            "category": {"type": "STRING", "enum": ["birthday", "pasand", "napasand", "general"]},
            "person": {"type": "STRING", "description": "For a birthday: whose (e.g. 'Didi')"},
            "date": {"type": "STRING", "description": "For a birthday: e.g. '5 March'"},
            "go_ahead": {"type": "BOOLEAN"},
        },
        "required": [],
    },
    "handler": kaka_memory,
}
