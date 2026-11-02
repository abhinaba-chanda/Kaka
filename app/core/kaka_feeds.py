r"""
core/kaka_feeds.py - Kaka update 18 (helper "feeds"): keyless live sources for
the morning update, weather, India news and FC Barcelona scores.

    weather  : Open-Meteo forecast + geocoding (no key)
    news     : Google News RSS en-IN (no key)
    barca    : ESPN public site API, team 83 (no key); TheSportsDB test key
               "123" as fallback (the old "3" key is disabled - checked 2 Nov 2026)

Every fetch has a short timeout and every function returns data or None, never
raises. compose_* build the short Hinglish text Kaka speaks. Only stdlib at the
top level (startup cost); requests is imported lazily, urllib is the fallback.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
KOLKATA = (22.5726, 88.3639, "Kolkata")
BARCA_ID = "83"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

URL_FORECAST = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,precipitation"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,precipitation_sum"
                "&timezone=auto&forecast_days=3")
URL_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=1&language=en&format=json"
URL_NEWS = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
URL_BARCA_PAST = ESPN + "/all/teams/83/schedule"
URL_BARCA_NEXT = ESPN + "/all/teams/83/schedule?fixture=true"
URL_SCOREBOARD = ESPN + "/{slug}/scoreboard"
URL_TSDB_LAST = "https://www.thesportsdb.com/api/v1/json/123/eventslast.php?id=133739"
URL_TSDB_NEXT = "https://www.thesportsdb.com/api/v1/json/123/eventsnext.php?id=133739"

TIMEOUT = 4.0


def _now() -> datetime:          # local wall clock (his PC is IST); tests patch it
    return datetime.now()


def _log(text: str) -> None:
    try:
        from core import kaka_services as ks
        ks.log(text)
    except Exception:
        pass


# ------------------------------------------------------------------ http
def _http_get(url: str, timeout: float = TIMEOUT, headers: Optional[dict] = None) -> bytes:
    """Raw GET. Raises on failure (callers catch). Tests monkeypatch this."""
    h = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}
    h.update(headers or {})
    try:
        import requests
        r = requests.get(url, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.content
    except ImportError:
        pass
    import urllib.request
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url: str, timeout: float = TIMEOUT):
    try:
        return json.loads(_http_get(url, timeout).decode("utf-8", "replace"))
    except Exception as e:
        _log(f"SYS: feed fail {url.split('?')[0][-50:]}: {type(e).__name__}")
        return None


def parallel(jobs: dict, timeout: float = TIMEOUT + 1.5) -> dict:
    """{name: fn} -> {name: result or None}; hard wall-clock bound, never waits
    for a stuck thread (daemon threads are abandoned)."""
    out = {k: None for k in jobs}
    def _run(k, fn):
        try:
            out[k] = fn()
        except Exception:
            out[k] = None
    ts = [threading.Thread(target=_run, args=(k, fn), daemon=True, name=f"kaka-feed-{k}")
          for k, fn in jobs.items()]
    for t in ts:
        t.start()
    end = time.monotonic() + timeout
    for t in ts:
        t.join(max(0.0, end - time.monotonic()))
    return dict(out)


# ------------------------------------------------------------------ weather
WMO = {
    0: "aasmaan saaf", 1: "zyaadatar saaf", 2: "thode baadal", 3: "baadal chhaye",
    45: "kohra", 48: "kohra", 51: "halki boonda-baandi", 53: "boonda-baandi",
    55: "tez boonda-baandi", 56: "thandi boonda-baandi", 57: "thandi boonda-baandi",
    61: "halki baarish", 63: "baarish", 65: "tez baarish", 66: "thandi baarish",
    67: "thandi baarish", 71: "halki barf", 73: "barf", 75: "tez barf", 77: "barf",
    80: "halki bauchhar", 81: "bauchhar", 82: "tez bauchhar", 85: "barf", 86: "barf",
    95: "aandhi-toofan", 96: "toofan aur ole", 99: "toofan aur ole",
}


def wmo(code) -> str:
    try:
        return WMO.get(int(code), "mausam")
    except Exception:
        return "mausam"


def parse_forecast(js) -> Optional[dict]:
    """Open-Meteo forecast JSON -> {now:{temp,feels,humidity,desc}, days:[{date,max,min,
    rain_prob,rain_mm,desc}...]} (days[0]=today, days[1]=tomorrow)."""
    try:
        cur = js.get("current") or {}
        d = js.get("daily") or {}
        days = []
        for i, date in enumerate(d.get("time") or []):
            def g(key):
                v = (d.get(key) or [None] * (i + 1))
                return v[i] if i < len(v) else None
            days.append({"date": date, "max": g("temperature_2m_max"),
                         "min": g("temperature_2m_min"),
                         "rain_prob": g("precipitation_probability_max"),
                         "rain_mm": g("precipitation_sum"), "desc": wmo(g("weather_code"))})
        now = None
        if cur.get("temperature_2m") is not None:
            now = {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
                   "humidity": cur.get("relative_humidity_2m"), "desc": wmo(cur.get("weather_code"))}
        if not days and not now:
            return None
        return {"now": now, "days": days}
    except Exception:
        return None


def parse_geocode(js) -> Optional[tuple]:
    try:
        r = (js.get("results") or [])[0]
        return float(r["latitude"]), float(r["longitude"]), str(r.get("name") or "")
    except Exception:
        return None


def geocode(city: str) -> Optional[tuple]:
    from urllib.parse import quote
    c = (city or "").strip()
    if not c or c.lower() in ("kolkata", "calcutta", "kolkatta", "kol"):
        return KOLKATA
    return parse_geocode(_get_json(URL_GEOCODE.format(name=quote(c))))


def weather(city: str = "") -> Optional[dict]:
    """-> parsed forecast with 'place', or None. A city that cannot be found
    returns {'error': 'city'}."""
    g = geocode(city)
    if g is None:
        return {"error": "city", "place": city}
    lat, lon, name = g
    w = parse_forecast(_get_json(URL_FORECAST.format(lat=lat, lon=lon)))
    if w is None:
        return None
    w["place"] = name or city or "Kolkata"
    return w


def _n(v) -> str:
    try:
        return str(int(round(float(v))))
    except Exception:
        return "?"


def _rain_phrase(day: dict) -> str:
    p = day.get("rain_prob")
    mm = day.get("rain_mm")
    try:
        p = int(p)
    except Exception:
        p = None
    try:
        mm = float(mm)
    except Exception:
        mm = None
    if p is None and mm is None:
        return ""
    if p is not None and p >= 60 or (mm or 0) >= 5:
        s = "baarish ke chance zyaada"
    elif p is not None and p >= 30 or (mm or 0) >= 1:
        s = "baarish ho sakti hai"
    else:
        s = "baarish ke chance kam"
    bits = []
    if p is not None:
        bits.append(f"{p}%")
    if mm:
        bits.append(f"{mm:.1f} mm")
    return s + (f" ({', '.join(bits)})" if bits else "")


def weather_text(w: Optional[dict], when: str = "today") -> str:
    if not w:
        return "Mausam ki jaankari abhi nahi mili (internet/Open-Meteo jawab nahi diya)."
    if w.get("error") == "city":
        return f"'{w.get('place')}' naam ki jagah nahi mili, mausam nahi bata paya."
    place = w.get("place") or "Kolkata"
    days = w.get("days") or []
    if when == "tomorrow":
        if len(days) < 2:
            return f"{place} ka kal ka forecast nahi mila."
        d = days[1]
        return (f"Kal {place} mein {d['desc']}, {_n(d['min'])} se {_n(d['max'])} degree, "
                f"{_rain_phrase(d)}.").replace(", .", ".")
    parts = []
    now = w.get("now")
    if now:
        s = f"{place} mein abhi {_n(now['temp'])} degree, {now['desc']}"
        if now.get("feels") is not None and abs(float(now["feels"]) - float(now["temp"])) >= 2:
            s += f", mehsoos {_n(now['feels'])} jaisa"
        parts.append(s)
    if days:
        d = days[0]
        s = f"aaj {_n(d['min'])} se {_n(d['max'])} degree"
        r = _rain_phrase(d)
        if r:
            s += f", {r}"
        parts.append(s)
    return (". ".join(p[0].upper() + p[1:] for p in parts) + ".") if parts else \
        f"{place} ka mausam nahi mila."


# ------------------------------------------------------------------ news
_SRC_RE = re.compile(r"\s+-\s+[^-]{2,60}$")


def parse_rss(xml_bytes, n: int = 3) -> list:
    """Google News RSS -> up to n clean headlines (source suffix removed)."""
    out = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_bytes)
        for item in root.iter("item"):
            t = (item.findtext("title") or "").strip()
            src = (item.findtext("source") or "").strip()
            if src and t.endswith(" - " + src):
                t = t[: -len(" - " + src)]
            else:
                t = _SRC_RE.sub("", t)
            t = re.sub(r"\s+", " ", t).strip()
            if t and t not in out:
                out.append(t)
            if len(out) >= n:
                break
    except Exception:
        return []
    return out


def news(n: int = 3) -> Optional[list]:
    try:
        r = parse_rss(_http_get(URL_NEWS), n)
        return r or None
    except Exception as e:
        _log(f"SYS: news fail: {type(e).__name__}")
        return None


def news_text(items: Optional[list]) -> str:
    if not items:
        return "News abhi nahi mili (Google News jawab nahi diya)."
    return "Aaj ki khabrein: " + " | ".join(f"{i + 1}. {t}" for i, t in enumerate(items))


# ------------------------------------------------------------------ barca
def _dt(s) -> Optional[datetime]:
    try:
        s = str(s).strip().replace("Z", "+00:00")
        if re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d\+", s):
            s = s.replace("+", ":00+", 1)
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _score(v):
    if isinstance(v, dict):
        v = v.get("displayValue", v.get("value"))
    try:
        return int(float(v))
    except Exception:
        return None


def parse_espn_events(js) -> list:
    """ESPN schedule/scoreboard JSON -> [{id,date,home,away,hs,as_,state,detail,
    league,slug}] (state: pre | in | post)."""
    out = []
    try:
        for e in (js.get("events") or []):
            try:
                c = (e.get("competitions") or [{}])[0]
                comp = {x.get("homeAway"): x for x in (c.get("competitors") or [])}
                h, a = comp.get("home") or {}, comp.get("away") or {}
                st = (c.get("status") or e.get("status") or {})
                typ = st.get("type") or {}
                lg = e.get("league") if isinstance(e.get("league"), dict) else {}
                out.append({
                    "id": str(e.get("id") or ""),
                    "date": _dt(e.get("date") or c.get("date")),
                    "home": (h.get("team") or {}).get("shortDisplayName") or (h.get("team") or {}).get("displayName") or "?",
                    "away": (a.get("team") or {}).get("shortDisplayName") or (a.get("team") or {}).get("displayName") or "?",
                    "home_id": str((h.get("team") or {}).get("id") or h.get("id") or ""),
                    "away_id": str((a.get("team") or {}).get("id") or a.get("id") or ""),
                    "hs": _score(h.get("score")), "as_": _score(a.get("score")),
                    "state": typ.get("state") or "", "detail": typ.get("shortDetail") or typ.get("detail") or "",
                    "clock": st.get("displayClock") or "",
                    "league": lg.get("name") or lg.get("shortName") or (e.get("seasonType") or {}).get("name") or "",
                    "slug": lg.get("slug") or "",
                })
            except Exception:
                continue
    except Exception:
        return []
    return out


def parse_tsdb(js) -> list:
    out = []
    try:
        for e in (js.get("events") or js.get("results") or []):
            d = _dt(e.get("strTimestamp") or "")
            hs, as_ = _score(e.get("intHomeScore")), _score(e.get("intAwayScore"))
            out.append({"id": str(e.get("idEvent") or ""), "date": d,
                        "home": e.get("strHomeTeam") or "?", "away": e.get("strAwayTeam") or "?",
                        "home_id": "", "away_id": "", "hs": hs, "as_": as_,
                        "state": "post" if hs is not None else "pre", "detail": "", "clock": "",
                        "league": e.get("strLeague") or "", "slug": ""})
    except Exception:
        return []
    return out


def pick_barca(past: list, future: list, now_utc: Optional[datetime] = None) -> dict:
    """-> {last, next, live} event dicts (or None)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    allev = [e for e in (past or []) + (future or []) if e.get("date")]
    live = next((e for e in allev if e["state"] == "in"), None)
    done = sorted([e for e in allev if e["state"] == "post"], key=lambda e: e["date"])
    up = sorted([e for e in allev if e["state"] == "pre" and e["date"] > now_utc - timedelta(hours=3)],
                key=lambda e: e["date"])
    return {"last": done[-1] if done else None, "next": up[0] if up else None, "live": live}


