"""
core/spotify_playlist.py - Kaka update 15: play a Spotify PLAYLIST.

Songs already work (actions/music_player.py, unchanged). A playlist did not:
Ctrl+K + Enter on a playlist only OPENS its page - Spotify plays a song on
Enter, but for a playlist it waits for the green Play button. So:

  1. Spotify to the front (update 14's fast focus; opens it if closed)
     "liked songs" -> Spotify's own link spotify:collection:tracks
  2. Ctrl+K quick search, paste the playlist name, short poll for results,
     Enter -> the playlist page (Ctrl+K lists his own playlists first)
  3. PLAY: UI Automation - the green button is named "Play <playlist>"
     (track rows are "Play <song> by <artist>", the player bar is just "Play"
     at the bottom - that one would resume the OLD song, so it is never used).
     Invoke it (or click its centre). Not found -> first track's play button.
     Still nothing and nothing playing -> Space (keyboard fallback).
  4. VERIFY: Spotify's window title becomes "Artist - Song" while playing,
     or the button turns into "Pause <playlist>". Polled ~4 s.
  5. "shuffle karke" -> the "Enable shuffle" button, else Ctrl+S.
  Never asks a question (opening / playing music never asks - update 13).
  One "[time] spotify_playlist: ..." line in the activity log (update 14).
"""
from __future__ import annotations

import re
import time

from core import appidentity

try:
    from core import desktop_driver as drv
except Exception as _e:      # pragma: no cover
    drv = None
    print(f"[playlist] driver unavailable: {_e}")
try:
    from core import uia
except Exception:            # pragma: no cover
    uia = None
try:
    from core import speed
except Exception:            # pragma: no cover
    speed = None

PROCS = ("spotify",)
LIKED_URI = "spotify:collection:tracks"
_IDLE_TITLES = {"spotify", "spotify premium", "spotify free", ""}
_BTN = ("Button", "Hyperlink", "MenuItem", "ListItem")

_PLAYLIST_RX = re.compile(r"(?i)\bplay ?lists?\b|\u092a\u094d\u0932\u0947\u0932\u093f\u0938\u094d\u091f")
_LIKED_RX = re.compile(r"(?i)\bliked\b|\blike(d)? (kiye|kiya|wale|songs?|gaane|gane)\b|"
                       r"\bpasand(ida| kiye| wale)? (gaane|gane|songs?)\b")
_SHUFFLE_RX = re.compile(r"(?i)\bshuffle\b|\brandom\b|\bmix karke\b")
_FILLER_ANY = re.compile(r"(?i)\b(play ?lists?|meri|mera|mere|my|apni|apna|wali|wala|vali|vala)\b|"
                         r"\bspotify( pe| par| on| mein)?\b|\u092a\u094d\u0932\u0947\u0932\u093f\u0938\u094d\u091f")
_FILLER_END = re.compile(r"(?i)(\s+(shuffle|random)( karke| kar ke| mein| me| pe| par| on)?|"
                         r"\s+(chalao|chala do|chala de|chala|bajao|baja do|baja|play karo|play kar do|"
                         r"play|lagao|laga do|start karo|suna do|sunao))+\s*$")

# the green button / a track row / the bottom bar (English and Hindi Spotify)
_PLAY_RX = r"^\s*(Play|\u091a\u0932\u093e\u090f\u0902|\u091a\u0932\u093e\u090f\u0901)\b"
_PAUSE_RX = r"^\s*(Pause|\u0930\u094b\u0915\u0947\u0902)\b"


def wants_playlist(params: dict) -> bool:
    """kind=playlist from the brain, or the words 'playlist' / 'liked songs'."""
    p = params or {}
    kind = str(p.get("kind") or "").lower().strip()
    if kind in ("playlist", "liked", "liked_songs", "liked songs", "collection"):
        return True
    if kind in ("song", "track", "artist"):
        return False
    q = str(p.get("query") or "")
    return bool(_PLAYLIST_RX.search(q) or _LIKED_RX.search(q))


