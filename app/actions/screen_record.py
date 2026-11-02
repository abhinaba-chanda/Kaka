r"""
actions/screen_record.py - Kaka update 20 (feature 32): screen recording by voice.
Logic in core/kaka_recorder.py (+ core/kaka_ffmpeg.py). No question for any action.
"""
try:                                   # stdlib-only; registers the startup cleanup
    from core import kaka_recorder as _rec
except Exception:                      # never block tool discovery
    _rec = None


def _flag(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "haan", "on")
    return bool(v)


def screen_record(parameters: dict = None) -> str:
    p = parameters or {}
    a = str(p.get("action") or "status").strip().lower()
    try:
        R = _rec
        if R is None:
            from core import kaka_recorder as R
        if a in ("start", "shuru", "on"):
            return R.start(window=_flag(p.get("window")), mic=_flag(p.get("mic")))
        if a in ("stop", "band", "off"):
            return R.stop()
        if a in ("save_replay", "replay_save", "save"):
            return R.save_replay(p.get("seconds") or 30)
        if a == "replay_on":
            return R.replay_on()
        if a == "replay_off":
            return R.replay_off()
        return R.status()
    except Exception as e:
        return f"Recording mein dikkat aayi: {type(e).__name__}: {e}"


TOOL = {
    "name": "screen_record",
    "description": ("Screen recording (mp4 in F:\\Kaka\\Data\\Recordings; works in game mode too). "
                    "'recording shuru karo' -> start; 'ye window record karo' -> start window=true; "
                    "'mic ke saath recording' -> start mic=true; 'recording band karo' -> stop; "
                    "'recording kitni hui' -> status; 'pichhle 30 second save karo' -> save_replay seconds=30; "
                    "'replay buffer chalu/band karo' -> replay_on/replay_off. No question."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING",
                   "enum": ["start", "stop", "status", "save_replay", "replay_on", "replay_off"]},
        "window": {"type": "BOOLEAN", "description": "only the window in front ('ye window')"},
        "mic": {"type": "BOOLEAN", "description": "add microphone ('mic ke saath')"},
        "seconds": {"type": "INTEGER", "description": "save_replay length, default 30"}},
        "required": ["action"]},
    "handler": screen_record,
}