def _is_barca(name: str, tid: str = "") -> bool:
    return tid == BARCA_ID or "barcelona" in (name or "").lower() or (name or "").lower() in ("barca", "barça")


def _vs(e: dict) -> str:
    """'Alaves ke against, ghar pe' style opponent phrase."""
    if _is_barca(e["home"], e.get("home_id", "")):
        return f"{e['away']} ke khilaaf (ghar pe)"
    return f"{e['home']} ke khilaaf (unke ghar)"


_DAYS = ["Somvaar", "Mangalvaar", "Budhvaar", "Guruvaar", "Shukravaar", "Shanivaar", "Ravivaar"]
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def ist_when(d: datetime, today=None) -> str:
    """UTC datetime -> 'aaj raat 1:30 baje' / 'Shanivaar 8 Nov, shaam 5:30 baje' (IST).
    Matches after midnight (00:00-03:59 IST) are named by the evening before,
    the way people say it: a 01:30 Wednesday kick-off is 'Mangalvaar raat 1:30'."""
    x = d.astimezone(IST)
    today = today or datetime.now(IST).date()
    h, m = x.hour, x.minute
    day_date = x.date()
    if h < 4:
        part, day_date = "raat", day_date - timedelta(days=1)
    else:
        part = ("subah" if h < 12 else "dopahar" if h < 16 else
                "shaam" if h < 19 else "raat")
    t = f"{part} {h % 12 or 12}:{m:02d} baje"
    dd = (day_date - today).days
    if dd == 0:
        day = "aaj"
    elif dd in (1, -1):
        day = "kal"
    else:
        day = f"{_DAYS[day_date.weekday()]} {day_date.day} {_MON[day_date.month - 1]}"
    return f"{day} {t}"


