"""
Kaka's brain chain - API models only (KAKA EDITION, Update 5, Nov 2026).

    1. Gemini, with the key the app already saved (config/api_keys.json).
       Spoken to through Google's own REST API (not the OpenAI-compatible one)
       because Gemini 3 models need their "thought signatures" handed back on
       every tool call; this file keeps them. Several models in turn - each
       has its own free daily quota.
    2. A 2nd Gemini key from a spare slot, if one is saved.
    3. Groq (GROQ_API_KEY), then Cerebras and the other free providers in
       Settings > API KEYS (kaka_keys.NAMED) - each only while its key is set.
    4. The two spare key slots (any OpenAI-compatible address).

    Update 5 removed the AI on this PC (Ollama): nothing here talks to
    127.0.0.1 any more. A rung that fails BEFORE it has said anything (quota
    429, model gone, overloaded, no network) is rested for a while (until the
    time the provider names, for a daily limit) and the next rung answers the
    same request. Keys are read from where they are kept, never copied.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from importlib.util import find_spec
from typing import Any, AsyncIterator, Optional

from .base import DEFAULT_CAPS, Field, FunctionCall

GEMINI_BASE = "https://generativelanguage.googleapis.com"
# Strong models first (they understand Hinglish voice commands best), then the
# lite models as the quota fallback.  On a free key every model has its own
# daily allowance: the flash models about 20 requests a day, the lite models
# about 500 - so a busy day starts on flash and moves to lite by itself.
DEFAULT_GEMINI_MODELS = ("gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, "
                         "gemini-3-flash-preview, gemini-flash-latest, gemini-3.5-flash-lite, "
                         "gemini-3.1-flash-lite, gemini-3.1-flash-lite-preview, "
                         "gemini-flash-lite-latest")
# Update 2-4 shipped this list; a settings file holding exactly it gets the
# new one (Google's free list on 1 Nov 2026 starts with 3.8 / 3.6 Flash).
_U2_DEFAULT_MODELS = ("gemini-3.5-flash, gemini-3-flash-preview, "
                      "gemini-flash-latest, gemini-3.5-flash-lite, "
                      "gemini-3.1-flash-lite, gemini-3.1-flash-lite-preview, "
                      "gemini-flash-lite-latest")
# The order shipped by the first Kaka upgrade (lite first). A settings file that
# still carries exactly this string gets the new order.
_OLD_DEFAULT_MODELS = ("gemini-3.5-flash-lite, gemini-3.1-flash-lite, "
                       "gemini-flash-lite-latest, gemini-3.5-flash, "
                       "gemini-flash-latest")
GROQ_BASE = "https://api.groq.com/openai/v1"
# Update 5: llama-3.3-70b-versatile is no longer on Groq's free plan (1 Nov
# 2026); gpt-oss-20b is, with tool calling. An old saved choice is upgraded.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
_OLD_GROQ_MODELS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant")
DUMMY_SIGNATURE = "skip_thought_signature_validator"

# How long a rung rests after each kind of failure (seconds).
_REST = {"quota": 15 * 60, "gone": 6 * 60 * 60, "auth": 6 * 60 * 60,
         "busy": 2 * 60, "net": 60, "other": 5 * 60}
def _retry_after(err: str) -> float:
    """Seconds Google asks us to wait (RetryInfo / 'Please retry in 23h27m'), or 0."""
    import re
    m = re.search(r"retryDelay\W+(\d+(?:\.\d+)?)s", err)
    if m:
        return float(m.group(1))
    if re.search(r"(?:retry|try again) in\s+\d+(?:\.\d+)?ms", err, re.I):
        return 1.0
    # Google: "Please retry in 23h27m"; Groq/OpenAI-style: "Please try again in 7m12.5s"
    m = re.search(r"(?:retry|try again) in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:(\d+(?:\.\d+)?)s)?", err, re.I)
    if m and any(m.groups()):
        h, mi, s = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + s
    return 0.0


def _classify(err: str) -> str:
    low = err.lower()
    if "429" in err or "resource_exhausted" in low or "quota" in low or "rate limit" in low:
        return "quota"
    if ("404" in err or "not found" in low or "no longer available" in low
            or "not supported" in low):
        return "gone"
    if "401" in err or "403" in err or "api key" in low or "permission" in low:
        return "auth"
    if "503" in err or "504" in err or "500" in err or "unavailable" in low or "overloaded" in low:
        return "busy"
    if ("connect" in low or "timed out" in low or "timeout" in low
            or "getaddrinfo" in low or "network" in low):
        return "net"
    return "other"


class GeminiHTTPError(RuntimeError):
    """A refusal from Google, short text for the log plus the full body (which
    carries the RetryInfo telling us how long a quota lasts)."""

    def __init__(self, short: str, full: str = ""):
        super().__init__(short)
        self.full = full or short


def _thinking_level(model: str) -> str:
    # Spoken commands need a fast first word: lite models think minimally,
    # flash models a little (helps with unclear Hinglish), never at length.
    return "minimal" if "lite" in model else "low"


def _user_env(name: str) -> str:
    """os.environ first; on Windows also the per-user registry, so a key set
    with `setx` reaches a process that was started before it was set."""
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


def _gemini_key() -> str:
    try:
        from memory.config_manager import get_gemini_key
        return (get_gemini_key() or "").strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  OpenAI-shaped history  ->  Gemini REST
# ─────────────────────────────────────────────────────────────────────────────
def _parts_of(content: Any) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    out = []
    for p in content if isinstance(content, list) else [content]:
        if isinstance(p, str):
            out.append({"text": p})
            continue
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and p.get("text"):
            out.append({"text": p["text"]})
        elif p.get("type") == "input_audio":
            # The user's own voice for this turn (Kaka edition): Gemini hears it
            # directly instead of reading a transcript of it.
            a = p.get("input_audio") or {}
            if a.get("data"):
                fmt = str(a.get("format") or "wav").lower()
                out.append({"inlineData": {"mimeType": "audio/" + fmt, "data": a["data"]}})
        elif p.get("type") == "image_url":
            url = (p.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                mime, data = url[5:].split(";base64,", 1)
                out.append({"inlineData": {"mimeType": mime or "image/png", "data": data}})
    return out


def to_gemini(messages: list[dict], signatures: dict) -> tuple[Optional[dict], list[dict]]:
    system_txt: list[str] = []
    contents: list[dict] = []
    names: dict[str, str] = {}

    def push(role: str, parts: list[dict]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": list(parts)})

    for m in messages:
        role = m.get("role")
        if role == "system":
            t = m.get("content")
            if isinstance(t, str) and t:
                system_txt.append(t)
            else:
                system_txt += [p["text"] for p in _parts_of(t) if "text" in p]
        elif role == "user":
            push("user", _parts_of(m.get("content")))
        elif role == "assistant":
            parts = _parts_of(m.get("content"))
            for i, tc in enumerate(m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                cid = tc.get("id") or ""
                names[cid] = fn.get("name", "")
                part: dict[str, Any] = {"functionCall": {"name": fn.get("name", ""),
                                                         "args": args}}
                if cid:
                    part["functionCall"]["id"] = cid
                sig = signatures.get(cid)
                if sig:
                    part["thoughtSignature"] = sig
                elif i == 0:
                    part["thoughtSignature"] = DUMMY_SIGNATURE
                parts.append(part)
            push("model", parts)
        elif role == "tool":
            cid = m.get("tool_call_id") or ""
            name = m.get("name") or names.get(cid, "") or "tool"
            content = m.get("content")
            if not isinstance(content, str):
                try:
                    content = json.dumps(content)
                except Exception:
                    content = str(content)
            fr: dict[str, Any] = {"name": name, "response": {"result": content}}
            if cid:
                fr["id"] = cid
            push("user", [{"functionResponse": fr}])
    if contents and contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": "(continue)"}]})
    sys_part = {"parts": [{"text": "\n\n".join(system_txt)}]} if system_txt else None
    return sys_part, contents


def tools_to_gemini(tools: list[dict] | None) -> list[dict]:
    decls = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if not fn or not fn.get("name"):
            continue
        d: dict[str, Any] = {"name": fn["name"], "description": fn.get("description", "")}
        params = fn.get("parameters")
        if isinstance(params, dict) and params.get("properties"):
            d["parametersJsonSchema"] = params
        decls.append(d)
    return [{"functionDeclarations": decls}] if decls else []


class _GeminiRung:
    kind = "gemini"

    def __init__(self, model: str, base: str, timeout: float = 60.0,
                 key_fn=None, label: str = ""):
        self.model = model
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._key_fn = key_fn or _gemini_key
        # A model that has not started answering within this many seconds is
        # treated as busy and the next one is asked (seen: 30 s stalls on a
        # free key at busy hours - far too long for a spoken command).
        self.first_byte_timeout = 12.0
        self.name = f"Gemini {label + ' ' if label else ''}{model}"
        self.signatures: dict[str, str] = {}
        self._thinking_ok = True

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-goog-api-key": self._key_fn()}

    def ready(self) -> bool:
        return bool(self._key_fn())

    def _url(self, method: str) -> str:
        return f"{self.base}/v1beta/models/{self.model}:{method}"

    def _timeout(self, total=None):
        import httpx
        total = total or self.timeout
        return httpx.Timeout(total, connect=10.0, read=min(total, self.first_byte_timeout))

    def _body(self, messages, tools, temperature) -> dict:
        sys_part, contents = to_gemini(messages, self.signatures)
        body: dict[str, Any] = {"contents": contents}
        if sys_part:
            body["systemInstruction"] = sys_part
        gt = tools_to_gemini(tools)
        if gt:
            body["tools"] = gt
        gen: dict[str, Any] = {}
        if temperature is not None:
            gen["temperature"] = temperature
        if self._thinking_ok:
            gen["thinkingConfig"] = {"thinkingLevel": _thinking_level(self.model)}
        if gen:
            body["generationConfig"] = gen
        return body

    def _refused(self, status: int, detail: str) -> Exception:
        """Build the error; a model that rejects the thinking setting is asked
        again without it (returns None so the caller retries)."""
        if status == 400 and self._thinking_ok and "thinking" in detail.lower():
            self._thinking_ok = False
            return None
        return GeminiHTTPError(f"{status} {_terse(detail)}", detail)

    def _parse_parts(self, parts, calls: list) -> str:
        text = ""
        for p in parts or []:
            if p.get("thought"):
                continue
            if "functionCall" in p:
                fc = p["functionCall"] or {}
                cid = fc.get("id") or f"call_{uuid.uuid4().hex[:10]}"
                if p.get("thoughtSignature"):
                    self.signatures[cid] = p["thoughtSignature"]
                args = fc.get("args") or {}
                if not isinstance(args, dict):
                    args = {"value": args}
                calls.append(FunctionCall(id=cid, name=fc.get("name", ""), args=args))
            elif p.get("text"):
                text += p["text"]
        if len(self.signatures) > 400:          # keep the store small
            for k in list(self.signatures)[:200]:
                self.signatures.pop(k, None)
        return text

    async def chat(self, messages, tools=None, *, temperature=None):
        import httpx
        if not self._key_fn():
            raise RuntimeError("401 no Gemini API key saved")
        url = self._url("streamGenerateContent") + "?alt=sse"
        calls: list[FunctionCall] = []
        async with httpx.AsyncClient(timeout=self._timeout()) as cl:
            for _attempt in (1, 2):
                body = self._body(messages, tools, temperature)
                async with cl.stream("POST", url, headers=self._headers(), json=body) as r:
                    if r.status_code >= 400:
                        detail = (await r.aread()).decode("utf-8", "replace")
                        err = self._refused(r.status_code, detail)
                        if err is None:
                            continue
                        raise err
                    async for chunk in self._sse(r, calls):
                        yield chunk
                    break
        if calls:
            yield {"tool_calls": calls}

    async def _sse(self, r, calls):
        async for line in r.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except Exception:
                continue
            cands = chunk.get("candidates") or []
            if not cands:
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = self._parse_parts(parts, calls)
            if text:
                yield {"text": text}

    def complete(self, messages, tools=None, *, timeout=None, temperature=None):
        import httpx
        if not self._key_fn():
            raise RuntimeError("401 no Gemini API key saved")
        url = self._url("generateContent")
        with httpx.Client(timeout=self._timeout(timeout)) as cl:
            for _attempt in (1, 2):
                r = cl.post(url, headers=self._headers(),
                            json=self._body(messages, tools, temperature))
                if r.status_code >= 400:
                    err = self._refused(r.status_code, r.text)
                    if err is None:
                        continue
                    raise err
                break
            data = r.json()
        calls: list[FunctionCall] = []
        cands = data.get("candidates") or [{}]
        text = self._parse_parts((cands[0].get("content") or {}).get("parts"), calls)
        return text, calls


def _flatten_audio(messages: list[dict]) -> list[dict]:
    """A brain that cannot hear gets the words instead of the voice clip: each
    input_audio part becomes its transcript (filled in by the engine)."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "input_audio" for p in c):
            parts = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "input_audio":
                    t = str(p.get("transcript") or "").strip()
                    parts.append({"type": "text", "text": t or "(the user's voice was not understood)"})
                elif isinstance(p, dict) and p.get("type") == "text" and p.get("audio_note"):
                    continue          # the "listen to the clip" note means nothing here
                else:
                    parts.append(p)
            if all(isinstance(p, dict) and p.get("type") == "text" for p in parts):
                m = dict(m, content=" ".join(p["text"] for p in parts))
            else:
                m = dict(m, content=parts)
        out.append(m)
    return out


