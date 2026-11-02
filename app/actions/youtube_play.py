"""
actions/youtube_play.py - Kaka update 18: YouTube by voice.

"YouTube pe <x> chalao" -> YouTube search page (keyless) -> first real video
(ytInitialData videoRenderer; ads, Shorts, channels, playlists skipped; regex
fallback) -> opens watch?v=<id>&autoplay=1 in his default browser.
Controls (pause/play, next, fullscreen, +/-10 s, mute, volume) bring the
browser window whose title has "YouTube" to the front (core.winfocus, the
Update-9/14 foreground helper) and press YouTube's own keyboard shortcuts with
ctypes keybd_event (no pyautogui needed).
"""
from __future__ import annotations

import json
import re
import sys
import time

_IS_WIN = sys.platform == "win32"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

VK_SHIFT, VK_UP, VK_DOWN = 0x10, 0x26, 0x28
# action -> list of key chords; a chord is a list of VKs pressed together
KEYS = {
    "pause": [[ord("K")]], "resume": [[ord("K")]], "toggle": [[ord("K")]],
    "next": [[VK_SHIFT, ord("N")]], "previous": [[VK_SHIFT, ord("P")]],
    "fullscreen": [[ord("F")]], "forward": [[ord("L")]], "back": [[ord("J")]],
    "mute": [[ord("M")]], "volume_up": [[VK_UP]], "volume_down": [[VK_DOWN]],
}
SAY = {
    "pause": "YouTube pause kar diya.", "resume": "YouTube chala diya.",
    "toggle": "YouTube play/pause kar diya.", "next": "Agla video laga diya.",
    "previous": "Pichhla video laga diya (playlist mein hi kaam karta hai).",
    "fullscreen": "Fullscreen toggle kar diya.", "forward": "10 second aage kar diya.",
    "back": "10 second peeche kar diya.", "mute": "YouTube mute/unmute kar diya.",
    "volume_up": "YouTube ki awaaz badhayi.", "volume_down": "YouTube ki awaaz kam ki.",
}
_SKIP = ("adSlotRenderer", "promotedVideoRenderer", "searchPyvRenderer",
         "promotedSparklesWebRenderer", "reelShelfRenderer", "shortsLockupViewModel",
         "channelRenderer", "playlistRenderer", "radioRenderer", "lockupViewModel",
         "movieRenderer", "shelfRenderer")


# ---------------------------------------------------------------- search
def _http_get(url: str, timeout: float = 5.0) -> str:
    """Raises on failure. Tests monkeypatch this."""
    headers = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}
    cookies = "SOCS=CAI; CONSENT=YES+1"
    try:
        import requests
        r = requests.get(url, headers=headers, cookies={"SOCS": "CAI", "CONSENT": "YES+1"},
                         timeout=timeout)
        r.raise_for_status()
        return r.text
    except ImportError:
        pass
    import urllib.request
    req = urllib.request.Request(url, headers=dict(headers, Cookie=cookies))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _initial_data(html: str):
    for pat in (r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>",
                r"window\[\"ytInitialData\"\]\s*=\s*(\{.*?\});"):
        m = re.search(pat, html, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return None


def _walk(o, out: list) -> None:
    if isinstance(o, dict):
        for k, v in o.items():
            if k in _SKIP:
                continue
            if k == "videoRenderer" and isinstance(v, dict):
                vid = v.get("videoId")
                url = json.dumps(v.get("navigationEndpoint") or {})
                title = ""
                try:
                    title = "".join(r.get("text", "") for r in (v.get("title") or {}).get("runs", []))
                except Exception:
                    pass
                if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) and "/shorts/" not in url:
                    out.append((vid, title))
                continue
            _walk(v, out)
    elif isinstance(o, list):
        for x in o:
            _walk(x, out)


def parse_first_video(html: str):
    """-> (video_id, title) of the first real video, or (None, '')."""
    try:
        data = _initial_data(html or "")
        if data is not None:
            found: list = []
            _walk(data, found)
            if found:
                return found[0]
        for vid in re.findall(r"/watch\?v=([A-Za-z0-9_-]{11})", html or ""):
            return vid, ""
        for vid in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html or ""):
            if f"/shorts/{vid}" not in html:
                return vid, ""
    except Exception:
        pass
    return None, ""


def search_url(q: str) -> str:
    from urllib.parse import quote_plus
    return "https://www.youtube.com/results?search_query=" + quote_plus(q)


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}&autoplay=1"


def _open(url: str) -> bool:
    """Default browser. Tests monkeypatch this."""
    try:
        if _IS_WIN:
            import os
            os.startfile(url)          # noqa: (Windows only)
            return True
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        try:
            import webbrowser
            return bool(webbrowser.open(url))
        except Exception:
            return False


