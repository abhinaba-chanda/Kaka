"""
actions/screen_explain.py - Kaka update 18: "screen pe kya hai", "ye error kya
hai", "isko samjhao".

One screenshot of the window he is looking at (Kaka's own HUD skipped),
downscaled in memory, sent with his question to Gemini (one-shot REST with
his existing key; works in every engine mode) -> a detailed spoken answer
(v1.1: 4-8 sentences, was 2-4 short ones) Kaka reads out. The screenshot is
never written anywhere permanent (memory only, max 1600 px).
"""
import time

SYSTEM = (
    "You are Kaka, a friendly PC assistant for an Indian user who speaks Hinglish "
    "(Roman Hindi + English). You are shown a screenshot of the window he is "
    "looking at. Answer in a DETAILED, natural spoken way: usually 4-8 Hinglish "
    "sentences (more if he asks to explain fully) - first what is on the screen / "
    "the direct answer, then the important details, what it means, and what he "
    "should do next. Match his language (Hinglish, Bengali or English). No "
    "markdown, no lists, no symbols. If there is an error: say in simple words "
    "what it means, why it happens and the exact fix (which setting/command/line). "
    "Quote short on-screen text exactly when it matters. If something is "
    "unreadable, say so honestly; never invent what is not visible."
)

# KAKA v1.1: a sharper screenshot for the detailed answer (was 1280 px).
SCREEN_MAX_SIDE = 1600


def _ks():
    from core import kaka_services as ks
    return ks


def ask_screen(question: str, mode: str = "window", system: str = SYSTEM,
               timeout: float = 20.0) -> tuple:
    """-> (ok, text, info). Shared with kaka_translate."""
    from core import kaka_screen, kaka_ai
    try:                                   # KAKA v1.1: up to 1600 px
        if getattr(kaka_screen, "MAX_SIDE", 0) < SCREEN_MAX_SIDE:
            kaka_screen.MAX_SIDE = SCREEN_MAX_SIDE
    except Exception:
        pass
    img, mime, info = kaka_screen.capture(mode)
    if img is None:
        return False, info.get("error") or "screenshot nahi le paaya", info
    where = (f"the active window titled '{info['title']}'" if info.get("mode") == "window"
             else "his whole screen")
    prompt = f"Screenshot of {where}. His question: {question}"
    try:
        ok, txt = kaka_ai.vision(img, prompt, timeout=timeout, mime=mime, system=system)
    finally:
        del img
    return ok, txt, info


def screen_explain(parameters=None, player=None):
    t0 = time.monotonic()
    p = parameters or {}
    q = str(p.get("question") or "").strip() or "Screen pe kya hai? Detail mein samjhao."
    mode = "screen" if str(p.get("target") or "").lower().startswith("screen") or \
        str(p.get("target") or "").lower() in ("full", "full_screen", "poori") else "window"
    try:
        ok, txt, info = ask_screen(q, mode)
    except Exception as e:
        ok, txt, info = False, f"screen padhne mein dikkat: {type(e).__name__}", {}
    secs = time.monotonic() - t0
    try:
        _ks().timing("screen explain", secs,
                     f"{info.get('mode', '?')}, {info.get('grabber', '-')}, "
                     f"{'ok' if ok else 'fail'}")
    except Exception:
        pass
    if not ok:
        return (f"status=failed. Screen samajh nahi paaya: {txt}. Tell him exactly "
                f"this in one short Hinglish line; do not describe the screen from imagination.")
    return ("status=ok. Answer from the real screenshot - say ALL of it to him in his "
            f"language, in full (do not shorten it), without adding anything: {txt}")


TOOL = {
    "name": "screen_explain",
    "description": (
        "Looks at the window the user is looking at RIGHT NOW (screenshot) and "
        "answers his question about it. Use for 'screen pe kya hai', 'ye kya hai', "
        "'ye error kya hai', 'isko samjhao', 'ye kaise fix karun', 'ye screen dekh', "
        "'screen dekh ke batao', 'what's on my screen', 'look at my screen'. "
        "target=screen when he says screen / poori screen / look at my screen; "
        "window for 'ye error / ye window / isko'. No question first - just call it. "
        "Speak the returned answer in full - it is meant to be detailed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING",
                         "description": "His question in his words, e.g. 'ye error kya hai aur fix kaise karun'"},
            "target": {"type": "STRING", "enum": ["window", "screen"],
                       "description": "window = the active window (default); screen = whole screen ('poori screen')"},
        },
        "required": ["question"],
    },
    "handler": screen_explain,
}
