"""
actions/kaka_translate.py - Kaka update 18: "isko Bengali mein bolo",
"English mein translate karo <text>", "screen ka text translate karo".

Text  -> Gemini text call (his existing key), translation returned for Kaka
         to read out as-is.
Screen-> the screenshot path of screen_explain: Gemini reads the visible text
         and translates it.
"""
import time

_LANGS = {"bangla": "Bengali", "bengali": "Bengali", "bangali": "Bengali",
          "hindi": "Hindi", "english": "English", "angrezi": "English",
          "angreji": "English", "urdu": "Urdu", "marathi": "Marathi",
          "tamil": "Tamil", "telugu": "Telugu", "gujarati": "Gujarati",
          "punjabi": "Punjabi", "odia": "Odia", "oriya": "Odia",
          "kannada": "Kannada", "malayalam": "Malayalam", "spanish": "Spanish",
          "french": "French", "german": "German", "japanese": "Japanese",
          "arabic": "Arabic", "hinglish": "Hinglish (Roman Hindi)"}


def norm_lang(lang: str) -> str:
    l = (lang or "").strip()
    return _LANGS.get(l.lower(), l.title() if l else "English")


def route(p: dict) -> str:
    """'screen' or 'text'."""
    src = str(p.get("source") or "").strip().lower()
    txt = str(p.get("text") or "").strip()
    if src == "screen":
        return "screen"
    if src == "text":
        return "text"
    return "text" if txt else "screen"


def _prompt_text(text: str, lang: str) -> str:
    return (f"Translate the following into {lang}. Use {lang}'s own script "
            f"(Hinglish means Roman letters). Natural spoken style. Output ONLY "
            f"the translation, nothing else.\n\n{text}")


def _prompt_screen(lang: str, focus: str) -> str:
    f = f" Focus on: {focus}." if focus else ""
    return (f"Read the main text visible in this screenshot (ignore menus, toolbars, "
            f"tabs, taskbar).{f} Translate it into {lang}, in {lang}'s own script, "
            f"natural spoken style. If it is long, translate the first ~120 words and "
            f"say 'baaki bhi hai'. Output ONLY the translation.")


def kaka_translate(parameters=None, player=None):
    t0 = time.monotonic()
    p = parameters or {}
    lang = norm_lang(p.get("target_language") or "")
    way = route(p)
    ok, out = False, ""
    try:
        if way == "screen":
            from actions.screen_explain import ask_screen
            ok, out, _info = ask_screen(_prompt_screen(lang, str(p.get("text") or "")),
                                        system="You translate text read from screenshots.")
        else:
            from core import kaka_ai
            ok, out = kaka_ai.text(_prompt_text(str(p.get("text")), lang))
    except Exception as e:
        ok, out = False, f"{type(e).__name__}"
    try:
        from core import kaka_services as ks
        ks.timing("translate", time.monotonic() - t0, f"{way}, {lang}, {'ok' if ok else 'fail'}")
    except Exception:
        pass
    if not ok:
        if way == "text":
            return (f"status=failed. Translate service nahi chala: {out}. If the text is "
                    f"short, translate it yourself into {lang} and say it; otherwise tell him that reason.")
        return (f"status=failed. Screen ka text translate nahi ho paaya: {out}. "
                f"Tell him that in one short line.")
    return f"status=ok. {lang} translation - read it out exactly as written, nothing added: {out}"


TOOL = {
    "name": "kaka_translate",
    "description": (
        "Translates SCREEN text or LONG text. 'screen ka text translate karo', "
        "'ye page Bengali mein batao', 'is paragraph ko English mein translate karo'. "
        "For a short sentence he just spoke ('isko Bengali mein bolo: main aa raha hoon') "
        "you may translate it yourself without this tool. Read the result out as-is."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target_language": {"type": "STRING",
                                "description": "Language to translate into: Bengali, English, Hindi, ... (default English)"},
            "text": {"type": "STRING",
                     "description": "The text to translate (source=text). For source=screen: optional hint what part to read"},
            "source": {"type": "STRING", "enum": ["text", "screen"],
                       "description": "screen = read the text from his active window; text = translate 'text'"},
        },
        "required": ["target_language"],
    },
    "handler": kaka_translate,
}
