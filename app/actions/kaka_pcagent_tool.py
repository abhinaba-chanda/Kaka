"""
actions/kaka_pcagent_tool.py - KAKA v1.1: Kaka operates the PC itself
(mouse + keyboard + screen). Logic and safety rules: app/kaka_pcagent.py.
"""


def pc_agent(parameters=None, player=None) -> str:
    try:
        import kaka_pcagent as A
    except Exception as e:
        return f"PC control load nahi hua: {e}"
    p = parameters or {}
    action = str(p.get("action") or "start").strip().lower()
    cur = A.current()
    if action == "stop":
        if cur:
            cur.stop()
            return "Ruk gaya. Say 1-3 words."
        return "Kuch chal hi nahi raha tha. One short line."
    if action in ("confirm", "cancel"):
        if not cur or not cur.waiting_confirm:
            return "Abhi koi sawaal pending nahi hai."
        A.clear_banner()
        cur.confirm(action == "confirm")
        return ("Theek hai, kar raha hoon. Say 2-4 words." if action == "confirm"
                else "Theek hai, nahi kiya. Say it in one line.")
    if action == "status":
        if not cur:
            return "PC control abhi nahi chal raha."
        return f"Chal raha hai: {cur.goal}. {len(cur.history)} steps ho gaye."
    goal = str(p.get("goal") or "").strip()
    if not goal:
        return "Kya karna hai PC pe, wo bolo."
    if cur:
        return f"Pehle wala kaam chal raha hai ({cur.goal}). 'ruk jao' bolo ya intezaar karo."

    def _done(res):
        try:
            from core import kaka_services as ks
            ks.say(f"PC wala kaam: {res}")
        except Exception:
            pass
    A.start(goal, done_cb=_done)
    return ("started. Kaka is now doing it on screen step by step (shown on the HUD; Esc "
            "or 'ruk jao Kaka' stops). Say 2-5 words like 'Kar raha hoon'. The result comes "
            "by itself. If it asks before sending/deleting, ask him that ONE question.")


TOOL = {
    "name": "pc_agent",
    "description": (
        "Kaka operates the PC by itself - looks at the screen and uses mouse + keyboard, many "
        "steps (max 15): 'WhatsApp Web khol ke Rahul ko msg likh do', 'YouTube pe lofi laga do "
        "(khud chala ke)', 'Notepad khol ke list likh do', 'is page pe login button dabao', "
        "'Chrome mein flipkart pe shoes dhundo'. action=start with goal=<the whole task in his "
        "words>. Use it for screen tasks that no simpler tool does in one call. 'ruk jao Kaka' "
        "/ 'stop' -> action=stop (immediately). It pauses and asks before Send / Delete / Buy / "
        "Post / Install: after his haan -> action=confirm, after nahi -> action=cancel."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["start", "stop", "confirm", "cancel", "status"]},
            "goal": {"type": "STRING", "description": "The whole task, e.g. 'WhatsApp Web pe Rahul ko \"kal milte hain\" likho'"},
        },
        "required": ["action"],
    },
    "handler": pc_agent,
}
