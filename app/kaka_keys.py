"""
kaka_keys.py - extra API keys for Kaka (KAKA EDITION, Nov 2026).

    * Groq key            -> Windows user variable GROQ_API_KEY
    * spare slots 1-11    -> Windows user variables KAKA_EXTRA_KEY_1 .. _11
      (update 8: Gemini keys 2-5, Groq key 2, six custom)
      with the slot's provider / address / model (not secret) kept in
      config/api_keys.json under "kaka_key_slots".

Keys are never written to the settings file or the log and never shown in
full: the panel shows only "saved ....1234". Saving also sets the variable in
the running app, so the brain chain uses a new key at once (no restart).

Update 3 adds:
    * the MAIN Gemini key row. That key stays where the stock app (and every
      Gemini feature in it) reads it: config/api_keys.json "gemini_api_key".
      Saving it there keeps the first-run key screen away. It is never logged.
    * "use first": which key Kaka asks first ("auto" = the order below);
      kept in config/api_keys.json as "kaka_brain_first" (not secret).
    * the local (Ollama) model Kaka falls back to: "kaka_ollama_model".

Update 5 (API only - no AI on this PC any more):
    * Ollama / local models / Piper / local Whisper are gone. "local" choices
      left in an old settings file simply read as AUTO.
    * More free API providers, each with its own row in API KEYS (NAMED below)
      and its own Windows user variable, like Groq.
    * Voice: BRIAN (ElevenLabs) or WINDOWS AWAAZ (offline Windows voice).

The brain chain order (AUTO) is:
    Gemini (main key) -> Gemini (2nd key, if a slot holds one) -> Groq
    -> Mistral -> NVIDIA NIM -> OpenRouter -> Cohere -> spare slots
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = HERE / "config" / "api_keys.json"

GROQ_VAR = "GROQ_API_KEY"
# KAKA update 8: 11 spare slots - Gemini keys 2-5, Groq key 2, six custom.
SLOT_VARS = tuple(f"KAKA_EXTRA_KEY_{i}" for i in range(1, 12))
DISCORD_VAR = "KAKA_DISCORD_TOKEN"          # KAKA update 20: Discord remote bot token
SLOT_DEFAULTS = ("gemini", "gemini", "gemini", "gemini", "groq",
                 "openai", "openai", "openai", "openai", "openai", "openai")


def slot_title(i: int, provider: str = "") -> str:
    if i < 4:
        return f"GEMINI KEY {i + 2}"
    if i == 4:
        return "GROQ KEY 2"
    return f"CUSTOM {i - 4}"

PROVIDERS = {
    # id: (label, default base url, default model)
    "gemini": ("Gemini (extra key)", "https://generativelanguage.googleapis.com", ""),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "google/gemma-4-26b-a4b-it:free"),
    "openai": ("Any OpenAI-compatible", "", ""),
}
PROVIDER_ORDER = ("gemini", "groq", "openrouter", "openai")


# -- secrets (Windows user environment) ----------------------------------------
def get_secret(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val or os.name != "nt":
        return val
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v, _t = winreg.QueryValueEx(k, name)
            return str(v or "").strip()
    except Exception:
        return ""


def _broadcast_env_change() -> None:
    try:
        import ctypes
        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x2
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG,
            2000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass


def set_secret(name: str, value: str) -> None:
    value = (value or "").strip()
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_SET_VALUE) as k:
            if value:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        threading.Thread(target=_broadcast_env_change, daemon=True).start()
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def masked(name: str) -> str:
    v = get_secret(name)
    if not v:
        return "not set"
    return "saved \u2022\u2022\u2022\u2022" + v[-4:]


# -- slot settings (not secret) ---------------------------------------------------
def _read_cfg() -> dict:
    try:
        d = json.loads(CFG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def slots() -> list[dict]:
    """The spare slots, each {"provider", "base_url", "model", "var", "has_key"}."""
    raw = _read_cfg().get("kaka_key_slots")
    raw = raw if isinstance(raw, list) else []
    out = []
    for i, var in enumerate(SLOT_VARS):
        s = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        prov = str(s.get("provider") or (SLOT_DEFAULTS[i] if i < len(SLOT_DEFAULTS) else "openai"))
        if prov not in PROVIDERS:
            prov = "openai"
        out.append({"provider": prov,
                    "base_url": str(s.get("base_url") or "").strip(),
                    "model": str(s.get("model") or "").strip(),
                    "var": var, "has_key": bool(get_secret(var))})
    return out


def save_slot(index: int, provider: str, base_url: str = "", model: str = "") -> None:
    from memory.config_manager import _patch_config
    cur = [{k: s[k] for k in ("provider", "base_url", "model")} for s in slots()]
    cur[index] = {"provider": provider if provider in PROVIDERS else "openai",
                  "base_url": (base_url or "").strip(), "model": (model or "").strip()}
    _patch_config(kaka_key_slots=cur)


def slot_target(s: dict) -> tuple[str, str]:
    """(base_url, model) with the provider's defaults filled in."""
    _label, base, model = PROVIDERS.get(s["provider"], PROVIDERS["openai"])
    return (s.get("base_url") or base).rstrip("/"), (s.get("model") or model)