def is_liked(query: str, kind: str = "") -> bool:
    k = (kind or "").lower()
    if k in ("liked", "liked_songs", "liked songs", "collection"):
        return True
    q = (query or "").strip()
    return (not clean_name(q)) or bool(_LIKED_RX.search(q))


def wants_shuffle(params: dict) -> bool:
    p = params or {}
    s = p.get("shuffle")
    if isinstance(s, str):
        s = s.strip().lower() in ("true", "1", "yes", "haan", "ha")
    return bool(s) or bool(_SHUFFLE_RX.search(str(p.get("query") or "")))


def clean_name(query: str) -> str:
    """'meri gym playlist chalao' -> 'gym'; '' when no name is left ('meri playlist')."""
    q = (query or "").strip()
    c = _FILLER_END.sub("", " " + q)
    c = re.sub(r"\s+", " ", _FILLER_ANY.sub(" ", c)).strip(" -,.")
    return _FILLER_END.sub("", " " + c).strip(" -,.")

def _words(text: str) -> set:
    return {w for w in (appidentity.fold(x) for x in re.split(r"\s+", text or "")) if len(w) > 1}


# ------------------------------------------------------------------ helpers
def _log(player, text: str) -> None:
    print(f"[playlist] {text}")
    if player:
        try:
            player.write_log(text)
        except Exception:
            pass