def barca(now_utc: Optional[datetime] = None) -> Optional[dict]:
    """Fetch last/next/live. ESPN first (past + fixtures in parallel), live score
    from the competition's scoreboard when a match is on; TheSportsDB fallback."""
    now_utc = now_utc or datetime.now(timezone.utc)
    r = parallel({"past": lambda: _get_json(URL_BARCA_PAST),
                  "next": lambda: _get_json(URL_BARCA_NEXT)})
    past = parse_espn_events(r["past"]) if r["past"] else []
    fut = parse_espn_events(r["next"]) if r["next"] else []
    src = "espn"
    if not past and not fut:
        t = parallel({"past": lambda: _get_json(URL_TSDB_LAST),
                      "next": lambda: _get_json(URL_TSDB_NEXT)})
        past = parse_tsdb(t["past"]) if t["past"] else []
        fut = parse_tsdb(t["next"]) if t["next"] else []
        src = "thesportsdb"
        if not past and not fut:
            return None
    res = pick_barca(past, fut, now_utc)
    # a match that should be on right now: ask its scoreboard for the live score
    cand = res["live"] or next((e for e in fut if e.get("date") and
                                e["date"] <= now_utc <= e["date"] + timedelta(hours=2, minutes=30)), None)
    if cand and src == "espn" and cand.get("slug"):
        sb = parse_espn_events(_get_json(URL_SCOREBOARD.format(slug=cand["slug"])) or {})
        hit = next((e for e in sb if e["id"] == cand["id"]), None)
        if hit:
            hit["league"] = hit["league"] or cand["league"]
            if hit["state"] == "in":
                res["live"] = hit
            elif hit["state"] == "post":
                res["last"], res["live"] = hit, None
                if res["next"] and res["next"]["id"] == hit["id"]:
                    rest = [e for e in fut if e["state"] == "pre" and e["date"] > hit["date"]]
                    res["next"] = min(rest, key=lambda e: e["date"]) if rest else None
    res["source"] = src
    return res