# -- Update 5: more free API providers ----------------------------------------
#   Each one: an OpenAI-compatible /chat/completions address with tool calling,
#   a default fast model, the Windows user variable that holds the key, and the
#   page where a free key is made. Order = the AUTO order after Groq (fastest
#   first). Verified against each provider's own docs on 1 Nov 2026.
NAMED = (
    {"id": "mistral", "title": "MISTRAL", "name": "Mistral", "var": "MISTRAL_API_KEY",
     "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest",
     "key_url": "https://console.mistral.ai/api-keys",
     "note": "free mode, no card",
     "limits": "Free mode (no card); limits shown in your Mistral console"},
    {"id": "nvidia", "title": "NVIDIA NIM", "name": "NVIDIA NIM", "var": "NVIDIA_API_KEY",
     "base_url": "https://integrate.api.nvidia.com/v1",
     "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
     "key_url": "https://build.nvidia.com/settings/api-keys",
     "note": "free dev key",
     "limits": "Free for NVIDIA Developer Program members (about 40 requests/min)"},
    {"id": "openrouter", "title": "OPENROUTER", "name": "OpenRouter", "var": "OPENROUTER_API_KEY",
     "base_url": "https://openrouter.ai/api/v1", "model": "google/gemma-4-26b-a4b-it:free",
     "key_url": "https://openrouter.ai/settings/keys",
     "note": ":free models, 50/day",
     "headers": {"HTTP-Referer": "https://github.com/FatihMakes/Mark-LVI", "X-Title": "Kaka"},
     "limits": "Models ending :free - 20/min, 50/day"},
    # Update 8. Cloudflare needs the Account ID too (second field in the row).
    {"id": "cloudflare", "title": "CLOUDFLARE AI", "name": "Cloudflare Workers AI",
     "var": "CLOUDFLARE_API_TOKEN", "account_var": "CLOUDFLARE_ACCOUNT_ID",
     "base_url": "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1",
     "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
     "key_url": "https://dash.cloudflare.com/?to=/:account/ai/workers-ai",
     "note": "token + Account ID, 10k neurons/day",
     "limits": "Free: 10,000 Neurons a day (resets 00:00 UTC)"},
    {"id": "sambanova", "title": "SAMBANOVA", "name": "SambaNova", "var": "SAMBANOVA_API_KEY",
     "base_url": "https://api.sambanova.ai/v1", "model": "Meta-Llama-3.3-70B-Instruct",
     "key_url": "https://cloud.sambanova.ai/apis",
     "note": "free tier unclear, ~20/day",
     "limits": "Docs: free tier 20 requests/day per model when no card is linked (unclear)"},
    {"id": "cohere", "title": "COHERE", "name": "Cohere", "var": "COHERE_API_KEY",
     "base_url": "https://api.cohere.ai/compatibility/v1", "model": "command-r7b-12-2024",
     "key_url": "https://dashboard.cohere.com/api-keys",
     "note": "trial, 1000/month",
     "limits": "Trial key - 20/min, 1,000 calls a month"},
)
NAMED_IDS = tuple(p["id"] for p in NAMED)


def named(pid: str) -> dict:
    for p in NAMED:
        if p["id"] == pid:
            return p
    return {}


def named_base(pid: str) -> str:
    """The provider's address, with Cloudflare's Account ID filled in ('' when
    a required Account ID is missing)."""
    p = named(pid)
    base = p.get("base_url", "")
    if "{ACCOUNT_ID}" in base:
        acc = get_secret(p.get("account_var", "")).strip()
        if not acc:
            return ""
        base = base.replace("{ACCOUNT_ID}", acc)
    return base.rstrip("/")


