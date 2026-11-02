"""
Kaka's voice: ElevenLabs "Brian", with this computer's own voice as the
automatic fallback.

KAKA EDITION (added for Abhinaba, Nov 2026)
    The ElevenLabs key is read from the Windows user environment variable
    ELEVENLABS_API_KEY at the moment of speaking — it is never written into the
    app's settings. When ElevenLabs refuses (free credits used up, no network,
    key missing) the same sentence is spoken by the Windows voice instead, and
    ElevenLabs is rested for a while so every sentence does not pay for the
    failed attempt.
"""
from __future__ import annotations

import time
from importlib.util import find_spec
from typing import AsyncIterator

from .base import DEFAULT_CAPS, Field

BRIAN = "nPczCjzI2devNBz1zQrb"
DEFAULT_MODEL = "eleven_flash_v2_5"


def _env(name: str) -> str:
    from .llm_kaka import _user_env
    return _user_env(name)


class KakaVoice:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg or {})
        self.voice_id = str(self.cfg.get("voice_id") or BRIAN).strip()
        self.model = str(self.cfg.get("model") or DEFAULT_MODEL).strip()
        self.caps = dict(DEFAULT_CAPS)
        self.caps["audio_out"] = True
        self._eleven = None
        self._eleven_key = ""
        self._system = None
        self._rest_until = 0.0
        self.active = ""

    # -- the two voices --------------------------------------------------------
    def _el(self):
        key = _env("ELEVENLABS_API_KEY")
        if not key:
            return None
        if self._eleven is None or key != self._eleven_key:
            from .tts_elevenlabs import ElevenLabsTTS
            self._eleven = ElevenLabsTTS({"api_key": key, "voice_id": self.voice_id,
                                          "model": self.model})
            self._eleven_key = key
        return self._eleven

    def _sys(self):
        if self._system is None:
            from .tts_system import SystemTTS
            self._system = SystemTTS({"voice": str(self.cfg.get("fallback_voice") or ""),
                                      "rate": self.cfg.get("fallback_rate") or 0})
        return self._system

    def _note(self, name: str) -> None:
        if name != self.active:
            print(f"[Kaka voice] now speaking with: {name}")
            if self.active and getattr(self, "_log", None):
                try:
                    self._log(f"SYS: Voice switched to {name}.")
                except Exception:
                    pass
            self.active = name

    # -- VOICE switch (Update 5: the PC voice is the Windows voice) -----------
    #   Brian   -> ElevenLabs; no key / no internet / refusal / no audio within
    #              EL_FIRST_AUDIO_S -> that reply is spoken by the Windows voice
    #   offline -> WINDOWS AWAAZ only
    EL_FIRST_AUDIO_S = 5.0

    @staticmethod
    def _voice_mode() -> str:
        try:
            import kaka_keys
            return kaka_keys.voice_mode()
        except Exception:
            return "brian"

    async def _speak_local(self, text: str) -> AsyncIterator[bytes]:
        """WINDOWS AWAAZ: the Windows voice on this PC (offline)."""
        self._note("Windows voice")
        async for pcm in self._sys().speak(text):
            yield pcm

    async def _speak_brian(self, text: str):
        """ElevenLabs. Yields audio; returns quietly with nothing yielded when it
        could not start (the caller then uses the PC voice for this reply)."""
        import asyncio
        el = None if time.monotonic() < self._rest_until else self._el()
        if el is None:
            if not getattr(self, "_said_nokey", False) and time.monotonic() >= self._rest_until:
                self._said_nokey = True
                print("[Kaka voice] No ELEVENLABS_API_KEY - PC voice instead of Brian")
            return
        agen = el.speak(text).__aiter__()
        started = False
        try:
            while True:
                try:
                    if not started:
                        pcm = await asyncio.wait_for(agen.__anext__(), self.EL_FIRST_AUDIO_S)
                    else:
                        pcm = await agen.__anext__()
                except StopAsyncIteration:
                    break
                if not started:
                    started = True
                    self._note("ElevenLabs Brian" if self.voice_id == BRIAN
                               else "ElevenLabs " + self.voice_id)
                yield pcm
        except Exception as e:
            if started:
                return
            low = (type(e).__name__ + " " + str(e)).lower()
            long_rest = ("quota" in low or "credit" in low or "401" in low
                         or "limit" in low or "unusual" in low)
            # no internet / slow: try Brian again in a minute, not every sentence
            self._rest_until = time.monotonic() + (6 * 3600 if long_rest else 60)
            why = "no audio in %.0fs" % self.EL_FIRST_AUDIO_S if isinstance(
                e, (asyncio.TimeoutError, TimeoutError)) else str(e)[:120]
            print(f"[Kaka voice] ElevenLabs failed ({why}) - PC voice for this reply"
                  f"{' (Brian rested a few hours)' if long_rest else ''}.")
        finally:
            try:
                await agen.aclose()
            except Exception:
                pass

    async def speak(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        if self._voice_mode() == "brian":
            got = False
            async for pcm in self._speak_brian(text):
                got = True
                yield pcm
            if got:
                return
        # offline voice chosen, or Brian could not speak this reply: never silent
        async for pcm in self._speak_local(text):
            yield pcm

    def probe(self) -> tuple[bool, str]:
        if self._voice_mode() == "offline":
            ok, msg = self._sys().probe()
            return ok, "WINDOWS AWAAZ (offline). " + msg
        el = self._el()
        if el is None:
            ok, msg = self._sys().probe()
            return ok, "No ELEVENLABS_API_KEY in Windows - the PC voice speaks instead of Brian. " + msg
        try:
            ok, msg = el.probe()
        except Exception as e:
            ok, msg = False, str(e)[:150]
        return ok, ("ElevenLabs: " + msg) if ok else ("ElevenLabs failed, the PC voice will speak: " + msg)

    async def warmup(self, log=None) -> None:
        """Remembers the activity log so a voice switch is shown to the user."""
        if callable(log):
            self._log = log
        return None


_test_voice = None


async def test_line(text: str = "Namaste! Main Kaka hoon, ab is awaaz mein bolunga.",
                    mode: str = "") -> tuple[bytes, str]:
    """For the TEST button: (24 kHz mono pcm16, name of the voice that spoke).
    mode '' = the current switch, 'brian' or 'offline' to force one."""
    global _test_voice
    if _test_voice is None:
        _test_voice = KakaVoice({})
    v = _test_voice
    v.active = ""
    if mode == "offline":
        gen = v._speak_local(text)
    elif mode == "brian":
        v._rest_until = 0.0
        async def both():
            got = False
            async for pcm in v._speak_brian(text):
                got = True
                yield pcm
            if not got:
                async for pcm in v._speak_local(text):
                    yield pcm
        gen = both()
    else:
        v._rest_until = 0.0
        gen = v.speak(text)
    buf = bytearray()
    async for pcm in gen:
        buf += pcm
    return bytes(buf), v.active


def play_pcm(pcm: bytes, rate: int = 0) -> None:
    """Blocking playback of pcm16 mono (Windows winsound; elsewhere a no-op)."""
    from . import audio
    rate = rate or audio.SPEAKER_RATE
    try:
        import winsound
    except ImportError:
        return
    winsound.PlaySound(audio.to_wav(pcm, rate, 1), winsound.SND_MEMORY)


def _make(cfg: dict) -> KakaVoice:
    return KakaVoice(cfg)


PROVIDER = {
    "id": "kaka_voice",
    "kind": "tts",
    "label": "Kaka voice (Brian / PC voice)",
    "hint": "ElevenLabs with the Windows voice as automatic backup",
    "requires": ("httpx",),
    "catalog": (
        {"id": "kaka_voice", "label": "ElevenLabs Brian (+ Windows backup)",
         "note": "key read from Windows ELEVENLABS_API_KEY", "local": False,
         "values": {}},
    ),
    "needs": (
        Field("voice_id", "ElevenLabs voice id", default=BRIAN, required=False,
              hint="Brian = nPczCjzI2devNBz1zQrb"),
        Field("model", "Model", kind="choice",
              choices=("eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2"),
              default=DEFAULT_MODEL, required=False,
              hint="flash is the quickest and cheapest"),
    ),
    "caps": {"audio_out": True},
    "factory": _make,
}


def available() -> bool:
    return find_spec("httpx") is not None
