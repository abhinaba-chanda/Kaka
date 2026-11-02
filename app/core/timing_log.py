r"""
core/timing_log.py - Kaka update 16: timing that survives Kaka closing.

Every "[time] ..." line (update 14 action timers, foreground timing, the new
voice timing below) and every "[uia] ..." line (screen-reading fallbacks) is
appended to  F:\Kaka\Data\kaka-timing.txt  with the date and time in front.
The file keeps the last ~500 lines. Nothing here ever raises: if the disk
says no, Kaka carries on exactly as before.

Instrumentation only - no behaviour of Kaka changes because of this file.

VoiceTiming: per voice turn,
  "[time] voice: heard->tool X s, tool Y s, tool->speak Z s, total W s"
  heard  = the last piece of his words' transcript arriving (Gemini Live sends
           no separate "speech ended" event to Kaka; the transcript of his last
           words is the closest signal the client has - it lands a little AFTER
           he stops, so heard->tool is slightly under-counted, never over).
  tool   = Kaka running the tool(s) (sum when a turn has several).
  speak  = first audio chunk of Kaka's reply arriving after the tool result
           was sent back (arrival on the PC, before the speaker buffer).
  A field Kaka could not measure is written "n/a", never guessed.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

FILE_NAME = "kaka-timing.txt"
MAX_LINES = 500          # keep this many
TRIM_SLACK = 100         # trim when the file grows past MAX_LINES + TRIM_SLACK
DEDUPE_S = 3.0           # the same line from two paths within 3 s -> once
STALE_S = 60.0           # words heard longer ago than this did not start this tool

_DUP_TAG = re.compile(r"^(\[\w+\] )\1+")
_LOCK = threading.Lock()
_STATE = {"path": None, "lines": None, "recent": {}, "last_time": "", "last_voice": "",
          "errors": 0}


def _default_path() -> Path:
    try:
        import kaka_paths
        root = Path(kaka_paths.DATA_ROOT)
    except Exception:
        env = os.environ.get("KAKA_DATA", "").strip()
        root = Path(env) if env else Path("F:/Kaka/Data")
    return root / FILE_NAME


def path() -> Path:
    p = _STATE.get("path")
    return Path(p) if p else _default_path()


def set_path(p) -> None:
    """Tests (or a future setting) point the file somewhere else."""
    with _LOCK:
        _STATE["path"] = str(p) if p else None
        _STATE["lines"] = None
        _STATE["recent"] = {}


def wanted(text) -> bool:
    try:
        t = str(text)
    except Exception:
        return False
    return ("[time]" in t) or ("[uia]" in t)


def _count_lines(p: Path) -> int:
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _trim(p: Path) -> None:
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        lines = f.read().splitlines()
    keep = lines[-MAX_LINES:]
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write("".join(l + "\r\n" for l in keep))
    os.replace(tmp, p)
    _STATE["lines"] = len(keep)


def record(text, now: Optional[float] = None) -> bool:
    """Append one line (any text) with a timestamp. True if written."""
    try:
        line = " ".join(str(text).replace("\r", " ").split("\n")).strip()
        line = _DUP_TAG.sub(r"\1", line)     # "[uia] [uia] x" -> "[uia] x"
        if not line:
            return False
        t = time.time() if now is None else now
        with _LOCK:
            recent = _STATE["recent"]
            last = recent.get(line)
            if last is not None and abs(t - last) < DEDUPE_S:
                return False
            recent[line] = t
            if len(recent) > 64:
                for k in sorted(recent, key=recent.get)[:32]:
                    recent.pop(k, None)
            if "[time] voice:" in line:
                _STATE["last_voice"] = line
            elif "[time]" in line:
                _STATE["last_time"] = line
            p = path()
            stamp = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
            p.parent.mkdir(parents=True, exist_ok=True)
            if _STATE["lines"] is None:
                _STATE["lines"] = _count_lines(p)
            with open(p, "a", encoding="utf-8", newline="") as f:
                f.write(f"{stamp}  {line}\r\n")
            _STATE["lines"] += 1
            if _STATE["lines"] > MAX_LINES + TRIM_SLACK:
                _trim(p)
        return True
    except Exception as e:
        _STATE["errors"] = _STATE.get("errors", 0) + 1
        if _STATE["errors"] <= 3:
            try:
                print(f"[timing] could not write {FILE_NAME}: {e}")
            except Exception:
                pass
        return False


def maybe(text) -> bool:
    """record(text) only for "[time]" / "[uia]" lines. Never raises."""
    try:
        if wanted(text):
            return record(text)
    except Exception:
        pass
    return False


# ------------------------------------------------------------------ "timing" command
def _strip_stamp(line: str) -> str:
    s = line.strip()
    if len(s) > 21 and s[4] == "-" and s[13] == ":" and s[19:21] == "  ":
        return s[21:]
    return s


def last_lines() -> tuple:
    """(last action [time] line, last voice line) - this session, else the file."""
    act, voice = _STATE.get("last_time") or "", _STATE.get("last_voice") or ""
    if act and voice:
        return act, voice
    try:
        with open(path(), "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for l in reversed(lines):
            l = _strip_stamp(l)
            if not voice and "[time] voice:" in l:
                voice = l
            elif not act and "[time]" in l and "[time] voice:" not in l:
                act = l
            if act and voice:
                break
    except Exception:
        pass
    return act, voice


def _secs(word: str) -> str:
    try:
        v = float(word.rstrip("s"))
        return f"{v:.1f} second"
    except Exception:
        return word


def summary() -> str:
    """One short Hinglish sentence for "timing" / "kitna time laga"."""
    act, voice = last_lines()
    out = []
    try:
        if act:
            body = act.split("[time]", 1)[1].strip()
            name, _, rest = body.partition(":")
            tot = rest.split("total", 1)[1].split()[0] if "total" in rest else ""
            out.append(f"Pichla kaam ({name.strip()}) {_secs(tot)} mein hua" if tot
                       else f"Pichla kaam: {body[:80]}")
        if voice:
            tot = voice.split("total", 1)[1].split()[0] if "total" in voice else "n/a"
            if tot not in ("n/a", "n/a,"):
                out.append(f"bolne se jawab tak {_secs(tot.rstrip(','))}")
    except Exception:
        pass
    if not out:
        return "Abhi koi timing save nahi hui - pehle koi kaam karwao."
    return ", ".join(out) + ". Poori list kaka-timing.txt mein hai."


# ------------------------------------------------------------------ voice timing
def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{max(0.0, v):.1f}s"


class VoiceTiming:
    """Feed it the Live events in order; it hands back the finished line once.
    Only turns with a tool call produce a line (that is where the time goes)."""

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self.clock = clock or time.monotonic
        self._reset()
        self.heard_at: Optional[float] = None

    def _reset(self):
        self.tool_at: Optional[float] = None       # first tool_call arrival
        self.tool_s: Optional[float] = None        # summed tool run time
        self.sent_at: Optional[float] = None       # tool response sent
        self.names: list = []
        self._run_start: Optional[float] = None
        self.turn_heard: Optional[float] = None

    def heard(self):
        """A piece of his words' transcript arrived."""
        now = self.clock()
        self.heard_at = now
        if self.tool_at is None:
            self.turn_heard = now

    def tool_start(self, name: str = ""):
        now = self.clock()
        if self.tool_at is None:
            self.tool_at = now
            if self.turn_heard is None:
                self.turn_heard = self.heard_at
            if self.turn_heard is not None and now - self.turn_heard > STALE_S:
                self.turn_heard = None     # typed command / proactive: nothing heard
        if name:
            self.names.append(str(name))
        self._run_start = now

    def tool_end(self):
        if self._run_start is None:
            return
        d = self.clock() - self._run_start
        self.tool_s = d if self.tool_s is None else self.tool_s + d
        self._run_start = None

    def response_sent(self):
        if self.tool_at is not None:
            self.sent_at = self.clock()

    def audio(self) -> str:
        """An audio chunk of Kaka's voice arrived. Returns the line when this is
        the first one after a tool result, else ""."""
        if self.sent_at is None:
            return ""
        return self._finish(self.clock())

    def turn_complete(self) -> str:
        """The model finished its turn. If a tool ran but no voice came after
        the result, the line goes out now with speak = n/a."""
        if self.sent_at is None and self.tool_at is None:
            self.turn_heard = None
            return ""
        if self.sent_at is None:
            return ""          # tool still running / result not sent yet
        return self._finish(None)

    def _finish(self, spoke_at: Optional[float]) -> str:
        heard = self.turn_heard
        h2t = (self.tool_at - heard) if (heard is not None and self.tool_at is not None
                                          and heard <= self.tool_at) else None
        t2s = (spoke_at - self.sent_at) if spoke_at is not None else None
        total = (spoke_at - heard) if (spoke_at is not None and heard is not None
                                       and heard <= spoke_at) else None
        names = ",".join(dict.fromkeys(self.names))
        line = (f"[time] voice: heard->tool {_fmt(h2t)}, tool {_fmt(self.tool_s)}, "
                f"tool->speak {_fmt(t2s)}, total {_fmt(total)}"
                + (f" ({names})" if names else ""))
        self._reset()
        self.heard_at = None               # used once; a later tool needs new words
        return line
