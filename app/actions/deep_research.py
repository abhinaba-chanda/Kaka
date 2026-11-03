r"""
actions/deep_research.py - KAKA: Deep Research mode (voice tool).

"Chandrayaan-3 pe deep research karo", "deep dive into how transformers
work", "X ke baare mein detail mein research karo" -> action=start.
"point 3 aur detail mein batao", "is pe aur research karo" -> action=deepen.
"research report dikhao" -> action=show; "research band karo" -> action=stop.

Runs in the background (about 1-2 minutes): live progress in the on-screen
panel, then Kaka speaks a 3-5 sentence summary and the full report (with
clickable sources) opens on screen and is saved to Data\Research as .md and
.pdf. Logic: app/kaka_research.py, panel/PDF: core/kaka_research_panel.py.
Read-only: it only searches and reads web pages.
"""


def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def _panel():
    try:
        from core import kaka_research_panel as P
        return P
    except Exception:
        return None


def _where(paths: dict) -> str:
    p = (paths or {}).get("pdf") or (paths or {}).get("md") or ""
    if not p:
        return "save nahi ho paya"
    import os
    folder = os.path.dirname(p)
    names = [os.path.basename(x) for x in ((paths or {}).get("md"), (paths or {}).get("pdf")) if x]
    return f"{folder} mein ({' aur '.join(names)})"


def _speak_done(res: dict, lang_label: str, deeper: bool) -> None:
    ks = _ks()
    if ks is None:
        return
    if not res.get("ok"):
        if res.get("stopped"):
            return
        ks.say(f"Deep research nahi ho payi: {res.get('error') or 'kuch gadbad hui'}.")
        return
    rep = res["report"]
    d = res.get("deeper") or {}
    summary = (d.get("summary") if deeper else rep.get("summary")) or rep.get("overview", "")[:600]
    where = _where(res.get("paths") or {})
    what = f"'{d.get('title')}' ka deeper section" if deeper else f"'{rep.get('topic')}' ki deep research"
    ks.say(f"{what} poori ho gayi. LANGUAGE: is summary ko {lang_label} mein bolo "
           f"(3-5 vakya, sab bolo): {summary} || Phir ek chhoti line: poori report screen pe "
           f"khuli hai (sources ke saath) aur save hai: {where}. Follow-up ke liye "
           f"'point 2 aur detail mein batao' bol sakte ho.", brief=False)


def deep_research(parameters=None, player=None) -> str:
    try:
        import kaka_research as R
    except Exception as e:
        return f"Deep research load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "start").strip().lower()
    lang = str(p.get("language") or "hinglish").strip().lower()
    lang_label = R.lang_name(lang)
    cur = R.current()
    P = _panel()

    if action == "stop":
        if cur:
            cur.stop.set()
            if P:
                P.show_error("Research rok di.")
            return "Research rok di. Say 2-4 words."
        return "Koi research chal hi nahi rahi thi. One short line."
    if action == "status":
        if not cur:
            return "Abhi koi research nahi chal rahi."
        st = cur.state
        return f"Chal rahi hai: {cur.topic} - {st.get('msg', '')}. One short line."
    if action == "show":
        if P and P.show_last():
            return "Pichli research report screen pe khol di. Say it in one short line."
        return "Abhi koi saved research report nahi hai. Tell him in one line."
    if cur:
        return (f"Pehle wali research chal rahi hai ({cur.topic}). Tell him to wait a minute "
                "or say 'research band karo'.")

    def _progress(st):
        if P:
            P.progress(st)

    if action in ("deepen", "more", "detail"):
        try:
            point = int(p.get("point") or 0)
        except Exception:
            point = 0
        focus = str(p.get("topic") or p.get("focus") or "").strip()
        last = R.last()
        if not last:
            if focus:
                action = "start"
            else:
                return ("Abhi koi pichli research yaad nahi. Ask him which topic to deep-research "
                        "(then call action=start).")
        else:
            n = len(last.get("sections") or [])
            if point and not 1 <= point <= n:
                return (f"Report mein sirf {n} points hain. Ask him which one (1-{n}).")

            def _done(res):
                if res.get("ok") and P:
                    P.show_report(res["report"], res.get("paths"))
                elif P and not res.get("stopped"):
                    P.show_error("Deeper research nahi ho payi: " + str(res.get("error") or ""))
                _speak_done(res, lang_label, True)
            R.Job(last["topic"], lang, kind="deepen", point=point, focus=focus,
                  progress=_progress, done=_done).start()
            what = f"point {point}" if point else (focus or "is topic")
            return (f"started (deeper research on {what} of '{last['topic']}', about a minute; "
                    "progress shows on screen). Say ONE short line in his language like "
                    "'is pe aur research kar raha hoon, ek minute' and stop. The result "
                    "arrives by itself - do not call any tool for it.")

    topic = str(p.get("topic") or "").strip()
    if not topic:
        return "Kis topic pe deep research karni hai? Ask him in one short line."

    def _done_new(res):
        if res.get("ok") and P:
            P.show_report(res["report"], res.get("paths"))
        elif P and not res.get("stopped"):
            P.show_error("Research nahi ho payi: " + str(res.get("error") or ""))
        _speak_done(res, lang_label, False)
    R.Job(topic, lang, kind="new", progress=_progress, done=_done_new).start()
    return (f"started (deep research on '{topic}': 4-6 sub-questions, real pages + Wikipedia, "
            "about 1-2 minutes; live progress shows in a panel on screen). Say ONE short line "
            "in his language like 'research kar raha hoon, ek minute' and stop talking. The "
            "spoken summary and the full report arrive by themselves - do not call any tool "
            "for it and do not answer the topic from memory now.")


TOOL = {
    "name": "deep_research",
    "description": (
        "DEEP research / deep dive on a topic: plans 4-6 sub-questions, searches each, reads "
        "the real web pages + Wikipedia, writes a full report with numbered sources, shows it "
        "on screen, saves it (.md + PDF in Data\\Research) and speaks a short summary. Use ONLY "
        "when he asks for deep / detailed research or a deep dive ('Chandrayaan-3 pe deep "
        "research karo', 'deep dive into X', 'X ke baare mein detail mein research karo') -> "
        "action=start topic=<topic>. Quick facts / normal questions use web_search instead. "
        "Follow-ups on the last report: 'point 3 aur detail mein batao' -> action=deepen "
        "point=3; 'is pe aur research karo' -> action=deepen (topic=<sub-topic> if he names "
        "one). 'research report dikhao' -> action=show. 'research band karo' -> action=stop. "
        "language = the language he is speaking (hinglish / hindi / bengali / english)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["start", "deepen", "show", "stop", "status"]},
            "topic": {"type": "STRING", "description": "The topic (start) or the sub-topic to go deeper on (deepen)"},
            "point": {"type": "INTEGER", "description": "deepen: section number of the last report (1-6)"},
            "language": {"type": "STRING", "enum": ["hinglish", "hindi", "bengali", "english"]},
        },
        "required": ["action"],
    },
    "handler": deep_research,
}