def _result_line(e: dict) -> str:
    hs, as_ = e.get("hs"), e.get("as_")
    if hs is None or as_ is None:
        return f"{e['home']} vs {e['away']}"
    b_home = _is_barca(e["home"], e.get("home_id", ""))
    bs, os_ = (hs, as_) if b_home else (as_, hs)
    opp = e["away"] if b_home else e["home"]
    word = "jeeta" if bs > os_ else "haara" if bs < os_ else "draw khela"
    return f"Barca {opp} se {bs}-{os_} {word}"


def barca_text(b: Optional[dict], today=None) -> str:
    if not b:
        return "Barca ka score abhi nahi mila (ESPN aur backup dono ne jawab nahi diya)."
    parts = []
    if b.get("live"):
        e = b["live"]
        clock = e.get("clock") or e.get("detail") or ""
        parts.append(f"Barca ka match abhi chal raha hai: {e['home']} {e.get('hs', '?')} - "
                     f"{e.get('as_', '?')} {e['away']}" + (f" ({clock})" if clock else ""))
    if b.get("last") and not b.get("live"):
        e = b["last"]
        lg = f", {e['league']}" if e.get("league") else ""
        parts.append(f"Pichhla match: {_result_line(e)} ({ist_when(e['date'], today)}{lg})")
    if b.get("next"):
        e = b["next"]
        lg = f", {e['league']}" if e.get("league") else ""
        parts.append(f"Agla match: {_vs(e)}, {ist_when(e['date'], today)} IST{lg}")
    return (". ".join(parts) + ".") if parts else "Barca ke match ki jaankari nahi mili."


