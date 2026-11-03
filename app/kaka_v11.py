"""KAKA v1.1: the hub for the new features.

  * VERSION (app/VERSION) and the v1.1 switches (kept in config/api_keys.json
    through kaka_user.save_setting - never clobbers anything else).
  * prompt_extras(): the lines main.py adds to Kaka's instructions at every
    session start (city, personality mode, recent-day summaries). Each part
    is independent - one failing just leaves that part out.
  * start(): runs once, 2 s after Kaka opens (core/kaka_services), in a
    background thread: daily conversation summary, auto-update check.

Every v1.1 feature lives in its own module (kaka_persona, kaka_daylog,
kaka_diary, kaka_doctor, kaka_junk, kaka_selfupdate, kaka_pcagent,
kaka_features_ui); importing this file never imports them.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
_STARTED = {"done": False}


def version() -> str:
    try:
        v = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
        return v or "1.0.0"
    except Exception:
        return "1.0.0"


def _log(msg: str) -> None:
    try:
        from core import kaka_services as ks
        ks.log(msg)
    except Exception:
        try:
            print(msg)
        except Exception:
            pass


def get(key: str, default=None):
    try:
        import kaka_user
        return kaka_user.get_setting(key, default)
    except Exception:
        return default


def put(key: str, value) -> bool:
    try:
        import kaka_user
        return kaka_user.save_setting(key, value)
    except Exception:
        return False


def prompt_extras() -> str:
    """Extra instruction text for _build_config. Never raises."""
    parts = []
    try:
        import kaka_user
        city = kaka_user.user_city()
        parts.append(f"[USER CITY]\nThe user lives in {city}. 'Mausam' / weather / "
                     f"'bahar kaisa hai' without a city means {city}.\n")
    except Exception:
        pass
    try:
        import kaka_persona
        b = kaka_persona.prompt_block()
        if b:
            parts.append(b)
    except Exception:
        pass
    try:
        import kaka_daylog
        b = kaka_daylog.prompt_block()
        if b:
            parts.append(b)
    except Exception:
        pass
    return "\n".join(parts)


def _daily_summary() -> None:
    try:
        import kaka_daylog
        n = kaka_daylog.run_daily()
        if n:
            _log(f"SYS: Memory - {n} din ki baaton ka summary save kiya.")
    except Exception as e:
        print(f"[v1.1] daily summary failed: {e}")


def _update_check() -> None:
    try:
        import kaka_selfupdate
        kaka_selfupdate.check_on_start()
    except Exception as e:
        print(f"[v1.1] update check failed: {e}")


def start() -> None:
    """Registered with core.kaka_services.on_start. Idempotent."""
    if _STARTED["done"]:
        return
    _STARTED["done"] = True

    def _run():
        _daily_summary()
        time.sleep(45)          # after the greeting / morning brief
        _update_check()
    threading.Thread(target=_run, name="kaka-v11-start", daemon=True).start()
    _log(f"SYS: Kaka v{version()} features ready.")


try:
    from core import kaka_services as _ks
    _ks.on_start(start)
except Exception:
    pass