def _say(player, text: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say("Tell the user, in one short Hinglish sentence: " + text)
        except Exception:
            pass


def _lap(step: str) -> None:
    if speed is not None:
        speed.lap(step)


def _poll(cond, timeout: float, interval: float = 0.25) -> bool:
    if speed is not None:
        return speed.poll(cond, timeout, interval, clock=time.monotonic, sleep=time.sleep)
    end = time.monotonic() + timeout
    while True:
        try:
            if cond():
                return True
        except Exception:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(interval)


def _windows() -> list:
    try:
        return list(drv.app_windows(PROCS, "Spotify"))
    except Exception:
        return []


def _titles() -> list:
    ts = [t for _h, _p, t in _windows()]
    if not ts:
        try:
            ts = list(drv.titles(PROCS))
        except Exception:
            ts = []
    return ts


def now_playing() -> str:
    """'Artist - Song' from Spotify's window title, or '' (paused / idle)."""
    for t in _titles():
        tt = (t or "").strip()
        if tt.lower() not in _IDLE_TITLES and " - " in tt:
            return tt
    return ""


def _main_hwnd():
    ws = _windows()
    if not ws:
        return None
    for h, _p, t in ws:                 # the main window carries the idle / song title
        tt = (t or "").strip().lower()
        if tt in _IDLE_TITLES or " - " in tt:
            return h
    return ws[0][0]


def _uia_ok() -> bool:
    try:
        return uia is not None and uia.available()
    except Exception:
        return False


def _win_box(hwnd):
    try:
        return uia._win_rect(hwnd)
    except Exception:
        return None


def _usable(e, box) -> bool:
    if getattr(e, "offscreen", False) or getattr(e, "giant", False):
        return False
    if e.right - e.left <= 0 or e.bottom - e.top <= 0:
        return box is None                  # no rect: only trust it when the window rect is unknown too
    return True


def _in_bottom_bar(e, box) -> bool:
    """The player bar (bottom ~110 px of the window): its 'Play' resumes the OLD song."""
    if box is None:
        return False
    return e.top >= box[3] - 110


def pick_play_button(els: list, name: str, box=None):
    """-> (element, why) for the button that plays THIS playlist, or (None, '')."""
    want = _words(name)
    big, rows, bare = [], [], []
    for e in els:
        n = (e.name or "").strip()
        if not re.match(_PLAY_RX, n, re.I) or not _usable(e, box) or _in_bottom_bar(e, box):
            continue
        rest = re.sub(_PLAY_RX, "", n, count=1, flags=re.I).strip()
        if not rest:
            bare.append(e)                       # a bare "Play" outside the player bar
        elif re.search(r"(?i)\sby\s|\s\u0926\u094d\u0935\u093e\u0930\u093e\s", " " + rest + " "):
            rows.append(e)                       # "Play <song> by <artist>" (a track row)
        else:
            big.append((len(want & _words(rest)), -e.top, e))
    if big:
        big.sort(key=lambda x: (x[0], x[1]), reverse=True)
        e = big[0][2]
        return e, f"'{e.name}' button"
    if bare:
        e = sorted(bare, key=lambda x: x.top)[0]
        return e, f"'{e.name}' button (page)"
    if rows:
        e = sorted(rows, key=lambda x: x.top)[0]
        return e, f"first track '{e.name}'"
    return None, ""


def _find_buttons(hwnd, rx: str) -> list:
    try:
        if hasattr(uia, "invalidate"):
            uia.invalidate()
        return list(uia.find(hwnd, rx, types=_BTN))
    except Exception as e:
        print(f"[playlist] uia find failed: {e}")
        return []


def _playing_now(before: str, hwnd, name: str) -> str:
    """Playing? -> what (title, or 'Pause <playlist>' button), else ''."""
    now = now_playing()
    if now and now != before:
        return now
    if hwnd and _uia_ok() and _pause_for(hwnd, name):
        return now or f"{name} (Pause button dikh raha)"
    return ""

def _pause_for(hwnd, name: str) -> bool:
    want = _words(name)
    box = _win_box(hwnd)
    for e in _find_buttons(hwnd, _PAUSE_RX):
        rest = re.sub(_PAUSE_RX, "", e.name or "", count=1, flags=re.I).strip()
        if rest and not _in_bottom_bar(e, box) and (not want or want & _words(rest)):
            return True
    return False


def _press_play(player, hwnd, name: str) -> tuple:
    """-> (pressed, how). UIA first; the keyboard only as the last way."""
    if hwnd and _uia_ok():
        box = _win_box(hwnd)
        found = {"e": None, "why": ""}

        def look():
            e, why = pick_play_button(_find_buttons(hwnd, _PLAY_RX), name, box)
            if e is not None:
                found.update(e=e, why=why)
                return True
            return False
        _poll(look, 3.0, 0.3)                    # the page is still loading: poll, early exit
        e = found["e"]
        if e is not None:
            if e.invoke():
                return True, found["why"] + " (invoke)"
            if drv.is_in_front(PROCS) and e.right > e.left:
                x, y = e.centre()
                drv.click(x, y)
                return True, found["why"] + " (click)"
            _log(player, f"Kaka: {found['why']} mila par dab nahi paya")
        else:
            names = [b.name for b in _find_buttons(hwnd, r".")][:30]
            _log(player, "Kaka: Spotify mein Play button nahi mila. Buttons: " + "; ".join(names))
    return False, ""


def _shuffle(player, hwnd) -> str:
    if hwnd and _uia_ok():
        on = _find_buttons(hwnd, r"^\s*Disable shuffle")
        if on:
            return "already on"
        off = _find_buttons(hwnd, r"^\s*Enable shuffle")
        off = [e for e in off if "smart" not in (e.name or "").lower()]
        if off and off[0].invoke():
            return "button"
    if drv.is_in_front(PROCS):
        drv.hotkey("ctrl", "s")
        return "Ctrl+S"
    return ""


# ------------------------------------------------------------------ main flow
def play_playlist(player, query: str, kind: str = "", shuffle: bool = False) -> str:
    log = (lambda s: _log(player, s)) if player else None
    t = speed.Timer("spotify_playlist", (lambda s: _log(player, s)) if player else None) \
        if speed is not None else None
    liked = is_liked(query, kind)
    name = "Liked Songs" if liked else clean_name(query)
    before = now_playing()
    try:
        return _run(player, log, name, liked, shuffle, before)
    finally:
        if t is not None:
            t.done(name)


def _run(player, log, name, liked, shuffle, before) -> str:
    _log(player, f"Kaka: Spotify pe '{name}' playlist chala raha hoon...")
    ok, _procs, why = drv.ensure_app("Spotify", wait=40.0, log=log,
                                     uri=LIKED_URI if liked else "")
    if not ok:
        if "not installed" in why:
            msg = ("Spotify is PC pe install nahi hai. Chaho toh YouTube pe chala doon, "
                   "ya Spotify install kar doon? (ask him which; do nothing until he says)")
        elif "khul gaya" in why or "khula hai" in why:
            msg = why
        else:
            msg = f"Spotify nahi khul paya ({why})."
        _log(player, f"Kaka: {msg}")
        _say(player, msg)
        return msg
    _lap("foreground")
    hwnd = _main_hwnd()
    if not liked:
        if not drv.is_in_front(PROCS) and not drv.focus_app(PROCS, "Spotify"):
            msg = (f"Spotify aage nahi aaya, isliye '{name}' playlist search nahi ki - "
                   f"Spotify pe ek click karke dobara bolo")
            _log(player, f"Kaka: {msg}")
            _say(player, msg)
            return msg
        if "opened" in why:
            time.sleep(2.0)                    # cold start: let Spotify finish loading
        want = _words(name)
        rx = "|".join(re.escape(w) for w in sorted(want)) if want else ""
        use_uia = bool(hwnd and _uia_ok() and rx)
        drv.hotkey("ctrl", "k")
        time.sleep(0.6)
        base_n = len(_find_buttons(hwnd, rx)) if use_uia else 0   # sidebar already has the name
        drv.hotkey("ctrl", "a")
        drv.paste(name)
        time.sleep(0.5)
        if use_uia:       # results are in when MORE elements carry the name than before typing
            if not _poll(lambda: len(_find_buttons(hwnd, rx)) > base_n, 1.3, 0.3):
                _log(player, "Kaka: Ctrl+K results dikhe nahi (UIA) - phir bhi Enter")
        else:
            time.sleep(1.3)                    # same total as the song path (1.8 s)
        _lap("search")
        if not drv.is_in_front(PROCS):
            msg = f"Spotify peeche chala gaya, isliye '{name}' playlist nahi khol paya"
            _log(player, f"Kaka: {msg}")
            _say(player, msg)
            return msg
        drv.press("enter")                     # opens the playlist page
        _log(player, f"Kaka: '{name}' playlist khol di")
    else:
        _log(player, "Kaka: Liked Songs khol diye (spotify:collection:tracks)")
    _lap("open")

    if hwnd and _uia_ok() and before:
        already = _playing_now("", hwnd, name) if _pause_for(hwnd, name) else ""
        if already:
            _log(player, f"Kaka: '{name}' playlist pehle se chal rahi hai - {already}")
            return f"playing playlist {name}: {already}"
    pressed, how = _press_play(player, hwnd, name)
    if not pressed and not before and drv.is_in_front(PROCS):
        drv.press("space")                     # last way: nothing was playing
        pressed, how = True, "Space key (fallback)"
    _lap("play")
    if pressed:
        _log(player, f"Kaka: play dabaya - {how}")
    playing = ""
    if pressed:
        _poll(lambda: bool(_playing_now(before, hwnd, name)), 4.0, 0.4)
        playing = _playing_now(before, hwnd, name)
    _lap("verify")
    if playing:
        extra = ""
        if shuffle:
            s = _shuffle(player, hwnd)
            extra = f" (shuffle: {s})" if s else " (shuffle nahi laga)"
            _lap("shuffle")
        _log(player, f"Kaka: '{name}' playlist chal rahi hai - {playing}{extra}")
        return f"playing playlist {name}: {playing}"
    if pressed:
        msg = (f"'{name}' playlist khul gayi aur play dabaya, par chalna confirm nahi hua - "
               f"na chale toh ek baar Space daba do")
    elif before:
        msg = (f"'{name}' playlist khul gayi, par Play button nahi mila - pehle se ek gaana "
               f"chal raha tha isliye Space nahi dabaya; green Play button daba do")
    else:
        msg = f"'{name}' playlist khul gayi, play confirm nahi hua - ek baar Space daba do"
    _log(player, f"Kaka: {msg}")
    _say(player, msg)
    return msg