# ------------------------------------------------------------------ calendar / mail
def google_access() -> dict:
    """Update 18 check: this codebase has no Google OAuth (no calendar/gmail
    plugin, no token file; email_draft only opens Gmail in the browser)."""
    try:
        from pathlib import Path
        app = Path(__file__).resolve().parent.parent
        cal = any((app / "plugins" / n).exists() for n in ("calendar_control.py", "gmail_control.py"))
        tok = any((app / "config" / n).exists() for n in ("token.json", "google_token.json", "credentials.json"))
        return {"calendar": bool(cal and tok), "gmail": bool(cal and tok)}
    except Exception:
        return {"calendar": False, "gmail": False}


# ------------------------------------------------------------------ compose
def greeting(now: Optional[datetime] = None) -> str:
    h = (now or _now()).hour
    if h < 12:
        return "Good morning Abhinaba!"
    if h < 17:
        return "Namaste Abhinaba!"
    return "Good evening Abhinaba!"


def compose_morning(now: Optional[datetime] = None) -> tuple:
    """-> (text, stats dict). All sources in parallel, hard ~5.5 s bound."""
    t0 = time.monotonic()
    now = now or _now()
    r = parallel({"weather": lambda: weather(""), "news": lambda: news(3),
                  "barca": lambda: barca()})
    lines = [greeting(now) + f" Aaj {_DAYS[now.weekday()]}, {now.day} {_MON[now.month - 1]}."]
    lines.append(weather_text(r["weather"], "today") if r["weather"] else "Mausam nahi mila.")
    ga = google_access()
    if not ga["calendar"]:
        lines.append("Calendar connect nahi hai, isliye aaj ka plan nahi bata sakta.")
    lines.append(barca_text(r["barca"], now.date()) if r["barca"] else "Barca ki khabar nahi mili.")
    lines.append(news_text(r["news"]) if r["news"] else "News nahi mili.")
    ok = {k: bool(v) and not (isinstance(v, dict) and v.get("error")) for k, v in r.items()}
    return " ".join(lines), {"secs": time.monotonic() - t0, "ok": ok}


