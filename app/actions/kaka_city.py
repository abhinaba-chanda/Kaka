"""
actions/kaka_city.py - KAKA v1.1: "Kaka, mera city Delhi kar do". The city is
kept as user_city in config/api_keys.json (app/kaka_user.py) and used for
weather and the morning brief. Settings > Customise > YOUR CITY changes it too.
"""


def kaka_city(parameters=None, player=None) -> str:
    try:
        import kaka_user as U
    except Exception as e:
        return f"City setting load nahi hui: {e}"
    p = parameters or {}
    city = str(p.get("city") or "").strip()
    if not city or str(p.get("action") or "").lower() == "status":
        return f"Abhi aapka city {U.user_city()} set hai. Ye ek line mein bata do."
    try:
        saved = U.save_user_city(city)
    except Exception as e:
        return f"City save nahi ho paya ({type(e).__name__}). Ek line mein bata do."
    try:
        if player:
            player.write_log(f"SYS: City set -> {saved}")
    except Exception:
        pass
    return (f"City saved: {saved}. Weather and the morning brief now use {saved}. "
            f"Tell him in one short line, e.g. 'Theek hai, ab {saved} ka mausam bataunga.' "
            f"His city for the rest of this conversation is {saved}.")


TOOL = {
    "name": "kaka_city",
    "description": (
        "Sets or tells the user's own city (used for weather and the morning brief): "
        "'mera city Delhi kar do', 'main Mumbai mein rehta hoon, city badal do', "
        "'set my city to Pune' -> city=<name>. 'mera city kya hai' -> action=status. "
        "No question first. For just asking another city's weather use weather_report."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "The city name, e.g. 'Delhi'"},
            "action": {"type": "STRING", "enum": ["set", "status"]},
        },
        "required": [],
    },
    "handler": kaka_city,
}