class _OpenAIRung:
    """One OpenAI-compatible provider (Groq, Cerebras, OpenRouter, a spare
    slot...). The key is read from Windows each time, so a key typed into
    the settings panel works at once, without a restart."""
    kind = "openai"
    local = False

    def __init__(self, name: str, cfg: dict, local: bool = False, key_env: str = "",
                 model_fn=None):
        self.name = name
        self.cfg = dict(cfg)
        self.key_env = key_env
        self.model_fn = model_fn
        self._llm = None
        self._llm_key = None
        self._llm_model = None

    def ready(self) -> bool:
        return (not self.key_env) or bool(_user_env(self.key_env))

    def _model(self) -> str:
        if self.model_fn is not None:
            try:
                m = self.model_fn()
                if m:
                    return m
            except Exception:
                pass
        return str(self.cfg.get("model") or "")

    def _client(self):
        key = _user_env(self.key_env) if self.key_env else self.cfg.get("api_key", "")
        model = self._model()
        if self._llm is None or key != self._llm_key or model != self._llm_model:
            from .llm_openai import OpenAICompatLLM
            self._llm = OpenAICompatLLM(dict(self.cfg, api_key=key, model=model))
            extra = self.cfg.get("headers")
            if isinstance(extra, dict) and extra:
                base_headers = self._llm._headers

                def _headers(_b=base_headers, _x=dict(extra)):
                    h = _b()
                    h.update(_x)
                    return h
                self._llm._headers = _headers
            self._llm_key = key
            self._llm_model = model
        return self._llm

    def _prep(self, messages, tools):
        return _flatten_audio(messages), tools

    async def chat(self, messages, tools=None, *, temperature=None):
        messages, tools = self._prep(messages, tools)
        async for ev in self._client().chat(messages, tools, temperature=temperature):
            yield ev

    def complete(self, messages, tools=None, *, timeout=None, temperature=None):
        messages, tools = self._prep(messages, tools)
        return self._client().complete(messages, tools, timeout=timeout, temperature=temperature)


