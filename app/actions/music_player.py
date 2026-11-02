"""
Kaka action - play music on Spotify (desktop app) by just saying the song.

"Tum Hi Ho chalao", "Kesariya bajao", "Arijit ke gaane chalao" -> Spotify, the
default player. YouTube only when he names YouTube (youtube_video tool).

How it plays (Kaka update 8, 9):
  1. open Spotify straight on the search results with its own link
     spotify:search:<song> (fallbacks: Spotify.exe, Store app, Start Menu),
     wait up to 40 s, bring it to the front and CHECK it is in front
  2. Ctrl+K quick search, paste the song, Enter  -> Spotify plays the top result
     (Spotify's own answer on its community forum: "tap enter on the one you'd
     like to play. The song will start to play automatically")
  3. VERIFY: Spotify's window title becomes "Artist - Song" while playing
  4. not playing? second way: Ctrl+K, paste, Down, Enter
  5. still not? say so honestly. Never opens the browser by itself.
"""
from __future__ import annotations

import re
import threading
import time

from core import appidentity, outcome

try:
    from core import desktop_driver as drv
except Exception as _e:      # pragma: no cover
    drv = None
    print(f"[music] driver unavailable: {_e}")

_SPOTIFY_PROCS = ("spotify",)
_IDLE_TITLES = {"spotify", "spotify premium", "spotify free", ""}


def _now_playing() -> str:
    """'Artist - Song' from Spotify's window title, or '' when nothing plays."""
    for t in drv.titles(_SPOTIFY_PROCS):
        tt = (t or "").strip()
        if tt.lower() not in _IDLE_TITLES and " - " in tt:
            return tt
    return ""


def _wait_playing(before: str, query: str, timeout: float = 6.0) -> str:
    words = {w for w in appidentity.fold(query).split() if len(w) > 2}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        now = _now_playing()
        if now and (now != before or (words and words & set(appidentity.fold(now).split()))):
            return now
        time.sleep(0.4)
    return ""