def named_model(pid: str) -> str:
    """The model Kaka asks this provider for (his own choice, or the default)."""
    m = _read_cfg().get("kaka_models")
    m = m if isinstance(m, dict) else {}
    return str(m.get(pid) or "").strip() or named(pid).get("model", "")


def set_named_model(pid: str, model: str) -> None:
    cur = _read_cfg().get("kaka_models")
    cur = dict(cur) if isinstance(cur, dict) else {}
    model = (model or "").strip()
    if model and model != named(pid).get("model", ""):
        cur[pid] = model
    else:
        cur.pop(pid, None)
    _write_cfg(kaka_models=cur)


def key_page(pid: str) -> str:
    if pid == "discord":                       # KAKA update 20
        return "https://discord.com/developers/applications"
    if pid == "gemini":
        return "https://aistudio.google.com/app/apikey"
    if pid == "groq":
        return "https://console.groq.com/keys"
    return named(pid).get("key_url", "")


# -- test a key -------------------------------------------------------------------
def _chat_test(base: str, key: str, model: str, extra_headers=None) -> tuple[bool, str]:
    import httpx
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    h.update(extra_headers or {})
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with: OK"}],
            "max_tokens": 8, "stream": False}
    try:
        r = httpx.post(f"{base}/chat/completions", headers=h, json=body,
                       timeout=httpx.Timeout(25, connect=8))
    except Exception as e:
        return False, f"no connection ({type(e).__name__})"
    if r.status_code == 200:
        return True, f"OK - key works ({model} answered)"
    if r.status_code in (401, 403):
        return False, "key refused (wrong or disabled key)"
    if r.status_code == 429:
        return True, "key works, but its free limit is used up right now"
    if r.status_code in (400, 404, 422):
        low = r.text.lower()
        if "model" in low:
            return False, f"key OK? model '{model}' not available - type another model"
    return False, f"error {r.status_code}"


def discord_test(key: str) -> tuple[bool, str]:
    """KAKA update 20: is this a working Discord BOT token? Never logs the key."""
    import httpx
    try:
        r = httpx.get("https://discord.com/api/v10/users/@me",
                      headers={"Authorization": f"Bot {key}", "User-Agent": "Kaka (kaka, 20)"},
                      timeout=12)
    except Exception as e:
        return False, f"no connection ({type(e).__name__})"
    if r.status_code == 200:
        try:
            name = (r.json() or {}).get("username") or "bot"
        except Exception:
            name = "bot"
        return True, f"OK - bot '{name}' token works"
    if r.status_code in (401, 403):
        return False, "token refused (Bot -> Reset Token, copy again)"
    return False, f"error {r.status_code}"


def test_key(provider: str, key: str, base_url: str = "", model: str = "") -> tuple[bool, str]:
    """Check this key with the provider. Never logs the key."""
    import httpx
    key = (key or "").strip()
    if not key:
        return False, "no key"
    if provider == "discord":                  # KAKA update 20
        return discord_test(key)
    p = named(provider)
    if p:
        base = named_base(provider)
        if not base:
            return False, "Account ID needed (second box)"
        return _chat_test(base, key, (model or named_model(provider)).strip(),
                          p.get("headers"))
    _label, dbase, _m = PROVIDERS.get(provider, PROVIDERS["openai"])
    base = (base_url or dbase).rstrip("/")
    if not base:
        return False, "address (base URL) needed"
    try:
        if provider == "gemini":
            r = httpx.get(f"{base}/v1beta/models", headers={"x-goog-api-key": key}, timeout=12)
        else:
            r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=12)
    except Exception as e:
        return False, f"no connection ({type(e).__name__})"
    if r.status_code == 200:
        try:
            n = len((r.json() or {}).get("data") or (r.json() or {}).get("models") or [])
        except Exception:
            n = 0
        return True, f"OK - key works ({n} models)" if n else "OK - key works"
    if r.status_code in (401, 403):
        return False, "key refused (wrong or disabled key)"
    if r.status_code in (404, 405) and model:
        return _chat_test(base, key, model)
    return False, f"error {r.status_code}"


# -- main Gemini key, "use first" ----------------------------------------------------
BRAIN_CHOICES = ("auto", "gemini", "groq") + NAMED_IDS + tuple(
    f"slot{i}" for i in range(1, len(SLOT_VARS) + 1))