def _terse(text: str, limit: int = 220) -> str:
    try:
        j = json.loads(text)
        if isinstance(j, list) and j:
            j = j[0]
        msg = ((j.get("error") or {}).get("message") if isinstance(j, dict) else None)
        if msg:
            return msg[:limit]
    except Exception:
        pass
    return " ".join(str(text).split())[:limit]


def _groq_model(cfg: dict) -> str:
    m = str(cfg.get("groq_model") or "").strip()
    return DEFAULT_GROQ_MODEL if (not m or m in _OLD_GROQ_MODELS) else m


# Rest times are shared by every chain in the app (the voice brain and the
# app's own background requests), so a provider whose free limit is used up
# is skipped everywhere until it resets.
_SHARED_REST: dict[str, float] = {}


class KakaChainLLM:
    """Tries each rung in order; a rung that fails before answering is rested."""

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg or {})
        models = str(self.cfg.get("gemini_models") or DEFAULT_GEMINI_MODELS)
        if " ".join(models.split()) in (" ".join(_OLD_DEFAULT_MODELS.split()),
                                        " ".join(_U2_DEFAULT_MODELS.split())):
            models = DEFAULT_GEMINI_MODELS
        base = str(self.cfg.get("gemini_base") or GEMINI_BASE)
        # no_gemini: the app's background requests already asked Gemini
        # themselves; their backup chain starts after it.
        self.no_gemini = str(self.cfg.get("no_gemini", "")) in ("1", "True", "true")
        self.rungs: list[Any] = [] if self.no_gemini else [
            _GeminiRung(m.strip(), base) for m in models.split(",") if m.strip()]
        if str(self.cfg.get("use_groq", "1")) not in ("0", "", "False", "false"):
            # Always present; used only while a GROQ_API_KEY exists (checked at
            # call time - see _OpenAIRung.ready).
            self.rungs.append(_OpenAIRung(
                "Groq", {"base_url": str(self.cfg.get("groq_base") or GROQ_BASE),
                         "model": _groq_model(self.cfg),
                         "timeout": 60}, key_env="GROQ_API_KEY"))
            self.rungs[-1].kaka_choice = "groq"
        self._rest = _SHARED_REST
        self.active = ""
        # Set by the pipeline engine: async (messages) -> messages with every
        # voice clip replaced by its transcript, for brains that cannot hear.
        self.audio_text = None
        self._extra_cache: dict = {}
        self._named_cache: dict = {}
        self.log = self.cfg.get("log") if callable(self.cfg.get("log")) else None
        self.caps = dict(DEFAULT_CAPS)
        self.caps.update({"tools": True, "tool_format": "openai",
                          "vision": True, "max_tools": None, "max_tool_chars": None})

    def _say(self, msg: str) -> None:
        print(f"[Kaka brain] {msg}")
        if self.log:
            try:
                self.log(f"SYS: {msg}")
            except Exception:
                pass

    def _resting(self, rung) -> bool:
        until = self._rest.get(rung.name, 0.0)
        return bool(until and time.monotonic() < until)

    @property
    def accepts_audio(self) -> bool:
        """True when a Gemini model is available to hear the user's voice
        directly (Kaka edition). The engine then sends the clip itself."""
        return any(r.kind == "gemini" and not self._resting(r) for r in self._usable())

    def _named_rungs(self) -> list:
        """Cerebras, OpenRouter, Mistral... (kaka_keys.NAMED), in AUTO order.
        All are built; each is used only while its key is set."""
        try:
            import kaka_keys
            named = kaka_keys.NAMED
        except Exception:
            return []
        out = []
        for p in named:
            base = kaka_keys.named_base(p["id"]) if hasattr(kaka_keys, "named_base") else p["base_url"]
            if not base:
                continue        # e.g. Cloudflare without its Account ID
            sig = (p["id"], base)
            if sig not in self._named_cache:
                r = _OpenAIRung(p["name"], {"base_url": base, "model": p["model"],
                                            "timeout": p.get("timeout", 45),
                                            "headers": p.get("headers") or {}},
                                key_env=p["var"],
                                model_fn=lambda pid=p["id"]: kaka_keys.named_model(pid))
                r.kaka_choice = p["id"]
                self._named_cache[sig] = r
            out.append(self._named_cache[sig])
        return out

    def _extra_rungs(self) -> tuple[list, list]:
        """The spare key slots from the settings panel (kaka_keys.py), read on
        every request so a key saved in the panel works at once. Returns
        (second-Gemini-key rungs, other rungs)."""
        gem, other = [], []
        try:
            import kaka_keys
            slots = kaka_keys.slots()
        except Exception:
            return gem, other
        models = [m.strip() for m in DEFAULT_GEMINI_MODELS.split(",")] if self.no_gemini \
            else [r.model for r in self.rungs if r.kind == "gemini"]
        for i, s in enumerate(slots, 1):
            var = s["var"]
            if not kaka_keys.get_secret(var):
                continue
            base, model = kaka_keys.slot_target(s)
            sig = (i, s["provider"], base, model)
            if s["provider"] == "gemini":
                if self.no_gemini:
                    continue
                key = (sig, "gemini")
                if key not in self._extra_cache:
                    self._extra_cache[key] = [
                        _GeminiRung(m, base or GEMINI_BASE, label=f"(key {i + 1})",
                                    key_fn=lambda v=var: kaka_keys.get_secret(v))
                        for m in ([model] if model else models)]
                    for r in self._extra_cache[key]:
                        r.kaka_slot = i
                gem.extend(self._extra_cache[key])
            else:
                if not base or not model:
                    continue
                if sig not in self._extra_cache:
                    label = kaka_keys.PROVIDERS.get(s["provider"], ("API",))[0]
                    self._extra_cache[sig] = [_OpenAIRung(
                        f"Slot {i} {label}", {"base_url": base, "model": model, "timeout": 60},
                        key_env=var)]
                    self._extra_cache[sig][0].kaka_slot = i
                other.extend(self._extra_cache[sig])
        return gem, other

    @staticmethod
    def local_only() -> bool:
        return False            # Update 5: there is no local AI any more

    async def warmup(self, log=None) -> None:
        if callable(log):
            self.log = self.log or log
        return None

    def _all_rungs(self) -> list:
        gem2, other = self._extra_rungs()
        main_gem = [r for r in self.rungs if r.kind == "gemini"]
        groq = [r for r in self.rungs if r.kind != "gemini"]
        named = self._named_rungs()
        order = main_gem + gem2 + groq + named + other
        # The key he pressed USE on goes first; the rest keep their order, so
        # a failure still falls down the chain.
        try:
            import kaka_keys
            first = kaka_keys.brain_first()
        except Exception:
            first = "auto"
        if first == "gemini":
            pick = main_gem
        elif first.startswith("slot") and first[4:].isdigit():
            n = int(first[4:])
            pick = [r for r in gem2 + other if getattr(r, "kaka_slot", 0) == n]
        elif first != "auto":
            pick = [r for r in groq + named if getattr(r, "kaka_choice", "") == first]
        else:
            pick = []
        if pick:
            order = pick + [r for r in order if r not in pick]
        return order

    def _usable(self) -> list:
        rungs = [r for r in self._all_rungs() if getattr(r, "ready", lambda: True)()]
        # KAKA update 8: a Gemini id that Google's model list (read once with
        # his key) does not offer is skipped instead of costing a 404 turn.
        try:
            from core import gemini as _g
            keep = [r for r in rungs if not (getattr(r, "kind", "") == "gemini"
                                             and getattr(r, "_key_fn", None) is _gemini_key
                                             and _g.model_known(r.model) is False)]
            rungs = keep or rungs
        except Exception:
            pass
        return rungs

    def _order(self) -> list:
        usable = self._usable()
        awake = [r for r in usable if not self._resting(r)]
        if awake:
            return awake
        # Everything resting: try them anyway, best first - never refuse - but
        # not a model whose DAILY limit is used up (asking it again is a wasted
        # round trip; it said when to come back).
        now = time.monotonic()
        soon = [r for r in usable if self._rest.get(r.name, 0.0) - now < 3600]
        return soon or usable

    def _fail(self, rung, err: Exception) -> None:
        text = f"{type(err).__name__}: {err}"
        why = _classify(text)
        rest = _REST.get(why, 300)
        if why == "quota":
            # A daily limit says "retry in 23h"; a per-minute one says seconds.
            # Rest exactly that long (at least 30 s, at most a day) so a model
            # whose day is used up is not asked again every 15 minutes.
            asked = _retry_after(getattr(err, "full", "") or text)
            if asked > 0:
                rest = max(30.0, min(asked, 24 * 3600.0))
        self._rest[rung.name] = time.monotonic() + rest
        self._say(f"{rung.name} unavailable ({why}: {(str(err) or type(err).__name__)[:90]}) — trying the next brain.")

    def _switched(self, rung) -> None:
        if rung.name != self.active:
            if self.active:
                self._say(f"Brain now: {rung.name}")
            self.active = rung.name

    def _no_brain(self, last) -> RuntimeError:
        if not self._usable():
            return RuntimeError(NO_KEY_LINE)
        return RuntimeError(f"no brain answered — last error: {last}")

    async def chat(self, messages, tools=None, *, stream: bool = True,
                   temperature: float | None = None) -> AsyncIterator[dict]:
        last: Optional[Exception] = None
        slow = 0
        for rung in self._order():
            started = False
            if rung.kind == "gemini" and slow >= 2:
                continue        # Google is slow right now: go to the next kind of brain
            if rung.kind != "gemini" and self.audio_text is not None:
                # Every Gemini model failed: the voice clip needs to become
                # words (Groq speech-to-text, or the Gemini transcript).
                try:
                    messages = await self.audio_text(messages)
                except Exception as e:
                    self._say(f"could not turn the voice into text ({e})")
            try:
                async for ev in rung.chat(messages, tools, temperature=temperature):
                    if not started:
                        started = True
                        self._switched(rung)
                    yield ev
                if not started:
                    self._switched(rung)
                return
            except Exception as e:
                if started:
                    raise
                last = e
                if rung.kind == "gemini" and _classify(f"{type(e).__name__}: {e}") in ("net", "busy"):
                    slow += 1
                self._fail(rung, e)
        raise self._no_brain(last)

    def complete(self, messages, tools=None, *, timeout=None, temperature=None):
        last: Optional[Exception] = None
        for rung in self._order():
            try:
                out = rung.complete(messages, tools, timeout=timeout, temperature=temperature)
                self._switched(rung)
                return out
            except Exception as e:
                last = e
                self._fail(rung, e)
        raise self._no_brain(last)

    def probe(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        try:
            text, _c = self.complete([{"role": "user", "content": "Reply with the single word: ready"}],
                                     timeout=60)
        except Exception as e:
            return False, str(e)[:200]
        return True, f"{self.active} answered in {time.monotonic() - t0:.2f}s: {text.strip()[:40]}"


NO_KEY_LINE = ("Koi API key nahi mili. Settings (gear) > API KEYS mein Gemini, Groq ya koi "
               "free key daalo.")


def _make(cfg: dict) -> KakaChainLLM:
    return KakaChainLLM(cfg)


PROVIDER = {
    "id": "kaka_chain",
    "kind": "llm",
    "label": "Kaka brain chain",
    "hint": "Gemini (free key) -> Groq -> other free keys (Settings > API KEYS)",
    "requires": ("httpx",),
    "catalog": (
        {"id": "kaka_chain", "label": "Kaka chain: Gemini -> Groq -> free API keys",
         "note": "uses your saved keys (Settings > API KEYS)", "local": False,
         "values": {}},
    ),
    "needs": (
        Field("gemini_models", "Gemini models (in order)", default=DEFAULT_GEMINI_MODELS,
              required=False, hint="Each has its own free daily limit"),
        Field("groq_model", "Groq model", default=DEFAULT_GROQ_MODEL, required=False,
              advanced=True, hint="Used only if GROQ_API_KEY is set in Windows"),
        Field("gemini_base", "Gemini address", default=GEMINI_BASE, required=False,
              advanced=True),
    ),
    "caps": {"tools": True, "tool_format": "openai", "vision": True},
    "factory": _make,
}


def available() -> bool:
    return find_spec("httpx") is not None
