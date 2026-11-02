"""
Kaka's ears - API only (KAKA EDITION, Update 5, Nov 2026).

      1. The brain itself hears the voice. The pipeline engine sends the clip of
         what was said straight to the Gemini model that answers (one call hears
         AND acts) - see `brain_hears` and engine_pipeline.flush_microphone.
      2. Beside that, a transcript for the "Suna: ..." line and for brains that
         cannot hear (Groq, Cerebras, the other free keys):
           - Gemini transcription (lite models, their own free quota), and
           - Groq speech-to-text (whisper-large-v3-turbo) with his Groq key.
         Gemini first while a Gemini brain answers; Groq first when he picked a
         non-Gemini key with USE (it is the faster one for those).
      3. Neither available: a Hinglish line pointing to Settings > API KEYS.

    Update 5 removed Whisper on this PC (faster-whisper) - nothing here loads
    a local speech model any more. Keys are read from where they are kept.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from importlib.util import find_spec

from . import audio
from .base import DEFAULT_CAPS, Field

DEFAULT_EAR_MODELS = ("gemini-3.5-flash-lite, gemini-3.1-flash-lite, "
                      "gemini-3.1-flash-lite-preview, gemini-flash-lite-latest, "
                      "gemini-3.5-flash")

# Kept short: Whisper's prompt is a style hint, and a long one makes it repeat
# words from the hint when the audio is unclear.
WHISPER_HINT = ("Kaka, YouTube kholo. Chrome mein Gmail kholo. Volume thoda kam karo. "
                "Aaj ka weather batao. Arijit Singh ka gaana bajao. Ek reminder lagao "
                "5 baje ka. WhatsApp pe message bhejo. Screenshot le lo. Tumi ke? "
                "Haan, nahi, theek hai, achha, bhai.")

TRANSCRIBE_PROMPT = (
    "Transcribe exactly what the speaker says in this audio clip. The speaker is "
    "from Kolkata, India, talking to a voice assistant named Kaka, in Hinglish "
    "(Hindi mixed with English), Hindi, English or Bengali. Write it in Latin "
    "script (Roman Hinglish, e.g. 'YouTube kholo', 'volume thoda kam karo', "
    "'tumi ke?'); keep English words, names and app names in their normal English "
    "spelling. Do not translate, do not answer, do not add anything. If there are "
    "no clear spoken words, output nothing.")

# A Gemini transcription that takes longer than this is abandoned.
_GEMINI_DEADLINE_S = 6.0

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3-turbo"
# Groq's Whisper takes a short hint ("prompt") - Kaka's words help Hinglish.
GROQ_HINT = ("Kaka, YouTube kholo. Chrome mein Gmail kholo. Volume thoda kam karo. "
             "Aaj ka weather batao. Arijit Singh ka gaana bajao. Theek hai, achha, bhai.")
NO_EAR_LINE = ("Sunne ke liye API key chahiye: Settings (gear) > API KEYS mein Gemini ya "
               "Groq key daalo.")


_FOREIGN_RE = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3040-\u30ff"
                         "\u31f0-\u31ff\u4e00-\u9fff\u3400-\u4dbf]")


def _foreign_script(text: str) -> bool:
    """Mostly Korean/Japanese/Chinese script: a mis-hearing of his Hindi."""
    letters = [c for c in (text or "") if c.isalpha()]
    return len(letters) >= 2 and sum(1 for c in letters if _FOREIGN_RE.match(c)) / len(letters) >= 0.3


class KakaEar:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg or {})
        self.groq_model = str(self.cfg.get("groq_stt_model") or GROQ_STT_MODEL).strip()
        self._groq_rest_until = 0.0
        self._said_noear = 0.0
        models = str(self.cfg.get("ear_models") or DEFAULT_EAR_MODELS)
        self.models = [m.strip() for m in models.split(",") if m.strip()]
        self.base = str(self.cfg.get("gemini_base") or "https://generativelanguage.googleapis.com").rstrip("/")
        self.brain_hears = str(self.cfg.get("brain_hears", "1")) not in ("0", "", "False", "false")
        self.use_gemini = str(self.cfg.get("gemini_ear", "1")) not in ("0", "", "False", "false")
        self.deadline = float(self.cfg.get("gemini_deadline") or _GEMINI_DEADLINE_S)
        self.caps = dict(DEFAULT_CAPS)
        self._rest: dict[str, float] = {}
        self.last_source = ""
        self.last_error = ""
        self._log = self.cfg.get("log") if callable(self.cfg.get("log")) else None

    # Language guess, for voices that take one (none now - Gemini/Groq decide).
    @property
    def detected_language(self) -> str:
        return ""

    # -- Gemini ---------------------------------------------------------------
    def _key(self) -> str:
        from .llm_kaka import _gemini_key
        return _gemini_key()

    def _url(self, model: str) -> str:
        return f"{self.base}/v1beta/models/{model}:generateContent"

    def _headers(self, key: str = "") -> dict:
        return {"Content-Type": "application/json", "x-goog-api-key": key or self._key()}

    def _keys(self) -> list:
        """The main Gemini key, then a second one from a spare slot (if any)."""
        out = [("1", self._key())]
        try:
            import kaka_keys
            for i, s in enumerate(kaka_keys.slots(), 2):
                if s["provider"] == "gemini":
                    k = kaka_keys.get_secret(s["var"])
                    if k:
                        out.append((str(i), k))
        except Exception:
            pass
        return [(n, k) for n, k in out if k]

    async def gemini_transcribe(self, pcm: bytes, sample_rate: int = audio.MIC_RATE) -> str:
        """One Gemini transcription, trying each model in turn. Raises when
        none answered."""
        import httpx
        from .llm_kaka import _classify, _retry_after, _REST
        if not self._keys():
            raise RuntimeError("no Gemini key saved")
        wav = audio.to_wav(pcm, rate=sample_rate)
        b64 = base64.b64encode(wav).decode("ascii")
        last = "no model"
        now = time.monotonic()
        pairs = [(n, k, m) for n, k in self._keys() for m in self.models]
        order = [p for p in pairs if self._rest.get(p[0] + ":" + p[2], 0) <= now] or pairs
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.deadline, connect=5.0)) as cl:
            for kname, key, model in order[:4]:
                body = {"contents": [{"role": "user", "parts": [
                            {"text": TRANSCRIBE_PROMPT},
                            {"inlineData": {"mimeType": "audio/wav", "data": b64}}]}],
                        "generationConfig": {"temperature": 0,
                                             "thinkingConfig": {"thinkingLevel": "minimal"}}}
                try:
                    r = await cl.post(self._url(model), headers=self._headers(key), json=body)
                    if r.status_code == 400 and "thinking" in r.text.lower():
                        body["generationConfig"].pop("thinkingConfig", None)
                        r = await cl.post(self._url(model), headers=self._headers(key), json=body)
                    if r.status_code >= 400:
                        raise RuntimeError(f"{r.status_code} {r.text[:2000]}")
                    data = r.json()
                except Exception as e:
                    text = f"{type(e).__name__}: {e}"
                    why = _classify(text)
                    rest = _REST.get(why, 300)
                    if why == "quota":
                        asked = _retry_after(text)
                        if asked > 0:
                            rest = max(30.0, min(asked, 24 * 3600.0))
                    self._rest[kname + ":" + model] = time.monotonic() + rest
                    last = f"{model}: {why}"
                    if why in ("net", "busy"):
                        break       # slow/overloaded: Groq now, not another wait
                    continue
                cands = data.get("candidates") or [{}]
                parts = (cands[0].get("content") or {}).get("parts") or []
                txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                self.last_source = "Gemini " + model
                txt = " ".join(txt.split()).strip().strip('"')
                if _foreign_script(txt):
                    # KAKA update 8: never pass Korean/CJK text on as his words.
                    last = f"{model}: foreign-script transcript"
                    continue
                return txt
        raise RuntimeError(last)

    # -- Groq speech-to-text ------------------------------------------------------
    @staticmethod
    def _groq_key() -> str:
        from .llm_kaka import _user_env
        return _user_env("GROQ_API_KEY")

    async def groq_transcribe(self, pcm: bytes, sample_rate: int = audio.MIC_RATE) -> str:
        """Groq's hosted Whisper (large-v3-turbo). Raises when it cannot."""
        import httpx
        key = self._groq_key()
        if not key:
            raise RuntimeError("no Groq key saved")
        if time.monotonic() < self._groq_rest_until:
            raise RuntimeError("Groq speech-to-text is resting (limit)")
        wav = audio.to_wav(pcm, rate=sample_rate)
        files = {"file": ("kaka.wav", wav, "audio/wav")}
        data = {"model": self.groq_model, "response_format": "json",
                "temperature": "0", "prompt": GROQ_HINT}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as cl:
                r = await cl.post(self.groq_url, headers={"Authorization": f"Bearer {key}"},
                                  data=data, files=files)
        except Exception as e:
            raise RuntimeError(f"Groq: no connection ({type(e).__name__})") from None
        if r.status_code == 429:
            from .llm_kaka import _retry_after
            wait = _retry_after(r.text) or float(r.headers.get("retry-after") or 60)
            self._groq_rest_until = time.monotonic() + max(10.0, min(wait, 24 * 3600.0))
            raise RuntimeError("Groq speech-to-text: free limit reached")
        if r.status_code >= 400:
            if r.status_code in (401, 403):
                self._groq_rest_until = time.monotonic() + 600
            raise RuntimeError(f"Groq speech-to-text error {r.status_code}")
        try:
            txt = str((r.json() or {}).get("text") or "")
        except Exception:
            txt = r.text
        txt = " ".join(txt.split()).strip()
        if txt and _foreign_script(txt) and "language" not in data:
            # KAKA update 8: Whisper guessed Korean/Japanese/Chinese for his
            # Hindi. Ask again with the language fixed to Hindi.
            data = dict(data, language="hi")
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as cl:
                    r2 = await cl.post(self.groq_url, headers={"Authorization": f"Bearer {key}"},
                                       data=data, files={"file": ("kaka.wav", wav, "audio/wav")})
                if r2.status_code < 400:
                    txt = " ".join(str((r2.json() or {}).get("text") or "").split()).strip()
            except Exception:
                pass
            if _foreign_script(txt):
                txt = ""
        if txt and txt in GROQ_HINT and len(txt) > 25:
            txt = ""            # Whisper echoed its hint back on unclear audio
        self.last_source = "Groq " + self.groq_model
        return txt

    @property
    def groq_url(self) -> str:
        return str(self.cfg.get("groq_stt_url") or GROQ_STT_URL)

    @staticmethod
    def _groq_first() -> bool:
        """He pressed USE on a non-Gemini key: those brains read words, and
        Groq's speech-to-text is the quick way to make them."""
        try:
            import kaka_keys
            return kaka_keys.brain_first() not in ("auto", "gemini")
        except Exception:
            return False

    def _no_ear(self, why: str) -> RuntimeError:
        now = time.monotonic()
        if now - self._said_noear > 60:
            self._said_noear = now
            print(f"[Kaka ear] {NO_EAR_LINE} ({why})")
            if self._log:
                try:
                    self._log("SYS: " + NO_EAR_LINE)
                except Exception:
                    pass
        return RuntimeError(NO_EAR_LINE)

    # -- the contract -----------------------------------------------------------
    async def transcribe(self, pcm: bytes, sample_rate: int = audio.MIC_RATE,
                         language: str = "", log=None) -> str:
        if not pcm or audio.duration_s(pcm, sample_rate) < 0.25:
            return ""
        if callable(log) and not self._log:
            self._log = log
        gem = self.use_gemini and bool(self._keys())
        groq = bool(self._groq_key())
        if not gem and not groq:
            raise self._no_ear("no Gemini or Groq key")

        async def by_gemini():
            return await asyncio.wait_for(self.gemini_transcribe(pcm, sample_rate),
                                          self.deadline + 1.0)

        async def by_groq():
            return await self.groq_transcribe(pcm, sample_rate)

        ways = [("Gemini", by_gemini)] if gem else []
        if groq:
            ways.insert(0 if self._groq_first() else len(ways), ("Groq", by_groq))
        errors = []
        for name, fn in ways:
            try:
                return await fn()
            except Exception as e:
                self.last_error = str(e)[:120] or type(e).__name__
                errors.append(f"{name}: {self.last_error}")
                print(f"[Kaka ear] {name} did not transcribe ({self.last_error})")
        if not groq:
            raise self._no_ear("Gemini failed and no Groq key - " + "; ".join(errors))
        raise RuntimeError("; ".join(errors) or "no transcription")

    async def warmup(self, log=None) -> None:
        if callable(log):
            self._log = log
        return None

    def probe(self) -> tuple[bool, str]:
        g = bool(self._key())
        q = bool(self._groq_key())
        if not g and not q:
            return False, NO_EAR_LINE
        parts = []
        if g:
            parts.append("Gemini ear on")
        if q:
            parts.append(f"Groq speech-to-text ({self.groq_model})")
        return True, " + ".join(parts)


def _make(cfg: dict) -> KakaEar:
    return KakaEar(cfg)


PROVIDER = {
    "id": "kaka_ear",
    "kind": "stt",
    "label": "Kaka ears (Gemini + Groq speech-to-text)",
    "hint": "Gemini hears Hinglish/Bengali; Groq's Whisper (API) as backup",
    "requires": ("httpx",),
    "catalog": (
        {"id": "kaka_ear", "label": "Gemini hears, Groq speech-to-text backup",
         "note": "uses your saved Gemini / Groq keys", "local": False,
         "values": {}},
    ),
    "needs": (
        Field("ear_models", "Gemini models for the 'Suna' line",
              default=DEFAULT_EAR_MODELS, required=False, advanced=True),
        Field("groq_stt_model", "Groq speech-to-text model", default=GROQ_STT_MODEL,
              required=False, advanced=True),
    ),
    "caps": {},
    "factory": _make,
}


def available() -> bool:
    return find_spec("httpx") is not None
