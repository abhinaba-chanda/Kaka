"""
actions/pc_health.py - Kaka update 19: "battery kitni hai", "RAM kitni use ho
rahi", "PC garam hai kya", "PC slow kyun hai". Logic: core/kaka_health.py.
Also starts the low-battery alert (background, 60 s check) 2 s after Kaka opens.
"""
from core import kaka_services as _ks


def _start_health():
    from core import kaka_health as H
    H.start()


_start_health.__name__ = "battery_alert_start"
_ks.on_start(_start_health)


def pc_health(parameters: dict, player=None) -> str:
    try:
        from core import kaka_health as H
        from core import kaka_services as ks
    except Exception as e:
        return f"PC health load nahi hua: {e}"
    p = parameters or {}
    focus = str(p.get("focus") or "all").lower().strip()
    if focus not in ("all", "battery", "ram", "cpu", "temp", "slow", "disk"):
        focus = "all"
    try:
        r = H.report(focus)
        ks.popup("PC health", r["detail"])
        return r["text"] + " (Poori detail HUD pe.) Isko chhote mein sunao."
    except Exception as e:
        return f"PC health padh nahi paya: {e}"


# ---- Tool declaration (auto-discovered by core/action_loader.py) ----
TOOL = {
    "name": "pc_health",
    "description": (
        "Quick PC stats: battery %/charging/time left, CPU %, RAM used/total, C: and F: free "
        "space, top 3 apps by RAM/CPU, CPU/GPU temperature. 'battery kitni hai' -> focus=battery; "
        "'RAM kitni use ho rahi' -> ram; 'PC garam hai kya' -> temp; "
        "'disk/storage kitna bacha' -> disk; 'PC kaisa hai' -> all. Read-only, never ask first. "
        "For a FULL check-up ('PC check karo', 'PC slow kyun hai' with tips) or junk cleaning "
        "use pc_doctor instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "focus": {"type": "STRING", "enum": ["all", "battery", "ram", "cpu", "temp", "slow", "disk"]},
        },
        "required": [],
    },
    "handler": pc_health,
}