def _write_cfg(**fields) -> None:
    """Read-modify-write config/api_keys.json, atomically. Refuses to write
    when the file exists but cannot be read, so a damaged file is never
    replaced by one without his Gemini key."""
    data: dict = {}
    if CFG.exists():
        raw = CFG.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("settings file is not readable")
    data.update(fields)
    CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CFG.with_name(CFG.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
    os.replace(tmp, CFG)


def gemini_main_key() -> str:
    return str(_read_cfg().get("gemini_api_key") or "").strip()


def masked_gemini() -> str:
    v = gemini_main_key()
    if not v:
        return "not set"
    return "saved \u2022\u2022\u2022\u2022" + v[-4:]


def _gemini_changed() -> None:
    try:                                   # core/gemini.py caches the key
        from core import gemini as _g
        _g.api_key(refresh=True)
    except Exception:
        pass


def set_gemini_main(value: str) -> None:
    value = (value or "").strip()
    if not value:
        raise ValueError("empty key")
    fields = {"gemini_api_key": value}
    if not str(_read_cfg().get("os_system") or ""):
        fields["os_system"] = "windows"    # the first-run screen checks both
    _write_cfg(**fields)
    _gemini_changed()


def clear_gemini_main() -> None:
    _write_cfg(gemini_api_key="")
    _gemini_changed()


def brain_first() -> str:
    """'auto' or the row he pressed USE on. An old 'local' (Update 4's SIRF
    LOCAL AI) reads as 'auto' - there is no local AI any more."""
    v = str(_read_cfg().get("kaka_brain_first") or "auto").strip().lower()
    return v if v in BRAIN_CHOICES else "auto"


def set_brain_first(choice: str) -> None:
    choice = (choice or "auto").strip().lower()
    _write_cfg(kaka_brain_first=choice if choice in BRAIN_CHOICES else "auto")
    _combo_reset()


def local_only() -> bool:
    """Update 5: local AI was removed - always False (kept for old callers)."""
    return False


def data_dir() -> Path:
    try:
        import kaka_paths
        return Path(kaka_paths.DATA_ROOT)
    except Exception:
        pass
    env = os.environ.get("KAKA_DATA", "").strip()
    if env:
        return Path(env)
    return HERE.parent.parent / "Data"


# -- VOICE switch ---------------------------------------------------------------
#   kaka_voice_mode  "brian"   = ElevenLabs Brian (internet); if it fails, that
#                                reply is spoken by the Windows voice
#                    "offline" = WINDOWS AWAAZ: the offline Windows voice only
VOICE_CHOICES = ("brian", "offline")
_combo_cache = [0.0, -1.0, "brian"]          # checked-at, mtime, voice mode


def _combo() -> str:
    import time as _t
    now = _t.monotonic()
    if now - _combo_cache[0] < 0.5:
        return _combo_cache[2]
    _combo_cache[0] = now
    try:
        m = CFG.stat().st_mtime
    except OSError:
        _combo_cache[1], _combo_cache[2] = -1.0, "brian"
        return _combo_cache[2]
    if m != _combo_cache[1]:
        _combo_cache[1] = m
        try:
            v = str(_read_cfg().get("kaka_voice_mode") or "brian").strip().lower()
            _combo_cache[2] = v if v in VOICE_CHOICES else "brian"
        except Exception:
            pass
    return _combo_cache[2]


def _combo_reset() -> None:
    _combo_cache[0], _combo_cache[1] = 0.0, -1.0


def voice_mode() -> str:
    """'brian' (default) or 'offline' (WINDOWS AWAAZ)."""
    return _combo()


def set_voice_mode(choice: str) -> None:
    choice = (choice or "brian").strip().lower()
    if choice == "windows":
        choice = "offline"
    _write_cfg(kaka_voice_mode=choice if choice in VOICE_CHOICES else "brian")
    _combo_reset()


def offline_voice() -> str:
    """The PC voice is always the Windows voice now (Piper was removed)."""
    return "windows"


def offline_voice_label(v: str = "") -> str:
    return "Windows voice"


def combo_tag() -> str:
    """Tag next to the KAKA wordmark: only when WINDOWS AWAAZ is chosen."""
    return "\u00b7 WINDOWS AWAAZ" if _combo() == "offline" else ""