# ---------------------------------------------------------------- controls
_BROWSERS = ("chrome", "msedge", "brave", "firefox", "opera", "vivaldi", "browser")


def _youtube_window():
    """(hwnd, title) of the browser window showing YouTube, or (0, '')."""
    try:
        from core import winfocus as wf
        best = (0, "")
        for hwnd, pid, title in wf.windows():
            if "youtube" not in (title or "").lower():
                continue
            name = ""
            try:
                name = (wf.proc_name(pid) or "").lower()
            except Exception:
                pass
            if not name or any(b in name for b in _BROWSERS):
                return hwnd, title
            best = best if best[0] else (hwnd, title)
        return best
    except Exception:
        return 0, ""


def _foreground(hwnd: int) -> bool:
    try:
        from core import winfocus as wf
        fh, _ = wf.foreground()
        if fh and int(fh) == int(hwnd):
            return True
        return bool(wf.force_foreground(hwnd, tries=2))
    except Exception:
        return False


def _press_vk(chord: list) -> None:
    """Press a chord (all down, then all up). Tests monkeypatch this."""
    import ctypes
    u = ctypes.windll.user32
    for vk in chord:
        u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
    for vk in reversed(chord):
        u.keybd_event(vk, 0, 0x0002, 0)
        time.sleep(0.02)


def _control(action: str, times: int = 1) -> str:
    if action == "skip_ad":
        return ("YouTube ka 'Skip ad' button keyboard se bharose se nahi dabta, "
                "isliye maine kuch nahi dabaya - screen pe 'Skip' dabana padega.")
    chords = KEYS.get(action)
    if not chords:
        return f"YouTube mein '{action}' karna nahi aata."
    hwnd, _title = _youtube_window()
    if not hwnd:
        return "YouTube wali browser window nahi mili - pehle 'YouTube pe ... chalao' bolo."
    if not _foreground(hwnd):
        return "YouTube window saamne nahi la paya, isliye key nahi dabayi."
    time.sleep(0.15)
    n = max(1, min(int(times or 1), 10)) if action in ("volume_up", "volume_down", "forward", "back") else 1
    for _ in range(n):
        for chord in chords:
            _press_vk(chord)
        time.sleep(0.05)
    msg = SAY.get(action, "Ho gaya.")
    if action in ("volume_up", "volume_down"):
        msg += " (Agar page scroll hua toh player pe ek click karke dobara bolo.)"
    return msg


# ---------------------------------------------------------------- tool
def youtube_play(parameters: dict = None, player=None) -> str:
    t0 = time.monotonic()
    p = parameters or {}
    action = str(p.get("action") or "play").strip().lower()
    query = str(p.get("query") or "").strip()
    note = action
    try:
        if action == "play":
            if not query:
                msg = "Kya chalaun YouTube pe? Naam bolo."
            else:
                vid, title = None, ""
                try:
                    vid, title = parse_first_video(_http_get(search_url(query)))
                except Exception as e:
                    note = f"search fail {type(e).__name__}"
                if vid:
                    ok = _open(watch_url(vid))
                    msg = (f"YouTube pe chala diya: {title or query}." if ok else
                           "Browser nahi khul paya, YouTube nahi chala.")
                else:
                    ok = _open(search_url(query))
                    msg = (f"Pehla video nahi mil paya, YouTube pe '{query}' ka search khol diya - "
                           "jo chahiye us pe click karo." if ok else
                           "YouTube search nahi hua aur browser bhi nahi khula.")
        else:
            msg = _control(action, int(p.get("times") or 1))
    except Exception as e:
        msg = f"YouTube mein dikkat aayi ({type(e).__name__})."
    try:
        from core import kaka_services as ks
        ks.timing(f"youtube_play {note}", time.monotonic() - t0)
    except Exception:
        pass
    return msg


TOOL = {
    "name": "youtube_play",
    "description": (
        "Play a video on YouTube and control it by voice. Use whenever he says YouTube: "
        "'YouTube pe <x> chalao/lagao' (action=play, query=x) opens the first real video in "
        "his browser and it autoplays. Controls on the open YouTube tab: pause/ruko (pause), "
        "chalao/resume (resume), agla video (next), fullscreen, aage karo (forward 10 s), "
        "peeche karo (back 10 s), mute, awaaz badhao/kam (volume_up/volume_down), ad skip "
        "(skip_ad). No question before playing. Songs without 'YouTube' go to play_music."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["play", "pause", "resume", "next", "previous", "fullscreen",
                                "forward", "back", "mute", "volume_up", "volume_down", "skip_ad"],
                       "description": "play (default) or a control for the open YouTube video"},
            "query": {"type": "STRING", "description": "What to search/play (action=play)"},
            "times": {"type": "INTEGER", "description": "Repeat count for forward/back/volume (default 1)"},
        },
        "required": ["action"],
    },
    "handler": youtube_play,
}