def _say(player, text: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say("Tell the user, in one short Hinglish sentence: " + text)
        except Exception:
            pass


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(text)
        except Exception:
            pass


def _search_uri(query: str) -> str:
    import urllib.parse
    return "spotify:search:" + urllib.parse.quote(query.strip(), safe="")


def _front(procs=_SPOTIFY_PROCS) -> bool:
    """Spotify really in front? Try to bring it; never type into another app."""
    if drv.is_in_front(procs):
        return True
    return drv.focus_app(procs, "Spotify")


def _try_way(player, label: str, keys, before: str, query: str) -> str:
    if not _front():
        _log(player, f"Kaka: Spotify aage nahi aaya, isliye {label} ke keys nahi dabaye")
        return ""
    try:
        keys()
    except Exception as e:
        print(f"[music] {label} failed: {e}")
        return ""
    return _wait_playing(before, query, timeout=8.0)


def _play_on_spotify(player, query: str) -> str:
    before = _now_playing()
    log = (lambda s: _log(player, s)) if player else None
    # Update 9: open Spotify straight on the search results with its own link
    # (spotify:search:...). Works for the website and the Store Spotify, and if
    # the link is not registered the plan falls back to Spotify.exe / Store /
    # Start Menu. 40 s for a cold start.
    ok, procs, why = drv.ensure_app("Spotify", wait=40.0, log=log, uri=_search_uri(query))
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
    time.sleep(2.5)                     # search results load

    def way1():                         # quick search -> Enter plays the top result
        drv.hotkey("ctrl", "k")
        time.sleep(0.8)
        drv.hotkey("ctrl", "a")
        drv.paste(query)
        time.sleep(1.8)
        drv.press("enter")

    def way2():                         # quick search -> first row -> Enter
        drv.press("esc")
        time.sleep(0.3)
        drv.hotkey("ctrl", "k")
        time.sleep(0.8)
        drv.hotkey("ctrl", "a")
        drv.paste(query)
        time.sleep(2.0)
        drv.press("down")
        time.sleep(0.2)
        drv.press("enter")

    now = _try_way(player, "way 1", way1, before, query)
    if not now:
        _log(player, "Kaka: pehla tareeka se gaana nahi chala, dusra try kar raha hoon")
        now = _try_way(player, "way 2", way2, before, query)
    if now:
        _log(player, f"Kaka: Spotify pe chal raha hai - {now}")
        return f"playing {now}"
    if not _front():
        msg = (f"Spotify mein '{query}' ke results khol diye, par Windows ne Spotify ko aage "
               f"nahi aane diya, isliye main play nahi daba paya - Spotify pe ek click karke "
               f"top result chala do, ya bolo toh YouTube pe chala doon?")
    else:
        msg = (f"Spotify mein '{query}' ke results khol diye, par gaana chalta hua confirm nahi "
               f"hua - top result pe ek click kar do, ya main YouTube pe chala doon?")
    _log(player, f"Kaka: {msg}")
    _say(player, msg)
    return msg


def play_music(parameters=None, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "play").lower().strip()
    query = str(p.get("query") or "").strip()
    if drv is None:
        return outcome.failed("play_music", "the desktop driver is not available")
    if action in ("pause", "resume", "play_pause", "toggle", "next", "previous", "prev", "stop"):
        key = {"pause": "play_pause", "resume": "play_pause", "toggle": "play_pause",
               "prev": "previous"}.get(action, action)
        before = _now_playing()
        if not drv.media_key(key):
            return outcome.failed("play_music", "the media key could not be pressed")
        if key in ("next", "previous"):
            time.sleep(1.2)
            now = _now_playing()
            if now and now != before:
                return outcome.done("play_music", action=action, now_playing=now)
        return outcome.unverified("play_music", f"{action} media key pressed", action=action)
    # update 15: a PLAYLIST / Liked Songs has its own path (the song path below is unchanged)
    if _is_playlist(p, query):
        return _start_playlist(player, p, query)
    if not query:
        if drv.media_key("play_pause"):
            return outcome.unverified("play_music", "play/pause key pressed - nothing named to play")
        return outcome.failed("play_music", "no song was named")
    if "youtube" in query.lower() or "youtube" in str(p.get("app") or "").lower():
        # He named YouTube: that is the YouTube tool's job, not Spotify's.
        try:
            from actions.youtube_video import youtube_video
            q = re.sub(r"(?i)\byou ?tube\b( pe| par| on| mein)?", "", query).strip()
            return youtube_video({"action": "play", "query": q or query}, player=player)
        except Exception as e:
            print(f"[music] YouTube hand-off failed: {e}")
    if not drv.can_drive():
        return outcome.failed("play_music", "keyboard control (pyautogui) is not installed")
    _log(player, f"Kaka: Spotify pe '{query}' chala raha hoon...")
    threading.Thread(target=_play_on_spotify, args=(player, query), daemon=True,
                     name="spotify-play").start()
    return outcome.started("play_music", app="Spotify", query=query)


def _is_playlist(p: dict, query: str) -> bool:
    if "youtube" in query.lower() or "youtube" in str(p.get("app") or "").lower():
        return False
    try:
        from core import spotify_playlist
        return spotify_playlist.wants_playlist(p)
    except Exception as e:
        print(f"[music] playlist check failed: {e}")
        return False


def _start_playlist(player, p: dict, query: str) -> str:
    """update 15: open the playlist (Ctrl+K) and press its green Play button."""
    from core import spotify_playlist as spl
    if not drv.can_drive():
        return outcome.failed("play_music", "keyboard control (pyautogui) is not installed")
    kind = str(p.get("kind") or "")
    shuffle = spl.wants_shuffle(p)
    name = "Liked Songs" if spl.is_liked(query, kind) else spl.clean_name(query)
    threading.Thread(target=spl.play_playlist, args=(player, query, kind, shuffle), daemon=True,
                     name="spotify-playlist").start()
    return outcome.started("play_music", app="Spotify", playlist=name, shuffle=shuffle)


TOOL = {
    "name": "play_music",
    "description": (
        "Plays a song / artist / playlist on the SPOTIFY DESKTOP APP, and controls "
        "playback (pause, resume, next, previous, stop) with media keys. This is "
        "the DEFAULT for music: 'Tum Hi Ho chalao', 'Kesariya bajao', 'Arijit ke "
        "gaane chalao', 'Spotify pe lofi chala', 'gaana roko', 'agla gaana'. "
        "If he says 'YouTube' anywhere ('YouTube pe lofi bajao') it is NOT this tool: "
        "use youtube_video. It answers at once "
        "(status=started: say you are playing it); it then checks Spotify really "
        "started the song and tells you separately if not. If Spotify is not "
        "installed you will be told - ask him, never open a website by yourself. "
        "PLAYLISTS: '<name> playlist chalao', 'meri gym playlist bajao', 'liked songs "
        "chalao' -> kind='playlist' with query = the playlist name ('gym', 'Liked "
        "Songs'); it opens the playlist and presses its Play button. 'shuffle karke' "
        "-> shuffle=true. Never ask a question before playing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "play (default) | pause | resume | next | previous | stop"},
            "app": {"type": "STRING",
                    "description": "spotify (default) | youtube - set youtube whenever he said YouTube"},
            "query": {"type": "STRING",
                      "description": "What to play, as he said it in its normal spelling: "
                                     "'Tum Hi Ho', 'Arijit Singh songs', 'Kesariya'"},
            "kind": {"type": "STRING",
                     "description": "song (default) | playlist - playlist whenever he says "
                                    "playlist or liked songs"},
            "shuffle": {"type": "BOOLEAN",
                        "description": "true when he says shuffle karke (playlists)"},
        },
    },
    "handler": play_music,
}
