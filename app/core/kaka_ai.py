r"""
core/kaka_ai.py - Kaka update 18: one small, bounded Gemini helper for the
new features (screen explain, translate, VS Code fix).

    from core import kaka_ai
    ok, txt = kaka_ai.text("Translate to Bengali: ...", timeout=20)
    ok, txt = kaka_ai.vision(jpeg_bytes, "ye error kya hai?", timeout=20)

Returns (True, answer) or (False, short Hinglish reason Kaka can say).
Never raises.

HOW
  1. REST generateContent (stdlib urllib, no SDK), with the key read-only from
     config/api_keys.json (kaka_services.gemini_key). The REST model names
     come from core/gemini.py's SMART ladder (the names measured on his key);
     rungs core/gemini.py has put in cooldown are skipped, and a 429 here
     cools the rung there too, so the whole app learns from it.
  2. Only if every REST rung is out of quota / down and time is left: the Live
     one-shot rung from core/gemini.py (a different quota pool), in a thread
     we stop waiting for at the deadline.
  One overall deadline (default 20 s) bounds everything.
"""
from __future__ import annotations

import json
import threading
import time

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"
_FALLBACK_MODELS = ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite",
                    "gemini-flash-latest")
_MAX_REST_RUNGS = 8      # a 429/404 answers in ~0.2 s, so trying more rungs is cheap
                         # (free tier: 20/day on each flash model, 500/day on flash-lite)
last_model = ""          # which model answered last (for the log)


def _key() -> str:
    try:
        from core import kaka_services as ks
        return ks.gemini_key()
    except Exception:
        return ""


def _gem():
    try:
        from core import gemini
        return gemini
    except Exception:
        return None


def _models() -> list:
    g = _gem()
    names = []
    try:
        lad = g._LADDERS[g.SMART]
        names = [m for m in lad if m != g.LIVE and not str(m).startswith(g.PROVIDER_PREFIX)]
    except Exception:
        names = list(_FALLBACK_MODELS)
    if not names:
        names = list(_FALLBACK_MODELS)
    try:
        fresh = [m for m in names if not g._cooling(m)]
        names = fresh or names
    except Exception:
        pass
    return names[:_MAX_REST_RUNGS]


def _cool(model: str, kind: str) -> None:
    g = _gem()
    try:
        if kind == "quota":
            g._cool(model)
        elif kind == "gone":
            g._cool(model, g._GONE_SECONDS)
        elif kind == "down":
            g._cool(model, g._UNAVAILABLE_SECONDS)
    except Exception:
        pass


def _http(url: str, body: dict, timeout: float):
    """POST json -> (status, parsed json or text). Test hook."""
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Kaka/18"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def _answer_text(js) -> str:
    try:
        out = []
        for c in (js.get("candidates") or [])[:1]:
            for p in ((c.get("content") or {}).get("parts") or []):
                if p.get("text") and not p.get("thought"):
                    out.append(p["text"])
        return "".join(out).strip()
    except Exception:
        return ""


def _classify(status, js) -> str:
    msg = json.dumps(js)[:600] if not isinstance(js, str) else js[:600]
    if status == 429 or "RESOURCE_EXHAUSTED" in msg:
        return "quota"
    if status in (401, 403) or "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return "key"
    if status == 404 or "is not found" in msg:
        return "gone"
    if status in (500, 502, 503, 504):
        return "down"
    return "other"


_REASONS = {
    "nokey": "Gemini key nahi mili (config/api_keys.json mein gemini_api_key)",
    "key": "Gemini key reject ho gayi (galat ya expire)",
    "quota": "Gemini ka free quota abhi khatam hai, thodi der baad try karo",
    "offline": "internet nahi lag raha, Gemini tak nahi pahunch paaya",
    "timeout": "Gemini ne time pe jawab nahi diya",
    "down": "Gemini abhi down hai, thodi der baad try karo",
    "blocked": "Gemini ne is cheez ka jawab dene se mana kar diya",
    "empty": "Gemini ne khaali jawab diya",
    "other": "Gemini se jawab nahi mila",
}


def _live_fallback(parts: list, system: str, key: str, budget: float):
    """Live one-shot rung (separate quota pool). Abandoned at the deadline."""
    g = _gem()
    if g is None or budget < 3.0:
        return None
    contents = []
    for p in parts:
        if "text" in p:
            contents.append(p["text"])
        else:
            contents.append(p)
    box = {}

    def _run():
        try:
            cfg = {"system_instruction": system} if system else None
            r = g._live_call(contents, cfg, int(budget * 1000), key)
            box["t"] = (getattr(r, "text", "") or "").strip() if r is not None else ""
        except BaseException as e:      # noqa: BLE001
            box["e"] = str(e)

    th = threading.Thread(target=_run, daemon=True, name="kaka-ai-live")
    th.start()
    th.join(budget)
    return box.get("t") or None


def generate(parts: list, system: str = "", timeout: float = 20.0,
             temperature: float = 0.3) -> tuple:
    """parts = REST parts ([{"text":..}, {"inline_data":{...}}]). (ok, text)."""
    global last_model
    t0 = time.monotonic()
    deadline = t0 + max(3.0, float(timeout))
    key = _key()
    if not key:
        return False, _REASONS["nokey"]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    kinds = []
    for m in _models():
        left = deadline - time.monotonic()
        if left < 1.5:
            kinds.append("timeout")
            break
        try:
            status, js = _http(_URL.format(m=m, k=key), body, min(left, 15.0))
        except Exception as e:
            s = str(e).lower()
            if "timed out" in s or "timeout" in s:
                kinds.append("timeout")
                continue
            kinds.append("offline")
            break                       # no network: no point trying more rungs
        if status == 200 and isinstance(js, dict):
            txt = _answer_text(js)
            if txt:
                last_model = m
                return True, txt
            fb = json.dumps(js)[:400]
            kinds.append("blocked" if ("SAFETY" in fb or "PROHIBITED" in fb or "blockReason" in fb) else "empty")
            if kinds[-1] == "blocked":
                break
            continue
        k = _classify(status, js)
        kinds.append(k)
        _cool(m, k)
        if k == "key":
            break
    # REST could not answer: the Live rung draws on a different quota pool.
    if kinds and all(k in ("quota", "down", "gone", "empty", "timeout") for k in kinds):
        left = deadline - time.monotonic()
        txt = _live_fallback(parts, system, key, left)
        if txt:
            last_model = "live"
            return True, txt
    for k in ("key", "offline", "blocked", "quota", "timeout", "down", "empty"):
        if k in kinds:
            return False, _REASONS[k]
    return False, _REASONS["other"]


def text(prompt: str, timeout: float = 20.0, system: str = "") -> tuple:
    return generate([{"text": str(prompt)}], system=system, timeout=timeout)


def vision(image_bytes: bytes, prompt: str, timeout: float = 20.0,
           mime: str = "image/jpeg", system: str = "") -> tuple:
    import base64
    if not image_bytes:
        return False, "screenshot nahi mila"
    parts = [{"inline_data": {"mime_type": mime,
                              "data": base64.b64encode(image_bytes).decode("ascii")}},
             {"text": str(prompt)}]
    return generate(parts, system=system, timeout=timeout)
