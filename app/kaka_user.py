"""KAKA: the name of the person Kaka is talking to.

One place for it, so a Kaka installed on any PC calls its own user by name:

    1. "user_name" in config/api_keys.json (Kaka-Setup.bat asks for it, and it
       can be changed later in Kaka: gear > Customise > YOUR NAME)
    2. the KAKA_USER_NAME environment variable (Setup sets this too)
    3. otherwise the friendly default "dost"

    python kaka_user.py              print the name Kaka uses
    python kaka_user.py set NAME     save NAME (or the KAKA_SET_NAME variable)

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
    print("NAME|current|" + user_name())
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