# ------------------------------------------------------------------ morning schedule
MORNING_AT = (8, 0)
MORNING_UNTIL = (11, 0)


def _state_path():
    from core import kaka_services as ks
    return ks.data_path("morning.json")


def load_state() -> dict:
    try:
        from core import kaka_services as ks
        s = ks.read_json(_state_path(), {})
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(s: dict) -> bool:
    try:
        from core import kaka_services as ks
        return ks.write_json(_state_path(), s)
    except Exception:
        return False


def set_enabled(on: bool) -> bool:
    s = load_state()
    s["enabled"] = bool(on)
    return save_state(s)


def is_enabled(s: Optional[dict] = None) -> bool:
    s = load_state() if s is None else s
    return bool(s.get("enabled", True))


def due(now: datetime, s: dict) -> bool:
    """Morning update is due: enabled, not yet run today, and between 08:00 and
    11:00. Running at 8 fires at 8:00; started later (PC was off) fires on that
    first start before 11; after 11 the day is skipped."""
    if not is_enabled(s):
        return False
    if s.get("last_run") == now.strftime("%Y-%m-%d"):
        return False
    t = (now.hour, now.minute)
    return MORNING_AT <= t < MORNING_UNTIL


def startup_briefing_overlaps(now: Optional[datetime] = None) -> bool:
    """For main.py: True when this start will get the morning update (so the old
    startup news briefing can skip its news part instead of doubling it)."""
    try:
        return due(now or _now(), load_state())
    except Exception:
        return False


def run_morning(deliver: bool = True) -> str:
    """Compose + deliver once; marks the day first so it can never repeat."""
    now = _now()
    s = load_state()
    s["last_run"] = now.strftime("%Y-%m-%d")
    save_state(s)
    text, st = compose_morning(now)
    try:
        from core import kaka_services as ks
        ks.timing("morning update", st["secs"],
                  ", ".join(f"{k} {'ok' if v else 'fail'}" for k, v in st["ok"].items()))
        if deliver:
            ks.popup("Aaj ka update", text)
            res = ks.say(text, brief=False)
            ks.log(f"SYS: Morning update -> {res}")
    except Exception as e:
        _log(f"SYS: morning deliver failed: {e}")
    return text


_THREAD = {"t": None}


def _loop(first_delay: float = 20.0, tick: float = 30.0) -> None:
    # first_delay: let the startup greeting/briefing finish before we speak
    time.sleep(first_delay)
    while True:
        try:
            if due(_now(), load_state()):
                run_morning(True)
        except Exception as e:
            _log(f"SYS: morning loop error: {e}")
        time.sleep(tick)


def start_scheduler() -> None:
    """Registered with ks.on_start (runs 2 s after Kaka starts)."""
    if _THREAD["t"] is not None and _THREAD["t"].is_alive():
        return
    t = threading.Thread(target=_loop, name="kaka-u18-morning", daemon=True)
    _THREAD["t"] = t
    t.start()


try:
    from core import kaka_services as _ks
    _ks.on_start(start_scheduler)
except Exception:
    pass
