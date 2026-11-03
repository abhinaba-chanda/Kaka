"""KAKA v1.1: voice for the Screen Activity Tracker (app/kaka_activity.py).

    "aaj ka activity report"          -> action=report period=today
    "is hafte ka report"              -> action=report period=week
    "aaj kitna time YouTube pe"       -> action=time name=YouTube
    "tracker band karo / chalu karo"  -> action=off / on
    "help when stuck band karo"       -> action=help_off (help_on)
    after "help chahiye?" haan        -> action=help (ONE screenshot, explain)
                       nahi           -> action=help_no
Everything stays on this PC.
"""
from __future__ import annotations

import time


def _A():
    import kaka_activity
    return kaka_activity


def _panel(rep) -> bool:
    try:
        import kaka_activity_ui
        return kaka_activity_ui.show(rep)
    except Exception:
        return False


def _period(p) -> str:
    v = str(p or "today").strip().lower()
    if v in ("week", "weekly", "hafta", "is hafte", "this week", "7", "7d"):
        return "week"
    if v in ("yesterday", "kal"):
        return "yesterday"
    return "today"


def _help(A) -> str:
    t = A.tracker()
    info = t.pending or {}
    t.pending = None
    title = str(info.get("title") or "")
    q = ("He looks stuck on this window" + (f" (title: '{title[:120]}')" if title else "") +
         ". Find the error / problem visible in the screenshot, say in simple words what it "
         "means, and give the exact steps to fix it (commands / settings / code change). "
         "If nothing looks wrong, say so honestly. Do not invent text that is not on screen.")
    t0 = time.monotonic()
    try:
        from actions.screen_explain import ask_screen
        ok, txt, info2 = ask_screen(q, "window")
    except Exception as e:
        ok, txt = False, f"screen padhne mein dikkat: {type(e).__name__}"
    try:
        from core import kaka_services as ks
        ks.timing("stuck help", time.monotonic() - t0, "ok" if ok else "fail")
    except Exception:
        pass
    if not ok:
        return (f"status=failed. Screen dekh nahi paaya: {txt}. Tell him this in one short line "
                f"in his language; do not guess what is on the screen.")
    return ("status=ok. From the real screenshot (taken once, not saved). Tell him all of it in "
            "his language, step by step, and offer to help further. Never click, type, delete or "
            f"install anything by yourself: {txt}")


def activity_tracker(parameters=None, player=None):
    p = parameters or {}
    act = str(p.get("action") or "report").strip().lower()
    try:
        A = _A()
    except Exception as e:
        return f"status=failed. Activity tracker load nahi hua ({type(e).__name__}). Tell him briefly."

    if act in ("off", "stop", "disable"):
        A.set_enabled(False)
        return ("status=ok. Screen Activity Tracker OFF - ab kuch track nahi hoga (thread band). "
                "Tell him in one short line. Already-saved days stay on his PC.")
    if act in ("on", "start", "enable"):
        A.set_enabled(True)
        ok = A.running() or not A._IS_WIN
        return ("status=ok. Screen Activity Tracker ON - har app / site ka time sirf is PC pe "
                "count hoga, koi screenshot nahi. Tell him in one short line." if ok else
                "status=failed. Tracker ON save hua par shuru nahi ho paaya. Tell him briefly.")
    if act == "help_off":
        A.set_help(False)
        return "status=ok. 'Help when stuck' OFF - Kaka ab khud se nahi poochega. Tell him in one line."
    if act == "help_on":
        A.set_help(True)
        extra = "" if A.tracker_enabled() else " (Tracker abhi OFF hai - ye tabhi chalega jab tracker ON ho.)"
        return "status=ok. 'Help when stuck' ON." + extra + " Tell him in one line."
    if act == "help_no":
        try:
            A.tracker().declined()
        except Exception:
            pass
        return "status=ok. He does not want help now. Say 'theek hai' in one short line, nothing else."
    if act == "help":
        return _help(A)
    if act == "status":
        return (f"status=ok. Tracker {'ON' if A.tracker_enabled() else 'OFF'}"
                f"{' (running)' if A.running() else ''}, help when stuck "
                f"{'ON' if A.help_enabled() else 'OFF'}. Tell him in one line.")

    period = _period(p.get("period"))
    try:
        A.tracker().save()
    except Exception:
        pass
    rep = A.report(period)
    off_note = "" if A.tracker_enabled() else " Note: tracker abhi OFF hai, isliye naya time count nahi ho raha."
    if act == "time":
        name = str(p.get("name") or "").strip()
        hit = A.find_item(rep, name) if name else None
        when = {"today": "aaj", "yesterday": "kal", "week": "is hafte"}[period]
        if not hit:
            return (f"status=ok. {when} '{name}' pe koi time record nahi hua (0 min).{off_note} "
                    f"Tell him in one short line in his language.")
        label, secs = hit
        _panel(rep)
        return (f"status=ok. {when} {label} pe {A.fmt_dur(secs)} (total active "
                f"{A.fmt_dur(rep.get('total_s', 0))}).{off_note} Tell him in one or two short "
                f"sentences in his language. The report panel is on screen.")
    shown = _panel(rep)
    return ("status=ok. " + A.spoken_summary(rep) + off_note +
            " Say this as a short summary (2-4 sentences) in his language" +
            (", and say the full report is on screen." if shown else "."))


TOOL = {
    "name": "activity_tracker",
    "description": (
        "Kaka's Screen Activity Tracker (only on this PC: app / site time per day, no "
        "screenshots). action=report period=today|yesterday|week for 'aaj ka activity report', "
        "'aaj maine kya kiya PC pe', 'is hafte ka report', 'screen time batao' (shows a bar panel). "
        "action=time name=<app or site> for 'aaj kitna time YouTube pe', 'VS Code pe kitna time'. "
        "action=off / on for 'tracker band karo' / 'tracker chalu karo'. action=help_off / "
        "help_on for 'help when stuck band / chalu karo'. After Kaka asked 'help chahiye?' "
        "([STUCK_CHECK]): haan -> action=help (looks at the screen once and explains), "
        "nahi -> action=help_no. action=status for 'tracker on hai?'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["report", "time", "on", "off", "help_on", "help_off",
                                "help", "help_no", "status"]},
            "period": {"type": "STRING", "enum": ["today", "yesterday", "week"],
                       "description": "today (default), yesterday, or week (last 7 days)"},
            "name": {"type": "STRING",
                     "description": "for action=time: the app or site, e.g. YouTube, VS Code, Chrome"},
        },
        "required": ["action"],
    },
    "handler": activity_tracker,
}
