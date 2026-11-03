"""KAKA: the name of the person Kaka is talking to.

One place for it, so a Kaka installed on any PC calls its own user by name:

    1. "user_name" in config/api_keys.json (Kaka-Setup.bat asks for it, and it
       can be changed later in Kaka: gear > Customise > YOUR NAME)
    2. the KAKA_USER_NAME environment variable (Setup sets this too)
    3. otherwise the friendly default "dost"

    python kaka_user.py              print the name Kaka uses
    python kaka_user.py set NAME     save NAME (or the KAKA_SET_NAME variable)
    python kaka_user.py city         print the city Kaka uses (weather etc.)
    python kaka_user.py setcity CITY save CITY (or the KAKA_SET_CITY variable)

v1.1: the user's CITY lives next to the name ("user_city", default Kolkata),
and save_setting()/get_setting() give the v1.1 features one safe way to keep
their own switches in the same file without clobbering anything else.

Never touches API keys: it only adds or changes "user_name", and refuses to
write if the settings file cannot be read.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DEFAULT_NAME = "dost"
DEFAULT_CITY = "Kolkata"
MAX_LEN = 40
CONFIG_FILE = Path(__file__).resolve().parent / "config" / "api_keys.json"


def clean(name) -> str:
    """Trim, drop control characters and quote/brace characters (the name goes
    into prompts and spoken lines), squeeze spaces, cap the length."""
    s = str(name or "")
    s = re.sub(r"[\x00-\x1f\x7f\"'`{}\[\]<>\\|]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_LEN].strip()


def _read_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _env_name() -> str:
    v = clean(os.environ.get("KAKA_USER_NAME", ""))
    if v or sys.platform != "win32":
        return v
    try:                                   # set by Setup after this process started
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return clean(winreg.QueryValueEx(k, "KAKA_USER_NAME")[0])
    except Exception:
        return ""


def configured_name() -> str:
    """The name the user gave (settings file, then KAKA_USER_NAME), or ''."""
    return clean(_read_config().get("user_name", "")) or _env_name()


def user_name() -> str:
    """The name Kaka calls the user by. Never empty, never raises."""
    try:
        return configured_name() or DEFAULT_NAME
    except Exception:
        return DEFAULT_NAME


def save_user_name(name) -> str:
    """Store the name in config/api_keys.json, keeping every other setting.
    Returns the stored name. Raises if the file exists but cannot be read."""
    n = clean(name)
    data: dict = {}
    if CONFIG_FILE.exists():
        raw = CONFIG_FILE.read_text(encoding="utf-8-sig")
        if raw.strip():
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("settings file is not a JSON object")
    data["user_name"] = n
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    return n


# ------------------------------------------------------------------ v1.1: any setting
def _write_config_key(key: str, value) -> None:
    """Set one key in config/api_keys.json, keeping every other setting.
    Raises if the file exists but cannot be read (never wipes the keys)."""
    data: dict = {}
    if CONFIG_FILE.exists():
        raw = CONFIG_FILE.read_text(encoding="utf-8-sig")
        if raw.strip():
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("settings file is not a JSON object")
    data[key] = value
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


def get_setting(key: str, default=None):
    """One value from config/api_keys.json, or default. Never raises."""
    try:
        v = _read_config().get(key, default)
        return default if v is None else v
    except Exception:
        return default


def save_setting(key: str, value) -> bool:
    """Store one value, keeping every other setting. False on any error."""
    try:
        _write_config_key(str(key), value)
        return True
    except Exception as e:
        try:
            print(f"[KAKA] setting {key} not saved: {e}")
        except Exception:
            pass
        return False


# ------------------------------------------------------------------ v1.1: the user's city
def clean_city(city) -> str:
    """Like clean(), plus a few spoken fillers removed ("Delhi city" -> "Delhi")."""
    s = clean(city)
    s = re.sub(r"(?i)\b(city|shahar|sheher|shehar|mein|me|ka|ki|ke)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .,-")
    return s[:MAX_LEN].strip()


def _env_city() -> str:
    v = clean_city(os.environ.get("KAKA_USER_CITY", ""))
    if v or sys.platform != "win32":
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return clean_city(winreg.QueryValueEx(k, "KAKA_USER_CITY")[0])
    except Exception:
        return ""


def configured_city() -> str:
    """The city the user gave (settings file, then KAKA_USER_CITY), or ''."""
    try:
        return clean_city(_read_config().get("user_city", "")) or _env_city()
    except Exception:
        return ""


def user_city() -> str:
    """The city Kaka uses for weather etc. Never empty, never raises."""
    try:
        return configured_city() or DEFAULT_CITY
    except Exception:
        return DEFAULT_CITY


def save_user_city(city) -> str:
    """Store the city (keeps every other setting). Returns what was stored.
    Raises if the settings file exists but cannot be read."""
    c = clean_city(city)
    if not c:
        raise ValueError("empty city")
    _write_config_key("user_city", c)
    return c


def _main(argv) -> int:
    if len(argv) > 1 and argv[1] == "set":
        name = " ".join(argv[2:]) if len(argv) > 2 else os.environ.get("KAKA_SET_NAME", "")
        if not clean(name):
            print("NAME|error|empty name - nothing saved")
            return 2
        try:
            print("NAME|saved|" + save_user_name(name))
            return 0
        except Exception as e:
            print(f"NAME|error|settings file could not be read ({e}) - not changed")
            return 3
    if len(argv) > 1 and argv[1] == "setcity":
        city = " ".join(argv[2:]) if len(argv) > 2 else os.environ.get("KAKA_SET_CITY", "")
        if not clean_city(city):
            print("CITY|error|empty city - nothing saved")
            return 2
        try:
            print("CITY|saved|" + save_user_city(city))
            return 0
        except Exception as e:
            print(f"CITY|error|settings file could not be read ({e}) - not changed")
            return 3
    if len(argv) > 1 and argv[1] == "city":
        print("CITY|current|" + user_city())
        return 0
    print("NAME|current|" + user_name())
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
