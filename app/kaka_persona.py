"""KAKA v1.1: personality modes - "funny mode", "strict teacher mode",
"bhai mode", "normal mode" (also Settings > v1.1 FEATURES).

The mode is kept as "persona" in config/api_keys.json (kaka_user.save_setting,
never clobbers anything else). main.py adds prompt_block() to Kaka's
instructions at every session start; a switch by voice also takes effect at
once in the running conversation (the tool result carries the new style).

Voice: Kaka's own Brian / Windows voice has one voice, so only the Gemini
voice (used when the Gemini voice is on) changes with the mode; "normal"
puts back the voice he had picked himself.
"""
from __future__ import annotations

PERSONAS = {
    "normal": {
        "label": "NORMAL",
        "hint": "normal / default",
        "prompt": "",
        "voice": "",
    },
    "funny": {
        "label": "FUNNY",
        "hint": "mazedaar, jokes",
        "prompt": ("PERSONALITY MODE = FUNNY. Be playful and witty: light jokes, funny "
                   "comparisons, desi humour and filmy one-liners woven into the answer, "
                   "but still give the full correct answer and still do every task. "
                   "Never joke about anything serious (health, money loss, errors that "
                   "lost his work) - then be normal."),
        "voice": "Puck",
    },
    "teacher": {
        "label": "STRICT TEACHER",
        "hint": "strict teacher, padhai",
        "prompt": ("PERSONALITY MODE = STRICT TEACHER. Talk like a strict but caring "
                   "teacher: clear, disciplined, explain step by step, correct his "
                   "mistakes directly, push him to study / finish work, end with a short "
                   "task or question to check he understood. No slang, no jokes."),
        "voice": "Kore",
    },
    "bhai": {
        "label": "BHAI",
        "hint": "bhai / dost wali baat",
        "prompt": ("PERSONALITY MODE = BHAI. Talk like his close elder brother / best "
                   "friend: casual Hinglish, 'bhai', 'arre', 'scene', 'tension mat le', "
                   "supportive and honest, hype him up. Still give full, correct answers "
                   "and still ask before any send / delete / buy / post / install."),
        "voice": "Fenrir",
    },
}

ALIASES = {
    "normal": "normal", "default": "normal", "simple": "normal", "regular": "normal",
    "funny": "funny", "comedy": "funny", "mazedaar": "funny", "mazak": "funny",
    "joker": "funny", "fun": "funny", "masti": "funny",
    "teacher": "teacher", "strict": "teacher", "strict teacher": "teacher",
    "strict_teacher": "teacher", "sir": "teacher", "master": "teacher",
    "masterji": "teacher", "padhai": "teacher",
    "bhai": "bhai", "brother": "bhai", "bro": "bhai", "dost": "bhai", "friend": "bhai",
}


def _ku():
    import kaka_user
    return kaka_user


def normalize(name) -> str:
    """Spoken / typed mode name -> one of PERSONAS, or ''."""
    t = str(name or "").strip().lower().replace("-", " ")
    for suffix in (" mode", " wala", " wali", " jaisa", " jaise", " ban jao", " bano"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t in ALIASES:
        return ALIASES[t]
    for word, key in ALIASES.items():
        if word in t.split() or (len(word) > 4 and word in t):
            return key
    return ""


def current() -> str:
    try:
        p = normalize(_ku().get_setting("persona", "normal"))
        return p or "normal"
    except Exception:
        return "normal"


def _apply_voice(persona: str) -> str:
    """Gemini voice for the mode. Returns the voice now set ('' = unchanged)."""
    try:
        from memory.config_manager import get_voice, save_voice, AVAILABLE_VOICES
    except Exception:
        return ""
    ku = _ku()
    try:
        want = PERSONAS[persona]["voice"]
        if persona == "normal":
            base = ku.get_setting("persona_base_voice", "")
            if base and base in AVAILABLE_VOICES and base != get_voice():
                save_voice(base)
                return base
            return ""
        if not want or want not in AVAILABLE_VOICES:
            return ""
        cur = get_voice()
        if current() == "normal" or not ku.get_setting("persona_base_voice", ""):
            ku.save_setting("persona_base_voice", cur)
        if cur != want:
            save_voice(want)
            return want
    except Exception:
        return ""
    return ""


def set_persona(name) -> tuple:
    """-> (ok, persona_key, voice_set). Persists; never raises."""
    key = normalize(name)
    if not key:
        return False, "", ""
    voice = ""
    try:
        if bool(_ku().get_setting("persona_voice", True)):
            voice = _apply_voice(key)
    except Exception:
        voice = ""
    ok = _ku().save_setting("persona", key)
    return ok, key, voice


def prompt_block(persona: str = "") -> str:
    p = persona or current()
    txt = PERSONAS.get(p, PERSONAS["normal"])["prompt"]
    return f"[PERSONALITY]\n{txt}\n" if txt else ""


def labels() -> list:
    return [(k, v["label"]) for k, v in PERSONAS.items()]
