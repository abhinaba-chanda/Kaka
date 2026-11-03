"""
kaka_tools.py — settings helper for Kaka by Abhinaba Chanda.

    python kaka_tools.py config            first-time setup (name, wake word, engines)
    python kaka_tools.py voice brian       Kaka speaks with ElevenLabs Brian (Gemini brain)
    python kaka_tools.py voice gemini      original Gemini Live voice (fastest)
    python kaka_tools.py voice toggle      switch between the two
    python kaka_tools.py hearing           Gemini hears Hinglish (Whisper as backup)
    python kaka_tools.py status            print current settings (never prints keys)

Never prints, logs or copies any API key. Refuses to write if the settings
file cannot be read, so a damaged file is never overwritten with an empty one.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))
CFG = HERE / "config" / "api_keys.json"


def _read() -> dict:
    if not CFG.exists():
        return {}
    try:
        data = json.loads(CFG.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except Exception as e:
        print(f"[X] settings file could not be read ({e}) - not changing anything")
        sys.exit(3)


def _has_key(data: dict) -> bool:
    return len(str(data.get("gemini_api_key") or "")) > 15


def set_voice(which: str) -> str:
    from memory.config_manager import save_providers
    data = _read()
    cur = str((data.get("providers") or {}).get("mode") or "gemini")
    if which == "toggle":
        which = "gemini" if cur == "pipeline" else "brian"
    mode = "pipeline" if which == "brian" else "gemini"
    save_providers(mode=mode)
    return which


def configure() -> None:
    from memory.config_manager import save_providers, _patch_config
    data = _read()
    fields = {"assistant_name": "Kaka"}
    if not str(data.get("user_name") or "").strip():
        fields["user_name"] = "Abhinaba"
    try:
        from core.wake_word import is_ready
        if is_ready():
            fields["wake_word_enabled"] = True
    except Exception as e:
        print(f"[!] wake word check failed: {e}")
    _patch_config(**fields)

    prov = data.get("providers") or {}
    creds = prov.get("creds") or {}
    new_creds = {
        "whisper": {"backend": "local", "model": "small", "device": "cpu",
                    "catalog": "local"},
        "kaka_ear": {"catalog": "kaka_ear", "model": "small", "device": "cpu"},
        "kaka_chain": {"catalog": "kaka_chain"},
        "kaka_voice": {"catalog": "kaka_voice",
                       "voice_id": "nPczCjzI2devNBz1zQrb",
                       "model": "eleven_flash_v2_5"},
    }
    # The plain Ollama entry too, so it is one click in the panel and the
    # app's own background requests can fall back to it.
    oc = creds.get("openai_compat") or {}
    if not str(oc.get("base_url") or "").strip():
        new_creds["openai_compat"] = {"base_url": "http://127.0.0.1:11434/v1",
                                      "model": "qwen3.5:4b", "max_tools": "14",
                                      "max_tool_chars": "12000", "catalog": "ollama"}
    side = str(prov.get("sidecar") or "")
    save_providers(
        mode="pipeline",
        pipeline={"stt": "kaka_ear", "llm": "kaka_chain", "tts": "kaka_voice"},
        creds=new_creds,
        sidecar=side or "openai_compat",
        sidecar_first=bool(prov.get("sidecar_first", False)),
    )


def set_hearing() -> None:
    """Update 2: Gemini hears (Whisper only as backup), smart models first."""
    from memory.config_manager import save_providers
    data = _read()
    prov = data.get("providers") or {}
    pipe = dict(prov.get("pipeline") or {})
    pipe.update({"stt": "kaka_ear"})
    pipe.setdefault("llm", "kaka_chain")
    pipe.setdefault("tts", "kaka_voice")
    creds = {"kaka_ear": {"catalog": "kaka_ear", "model": "small", "device": "cpu"}}
    chain = dict((prov.get("creds") or {}).get("kaka_chain") or {})
    if chain.get("gemini_models"):
        from providers.llm_kaka import _OLD_DEFAULT_MODELS, DEFAULT_GEMINI_MODELS
        if " ".join(str(chain["gemini_models"]).split()) == " ".join(_OLD_DEFAULT_MODELS.split()):
            creds["kaka_chain"] = {"gemini_models": DEFAULT_GEMINI_MODELS}
    save_providers(pipeline=pipe, creds=creds)


def status() -> None:
    data = _read()
    prov = data.get("providers") or {}
    mode = prov.get("mode") or "gemini"
    print(f"name        : {data.get('assistant_name', 'Kaka')}")
    print(f"user        : {data.get('user_name', '')}")
    print(f"gemini key  : {'saved' if _has_key(data) else 'MISSING'}")
    print(f"wake word   : {'ON' if data.get('wake_word_enabled') else 'off'}")
    print(f"voice/engine: {'Brian (ElevenLabs) + Gemini brain chain' if mode == 'pipeline' else 'Gemini Live voice'}")
    print(f"pipeline    : {prov.get('pipeline')}")
    stt = str((prov.get("pipeline") or {}).get("stt") or "")
    print(f"hearing     : {'Gemini hears you (Whisper on this PC = backup)' if stt == 'kaka_ear' else stt or '-'}")
    try:
        import kaka_keys
        extra = [s["provider"] for s in kaka_keys.slots() if s["has_key"]]
        print(f"spare keys  : {', '.join(extra) if extra else 'none'}")
    except Exception:
        pass
    print(f"backup calls: {prov.get('sidecar') or '-'}")
    try:
        from core.wake_word import is_ready, vosk_model_dir
        print(f"wake model  : {'ready' if is_ready() else 'NOT ready'} ({vosk_model_dir()})")
    except Exception as e:
        print(f"wake model  : check failed {e}")
    el = bool(os.environ.get("ELEVENLABS_API_KEY"))
    try:
        from providers.llm_kaka import _user_env
        el = el or bool(_user_env("ELEVENLABS_API_KEY"))
        gq = bool(_user_env("GROQ_API_KEY"))
    except Exception:
        gq = False
    print(f"elevenlabs  : {'key found in Windows' if el else 'no key - Windows voice will speak'}")
    print(f"groq        : {'key found' if gq else 'no key (optional)'}")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "config":
        configure()
        status()
    elif cmd == "voice":
        which = (argv[2] if len(argv) > 2 else "toggle").lower()
        if which not in ("brian", "gemini", "toggle"):
            print("use: voice brian|gemini|toggle")
            return 2
        now = set_voice(which)
        print("Kaka voice is now: " + ("Brian (ElevenLabs)" if now == "brian" else "Gemini Live"))
        if len(argv) > 3 and argv[3] == "--popup" and os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, "Kaka voice: " + ("Brian (ElevenLabs)" if now == "brian"
                                          else "Gemini (original, fastest)")
                    + "\n\nKaka will restart now.", "Kaka", 0x40)
            except Exception:
                pass
    elif cmd == "hearing":
        set_hearing()
        if "--brian" in argv:
            set_voice("brian")
        status()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
