"""
actions/kaka_persona_mode.py - KAKA v1.1: "funny mode", "strict teacher mode",
"bhai mode", "normal mode". Logic in app/kaka_persona.py.
"""


def persona_mode(parameters=None, player=None) -> str:
    try:
        import kaka_persona as P
    except Exception as e:
        return f"Personality mode load nahi hua: {e}"
    p = parameters or {}
    want = str(p.get("mode") or "").strip()
    if not want or want.lower() in ("status", "kaunsa", "current"):
        cur = P.current()
        return f"Abhi {P.PERSONAS[cur]['label']} mode chal raha hai. Bata do: one line."
    ok, key, voice = P.set_persona(want)
    if not key:
        return ("Ye mode nahi pata. Modes: normal, funny, strict teacher, bhai. "
                "Ek line mein poocho kaunsa chahiye.")
    if not ok:
        return "Mode save nahi ho paya (settings file likh nahi saka). Ek line mein bata do."
    try:
        if player:
            player.write_log(f"SYS: Personality mode -> {P.PERSONAS[key]['label']}")
    except Exception:
        pass
    style = P.PERSONAS[key]["prompt"] or "Back to your normal Kaka style."
    extra = (" The new voice starts from the next session." if voice else "")
    return (f"Mode saved: {P.PERSONAS[key]['label']}. FROM THIS REPLY ON, follow this "
            f"style for the rest of the conversation: {style}{extra} Now confirm the "
            f"switch to him in ONE line, already in the new style.")


TOOL = {
    "name": "persona_mode",
    "description": (
        "Changes Kaka's personality/talking style and remembers it: 'funny mode', "
        "'mazedaar ban jao' (funny), 'strict teacher mode' / 'teacher ban jao' (teacher), "
        "'bhai mode' / 'bhai jaise baat karo' (bhai), 'normal mode' / 'normal ho jao' "
        "(normal), 'kaunsa mode hai' (status). No question first."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {"type": "STRING",
                     "enum": ["normal", "funny", "teacher", "bhai", "status"],
                     "description": "Which personality"},
        },
        "required": ["mode"],
    },
    "handler": persona_mode,
}
