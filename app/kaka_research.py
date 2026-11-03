r"""
app/kaka_research.py - KAKA: Deep Research mode.

"Chandrayaan-3 pe deep research karo", "deep dive into how transformers work",
"X ke baare mein detail mein research karo":

  1. plan      Gemini splits the topic into 4-6 sub-questions
  2. gather    per sub-question, in parallel threads: a search (Gemini
               grounding when its quota is open, DuckDuckGo always for page
               links), then the top pages are FETCHED and read (requests +
               BeautifulSoup), plus the Wikipedia REST summary / extract of
               the topic. Hard time budget; dead pages are skipped.
  3. synth     Gemini writes a structured report (overview, one section per
               sub-question, key facts / numbers, viewpoints) citing the
               numbered sources [1]..[n]; Kaka adds the source list itself,
               so every URL is a real one that was fetched.
  4. deliver   short spoken summary (3-5 sentences, his language), the full
               report in the on-screen panel (core/kaka_research_panel.py),
               saved as Data/Research/<date>_<slug>.md and .pdf
  5. deepen    "point 3 aur detail mein batao" / "is pe aur research karo":
               a mini-research on that section, appended to the same report.

Read-only: nothing is sent, posted, bought or deleted. Without a Gemini key
(or with its quota spent) it still gathers and saves a report built from the
pages it read. Nothing here raises out of a public function.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _fwait
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlparse

# ------------------------------------------------------------------ budgets
PLAN_TIMEOUT = 20.0          # s, the planner call
GATHER_BUDGET = 55.0         # s, all searching + page reading, wall clock
SEARCH_TIMEOUT = 25.0        # s, one grounded search
PAGE_TIMEOUT = 8.0           # s, one page
PAGES_PER_QUESTION = 3
PAGE_CHARS = 3500            # text kept per page
MAX_PAGE_BYTES = 1_500_000
SYNTH_TIMEOUT = 75.0         # s, the report call
MIN_Q, MAX_Q = 4, 6
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Kaka-Research/1.0")
WIKI_UA = "Kaka-DeepResearch/1.0 (https://github.com/abhinaba-chanda/Kaka)"

SKIP_HOSTS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "x.com",
              "twitter.com", "tiktok.com", "pinterest.", "linkedin.com", "reddit.com",
              "quora.com", "play.google.com", "apps.apple.com", "amazon.", "flipkart.com")

LANGS = {"hinglish": "Hinglish (Roman-script Hindi mixed with English)",
         "hindi": "Hindi",
         "bengali": "Bengali (Bangla)",
         "english": "English"}

_LOCK = threading.RLock()
_ST = {"running": None, "last": None}      # running: Job, last: report dict


# ------------------------------------------------------------------ helpers
def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def _log(msg: str) -> None:
    ks = _ks()
    try:
        if ks is not None:
            ks.log(msg)
        else:
            print(msg)
    except Exception:
        pass


def lang_name(lang: str) -> str:
    l = (lang or "").strip().lower()
    for k, v in LANGS.items():
        if l.startswith(k[:4]):
            return v
    if l in ("bangla", "bn"):
        return LANGS["bengali"]
    if l in ("en",):
        return LANGS["english"]
    return LANGS["hinglish"]


def slugify(text: str, n: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n].strip("-") or "research")


def research_dir() -> Path:
    ks = _ks()
    if ks is not None:
        try:
            return ks.data_path("Research", "x").parent
        except Exception:
            pass
    try:
        import kaka_paths
        p = Path(kaka_paths.DATA_ROOT) / "Research"
    except Exception:
        p = Path.home() / "Kaka" / "Data" / "Research"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ------------------------------------------------------------------ LLM (mockable)
def _llm_json(prompt: str, timeout: float, smart: bool = True, system: str = ""):
    """Gemini -> parsed JSON, or None. Walks the app's own ladder (core.gemini),
    then the REST helper (core.kaka_ai). Tests replace this function."""
    full = (system + "\n\n" + prompt) if system else prompt
    try:
        from core import gemini
        tier = gemini.SMART if smart else gemini.FAST
        js = gemini.as_json(full, tier=tier,
                            config={"response_mime_type": "application/json"},
                            timeout_ms=int(timeout * 1000))
        if js is not None:
            return js
    except Exception as e:
        _log(f"[Research] Gemini (ladder) failed: {type(e).__name__}: {str(e)[:120]}")
    try:
        from core import kaka_ai
        ok, txt = kaka_ai.text(full, timeout=min(timeout, 40.0))
        if ok and txt:
            raw = txt
            if "{" in raw and "}" in raw:
                raw = raw[raw.find("{"): raw.rfind("}") + 1]
            return json.loads(raw)
    except Exception as e:
        _log(f"[Research] Gemini (REST) failed: {type(e).__name__}: {str(e)[:120]}")
    return None


def _grounded(query: str):
    """(text, [urls]) from Gemini google_search grounding, or None. Respects
    web_search's quota circuit breaker. Tests replace this function."""
    try:
        from actions import web_search as W
    except Exception:
        return None
    if not W._gemini_available():
        return None
    try:
        key = _ks().gemini_key() if _ks() else ""
    except Exception:
        key = ""
    if not key:
        return None

    def _go():
        from core import gemini
        resp = gemini.call(query, tier=gemini.SEARCH,
                           config={"tools": [{"google_search": {}}]},
                           timeout_ms=int(SEARCH_TIMEOUT * 1000))
        if resp is None:
            raise RuntimeError("every Gemini model on the ladder failed")
        cand = resp.candidates[0]
        txt = "".join(getattr(p, "text", "") or "" for p in (cand.content.parts or []))
        urls = []
        try:
            gm = getattr(cand, "grounding_metadata", None)
            for ch in (getattr(gm, "grounding_chunks", None) or []):
                web = getattr(ch, "web", None)
                u = getattr(web, "uri", None) if web else None
                if u:
                    urls.append((u, getattr(web, "title", "") or ""))
        except Exception:
            pass
        return txt.strip(), urls

    def _wrapped():
        try:
            return _go()
        except Exception as e:
            try:
                W._note_gemini_error(e)
            except Exception:
                pass
            raise
    return W._run_bounded(_wrapped, SEARCH_TIMEOUT + 2, "Gemini research search")


_DDG_LOCK = threading.Lock()
_DDG_LAST = [0.0]
DDG_GAP = 0.7            # s between DuckDuckGo queries: a parallel burst gets empty answers


def _ddg(query: str, n: int = 6) -> list:
    try:
        from actions import web_search as W
        with _DDG_LOCK:
            wait_s = DDG_GAP - (time.monotonic() - _DDG_LAST[0])
            if wait_s > 0:
                time.sleep(wait_s)
            try:
                return W._ddg_search(query, max_results=n) or []
            finally:
                _DDG_LAST[0] = time.monotonic()
    except Exception as e:
        _log(f"[Research] DDG failed: {e}")
        return []


# ------------------------------------------------------------------ fetching
def _skip_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return True
    if u.scheme not in ("http", "https"):
        return True
    host = (u.netloc or "").lower()
    if any(h in host for h in SKIP_HOSTS):
        return True
    if u.path.lower().endswith((".pdf", ".jpg", ".png", ".mp4", ".zip", ".doc", ".docx", ".ppt", ".pptx")):
        return True
    return False


def extract_text(html: str, max_chars: int = PAGE_CHARS) -> tuple:
    """(title, readable text) from an HTML page. BeautifulSoup (in Kaka's
    requirements) with the stdlib html.parser backend."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return "", _clean(re.sub(r"<[^>]+>", " ", html or ""))[:max_chars]
    soup = BeautifulSoup(html or "", "html.parser")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = _clean(og["content"])
    elif soup.title and soup.title.string:
        title = _clean(soup.title.string)
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside",
                     "form", "svg", "iframe", "button", "figure", "table"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    parts, total = [], 0
    for el in root.find_all(["h1", "h2", "h3", "p", "li"]):
        t = _clean(el.get_text(" "))
        if el.name == "li" and len(t) < 40:
            continue
        if el.name == "p" and len(t) < 50:
            continue
        if el.name in ("h1", "h2", "h3"):
            if len(t) < 3 or len(t) > 120:
                continue
            t = "## " + t
        parts.append(t)
        total += len(t)
        if total > max_chars * 1.3:
            break
    text = "\n".join(parts)
    if len(text) < 200:                     # odd layouts: fall back to all text
        text = _clean(root.get_text(" "))
    return title, strip_refs(text)[:max_chars]


_REF = re.compile(r"\[\s*(?:\d{1,3}|[a-z]|citation needed|note \d+|edit)\s*\]", re.I)


def strip_refs(text: str) -> str:
    """Remove a page's own footnote markers ([4], [ 5 ], [citation needed]) so
    they cannot be mistaken for Kaka's source numbers."""
    return re.sub(r" {2,}", " ", _REF.sub("", text or ""))


def fetch_page(url: str, timeout: float = PAGE_TIMEOUT) -> Optional[dict]:
    """{url (final), title, text} or None for a dead / non-HTML / empty page."""
    if _skip_url(url):
        return None
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
                         timeout=(4, timeout), stream=True, allow_redirects=True)
        if r.status_code != 200:
            r.close()
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        if ctype and "html" not in ctype and "text/plain" not in ctype:
            r.close()
            return None
        final = r.url or url
        if final != url and _skip_url(final):
            r.close()
            return None
        buf, size, t0 = [], 0, time.monotonic()
        for chunk in r.iter_content(65536):
            buf.append(chunk)
            size += len(chunk)
            if size > MAX_PAGE_BYTES or time.monotonic() - t0 > timeout:
                break
        r.close()
        raw = b"".join(buf)
        enc = r.encoding if (r.encoding and r.encoding.lower() != "iso-8859-1") else None
        try:
            html = raw.decode(enc or "utf-8", errors="replace")
        except Exception:
            html = raw.decode("utf-8", errors="replace")
        if "text/plain" in ctype:
            title, text = "", _clean(html)[:PAGE_CHARS]
        else:
            title, text = extract_text(html)
        if len(text) < 300:
            return None
        return {"url": final, "title": title or urlparse(final).netloc, "text": text}
    except Exception:
        return None


def wikipedia(topic: str, timeout: float = 10.0) -> Optional[dict]:
    """{url, title, text} from the Wikipedia REST summary + plain-text extract."""
    try:
        import requests
        h = {"User-Agent": WIKI_UA}
        s = requests.get("https://en.wikipedia.org/w/api.php",
                         params={"action": "query", "list": "search", "srsearch": topic,
                                 "srlimit": 1, "format": "json"},
                         headers=h, timeout=timeout).json()
        hits = (s.get("query") or {}).get("search") or []
        if not hits:
            return None
        title = hits[0]["title"]
        summ = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                            + quote(title.replace(" ", "_"), safe=""),
                            headers=h, timeout=timeout).json()
        ex = requests.get("https://en.wikipedia.org/w/api.php",
                          params={"action": "query", "prop": "extracts", "explaintext": 1,
                                  "exsectionformat": "plain", "titles": title,
                                  "format": "json", "redirects": 1},
                          headers=h, timeout=timeout).json()
        pages = (ex.get("query") or {}).get("pages") or {}
        extract = ""
        for pg in pages.values():
            extract = pg.get("extract") or ""
        summary = summ.get("extract") or ""
        url = ((summ.get("content_urls") or {}).get("desktop") or {}).get("page") \
            or "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        if extract and summary and extract.strip().startswith(summary.strip()[:80]):
            text = extract[:6500].strip()            # the extract already opens with the summary
        else:
            text = (summary + "\n" + extract[:6000]).strip()
        text = strip_refs(text)
        if not text:
            return None
        return {"url": url, "title": f"{title} - Wikipedia", "text": text[:6500],
                "summary": summary}
    except Exception:
        return None


# ------------------------------------------------------------------ sources
class Sources:
    """Numbered, de-duplicated sources [1]..[n]. Thread-safe."""

    def __init__(self, start: int = 1):
        self._lock = threading.Lock()
        self.items: list = []
        self._start = start

    @staticmethod
    def _key(url: str) -> str:
        u = urlparse(url)
        return (u.netloc.lower().replace("www.", "") + u.path.rstrip("/")).lower()

    def add(self, url: str, title: str, text: str = "", question: int = 0) -> int:
        with self._lock:
            k = self._key(url)
            for s in self.items:
                if s["key"] == k:
                    if question and question not in s["questions"]:
                        s["questions"].append(question)
                    if len(text) > len(s["text"]):
                        s["text"] = text
                    return s["n"]
            n = self._start + len(self.items)
            self.items.append({"n": n, "url": url, "title": _clean(title)[:140] or url,
                               "text": text, "key": k, "questions": [question] if question else []})
            return n

    def numbers(self) -> set:
        return {s["n"] for s in self.items}


# ------------------------------------------------------------------ steps
def plan(topic: str, lang: str = "hinglish", n_min: int = MIN_Q, n_max: int = MAX_Q,
         context: str = "") -> list:
    """4-6 English search-ready sub-questions. Falls back to a fixed outline."""
    prompt = (
        "You plan web research. Split the topic below into between "
        f"{n_min} and {n_max} distinct sub-questions that together give a deep, "
        "complete understanding (background, how it works / what happened, key "
        "facts and numbers, current status / latest developments, impact, "
        "debates or different viewpoints - whatever fits this topic). Each "
        "sub-question must be a short, self-contained English web-search query "
        "that names the topic (no pronouns). Return JSON only: "
        '{"topic_en": "<topic in English>", "questions": ["...", "..."]}\n\n'
        f"TOPIC: {topic}" + (f"\nCONTEXT (go deeper on this): {context}" if context else ""))
    js = _llm_json(prompt, PLAN_TIMEOUT, smart=False)
    qs = []
    if isinstance(js, dict):
        qs = [_clean(str(q)) for q in (js.get("questions") or []) if _clean(str(q))]
    elif isinstance(js, list):
        qs = [_clean(str(q)) for q in js if _clean(str(q))]
    seen, out = set(), []
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q[:200])
    if len(out) >= n_min:
        return out[:n_max]
    t = topic.strip()
    fallback = [f"{t} overview and background",
                f"{t} explained: how it works, key details",
                f"{t} key facts and numbers",
                f"{t} latest news and current status",
                f"{t} impact, criticism and different viewpoints"]
    if n_max <= 3:
        fallback = [f"{t} explained in detail", f"{t} key facts and numbers",
                    f"{t} latest developments and debates"]
    for q in fallback:
        if len(out) >= n_max:
            break
        if q.lower() not in seen:
            out.append(q)
    return out[:max(n_min, min(n_max, len(out)))]


def research_question(i: int, q: str, sources: Sources, deadline: float,
                      stop: Optional[threading.Event] = None) -> dict:
    """Search + read for one sub-question. -> {i, q, summary, nums, engine}."""
    res = {"i": i, "q": q, "summary": "", "nums": [], "engine": "none"}
    if stop is not None and stop.is_set():
        return res
    urls = []
    g = None
    try:
        g = _grounded(q) if time.monotonic() < deadline - 10 else None
    except Exception:
        g = None
    if g:
        txt, gurls = g
        res["summary"] = (txt or "")[:2500]
        res["engine"] = "gemini"
        urls.extend(gurls[:4])
    hits = _ddg(q, 6) if time.monotonic() < deadline - 5 else []
    if not hits and time.monotonic() < deadline - 8:
        # DuckDuckGo sometimes answers a burst of parallel queries with nothing;
        # one retry, staggered, with a shorter query.
        time.sleep(0.8 + 0.4 * (i % 4))
        short = " ".join(re.sub(r"[?\"']", " ", q).split()[:8])
        hits = _ddg(short, 6)
    if hits:
        if res["engine"] == "none":
            res["engine"] = "ddg"
            res["summary"] = "\n".join(f"- {h.get('title','')}: {h.get('snippet','')}"
                                       for h in hits[:5])[:2500]
        urls.extend((h.get("url") or "", h.get("title") or "") for h in hits)
    read, tried = 0, set()
    for url, title in urls:
        if read >= PAGES_PER_QUESTION or time.monotonic() > deadline - 2:
            break
        if (stop is not None and stop.is_set()) or not url or url in tried:
            continue
        tried.add(url)
        if _skip_url(url):
            continue
        left = max(2.0, min(PAGE_TIMEOUT, deadline - time.monotonic() - 1))
        page = fetch_page(url, timeout=left)
        if not page:
            continue
        n = sources.add(page["url"], page.get("title") or title, page["text"], question=i)
        res["nums"].append(n)
        read += 1
    if not read and time.monotonic() < deadline - 6 and not (stop is not None and stop.is_set()):
        w = wikipedia(q, timeout=max(3.0, min(8.0, deadline - time.monotonic() - 2)))
        if w:                               # nothing readable from search: Wikipedia's best match
            res["nums"].append(sources.add(w["url"], w["title"], w["text"], question=i))
            if res["engine"] == "none":
                res["engine"] = "wikipedia"
    return res


def _sources_block(sources: Sources, per: int = 2600, total: int = 60000) -> str:
    out, used = [], 0
    for s in sources.items:
        chunk = f"[{s['n']}] {s['title']} — {s['url']}\n{s['text'][:per]}\n"
        if used + len(chunk) > total:
            chunk = f"[{s['n']}] {s['title']} — {s['url']}\n{s['text'][:600]}\n"
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out)


_CITE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def fix_citations(text: str, valid: set) -> str:
    """Drop citation numbers that do not exist (a model that invents [9] when
    there are 7 sources would otherwise point at nothing)."""
    def _rep(m):
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        keep = [str(n) for n in nums if n in valid]
        return "[" + ", ".join(keep) + "]" if keep else ""
    return _CITE.sub(_rep, text or "")


def synthesize(topic: str, questions: list, findings: list, sources: Sources,
               lang: str = "hinglish", focus: str = "") -> dict:
    """-> {overview, sections[{title, body}], key_facts[], viewpoints[], summary, llm}"""
    lname = lang_name(lang)
    notes = "\n\n".join(
        f"SUB-QUESTION {f['i']}: {f['q']}\nSearch notes: {f['summary'][:1500]}\n"
        f"Pages read for it: {', '.join('[%d]' % n for n in f['nums']) or 'none'}"
        for f in findings)
    what = f'a deeper dive into "{focus}" (part of research on "{topic}")' if focus \
        else f'"{topic}"'
    prompt = (
        f"Write a deep research report on {what}, using ONLY the numbered sources "
        "and search notes below. Rules:\n"
        "- Cite sources inline as [n] using the numbers given. EVERY number, date, "
        "statistic or specific claim must carry a citation. Never cite a number "
        "that is not in the list. If sources disagree, say so with both citations.\n"
        "- Write the report in clear, simple English. Be detailed and concrete "
        "(several sentences per section). No markdown headings inside text; "
        "plain sentences, you may use **bold** sparingly.\n"
        f"- One section per sub-question, in the same order ({len(questions)} sections).\n"
        f"- 'summary': 3-5 short spoken sentences in {lname}, natural and warm, "
        "no citations, no symbols, no lists - this is read aloud by a voice assistant.\n"
        "Return JSON only:\n"
        '{"title": "...", "overview": "<1 paragraph>", '
        '"sections": [{"title": "<short heading>", "body": "<2-4 paragraphs with [n] citations>"}], '
        '"key_facts": ["<fact with number and [n]>", "..."], '
        '"viewpoints": ["<one perspective with [n]>", "..."], '
        '"summary": "<spoken summary>"}\n\n'
        f"SUB-QUESTIONS:\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)) +
        f"\n\nSEARCH NOTES:\n{notes}\n\nSOURCES:\n{_sources_block(sources)}")
    js = _llm_json(prompt, SYNTH_TIMEOUT, smart=True)
    valid = sources.numbers()
    if isinstance(js, dict) and (js.get("sections") or js.get("overview")):
        secs = []
        for s in js.get("sections") or []:
            if isinstance(s, dict):
                secs.append({"title": _clean(str(s.get("title") or ""))[:120],
                             "body": fix_citations(str(s.get("body") or "").strip(), valid)})
        while len(secs) < len(questions):
            secs.append({"title": questions[len(secs)], "body": ""})
        return {"title": _clean(str(js.get("title") or topic))[:150],
                "overview": fix_citations(str(js.get("overview") or "").strip(), valid),
                "sections": secs[:max(len(questions), 1)],
                "key_facts": [fix_citations(str(x), valid) for x in (js.get("key_facts") or []) if str(x).strip()][:12],
                "viewpoints": [fix_citations(str(x), valid) for x in (js.get("viewpoints") or []) if str(x).strip()][:8],
                "summary": _clean(str(js.get("summary") or "")),
                "llm": True}
    return fallback_report(topic, questions, findings, sources, focus)


def fallback_report(topic, questions, findings, sources: Sources, focus: str = "") -> dict:
    """No Gemini answer: a report made straight from what was read."""
    by_n = {s["n"]: s for s in sources.items}
    secs = []
    for f in findings:
        paras = []
        for n in f["nums"][:3]:
            s = by_n.get(n)
            if not s:
                continue
            txt = re.sub(r"(^|\n)## [^\n]*", " ", s["text"])
            sent = re.split(r"(?<=[.!?])\s+", _clean(txt))
            paras.append(" ".join(sent[:4])[:700] + f" [{n}]")
        if not paras and f.get("summary"):
            paras.append(f["summary"][:900])
        secs.append({"title": f["q"], "body": "\n\n".join(paras) or "(No readable page found for this one.)"})
    wiki = next((s for s in sources.items if "wikipedia.org" in s["url"]), None)
    overview = summary = ""
    if wiki:
        sents, seen = [], set()
        for x in re.split(r"(?<=[.!?])\s+", _clean(wiki["text"])):
            k = x[:60].lower()
            if x and k not in seen:
                seen.add(k)
                sents.append(x)
            if len(sents) >= 4:
                break
        overview = " ".join(sents) + f" [{wiki['n']}]"
        summary = " ".join(sents[:3])
    return {"title": (f"{focus} (deeper)" if focus else topic), "overview": overview,
            "sections": secs, "key_facts": [], "viewpoints": [],
            "summary": summary, "llm": False}


# ------------------------------------------------------------------ report <-> files
def to_markdown(rep: dict) -> str:
    L = [f"# Deep Research: {rep['title']}", "",
         f"*{rep['topic']} — {rep['created']} — by Kaka*", ""]
    if not rep.get("llm", True):
        L += ["> Gemini was not available, so this report is put together straight "
              "from the pages Kaka read (no AI summary).", ""]
    if rep.get("overview"):
        L += ["## Overview", "", rep["overview"], ""]
    for i, s in enumerate(rep["sections"], 1):
        L += [f"## {i}. {s['title']}", "", s["body"] or "_(nothing found)_", ""]
    if rep.get("key_facts"):
        L += ["## Key facts & numbers", ""] + [f"- {x}" for x in rep["key_facts"]] + [""]
    if rep.get("viewpoints"):
        L += ["## Different viewpoints", ""] + [f"- {x}" for x in rep["viewpoints"]] + [""]
    for d in rep.get("deeper") or []:
        L += [f"## Deeper: {d['title']}", ""]
        if d.get("intro"):
            L += [d["intro"], ""]
        for s in d.get("sections") or []:
            L += [f"### {s['title']}", "", s["body"], ""]
        if d.get("key_facts"):
            L += [f"- {x}" for x in d["key_facts"]] + [""]
    L += ["## Sources", ""]
    for s in rep["sources"]:
        L.append(f"{s['n']}. [{s['title']}]({s['url']})")
    L.append("")
    return "\n".join(L)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_html(text: str, urls: dict, link_color: str) -> str:
    h = _esc(text)
    h = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)

    def _rep(m):
        out = []
        for x in re.findall(r"\d+", m.group(1)):
            u = urls.get(int(x))
            out.append(f'<a href="{_esc(u)}" style="color:{link_color};text-decoration:none">{x}</a>'
                       if u else x)
        return "<sup>[" + ", ".join(out) + "]</sup>"
    return _CITE.sub(_rep, h)


def _paras(text: str, urls, c) -> str:
    out = []
    for p in re.split(r"\n\s*\n|\n", text or ""):
        if p.strip():
            out.append(f"<p>{_inline_html(p.strip(), urls, c)}</p>")
    return "".join(out)


def to_html(rep: dict, dark: bool = True, progress: str = "") -> str:
    if dark:
        fg, dim, acc, link, head = "#e6f4fa", "#8fa9b5", "#ffcc33", "#6fb2ff", "#ffcc33"
    else:
        fg, dim, acc, link, head = "#111111", "#555555", "#a50044", "#1a4fa0", "#004d98"
    urls = {s["n"]: s["url"] for s in rep.get("sources") or []}
    H = [f'<div style="color:{fg};">',
         f'<h1 style="color:{head};">{_esc(rep.get("title") or rep.get("topic", ""))}</h1>',
         f'<p style="color:{dim};">{_esc(rep.get("topic", ""))} &nbsp;·&nbsp; '
         f'{_esc(rep.get("created", ""))} &nbsp;·&nbsp; Deep Research by Kaka</p>']
    if progress:
        H.append(f'<p style="color:{acc};">{_esc(progress)}</p>')
    if rep.get("llm") is False:
        H.append(f'<p style="color:{dim};"><i>Gemini was not available, so this report is '
                 'put together straight from the pages Kaka read.</i></p>')
    if rep.get("overview"):
        H.append(f'<h2 style="color:{acc};">Overview</h2>' + _paras(rep["overview"], urls, link))
    for i, s in enumerate(rep.get("sections") or [], 1):
        H.append(f'<h2 style="color:{acc};">{i}. {_esc(s["title"])}</h2>'
                 + _paras(s["body"] or "(nothing found)", urls, link))
    if rep.get("key_facts"):
        H.append(f'<h2 style="color:{acc};">Key facts &amp; numbers</h2><ul>'
                 + "".join(f"<li>{_inline_html(x, urls, link)}</li>" for x in rep["key_facts"]) + "</ul>")
    if rep.get("viewpoints"):
        H.append(f'<h2 style="color:{acc};">Different viewpoints</h2><ul>'
                 + "".join(f"<li>{_inline_html(x, urls, link)}</li>" for x in rep["viewpoints"]) + "</ul>")
    for k, d in enumerate(rep.get("deeper") or []):
        if k == len(rep["deeper"]) - 1:
            H.append('<a name="deeper"></a>')
        H.append(f'<h2 style="color:{acc};">Deeper: {_esc(d["title"])}</h2>')
        if d.get("intro"):
            H.append(_paras(d["intro"], urls, link))
        for s in d.get("sections") or []:
            H.append(f'<h3>{_esc(s["title"])}</h3>' + _paras(s["body"], urls, link))
        if d.get("key_facts"):
            H.append("<ul>" + "".join(f"<li>{_inline_html(x, urls, link)}</li>"
                                      for x in d["key_facts"]) + "</ul>")
    if rep.get("sources"):
        H.append(f'<h2 style="color:{acc};">Sources</h2><ol>')
        for s in rep["sources"]:
            H.append(f'<li value="{s["n"]}"><a href="{_esc(s["url"])}" style="color:{link};">'
                     f'{_esc(s["title"])}</a><br/><span style="color:{dim};">{_esc(s["url"])}</span></li>')
        H.append("</ol>")
    H.append("</div>")
    return "".join(H)


def save(rep: dict) -> dict:
    """Write <date>_<slug>.md and .pdf (same name each time the report grows).
    -> {"md": path, "pdf": path or "", "pdf_error": ""}"""
    d = research_dir()
    base = rep.get("file_base") or f"{rep['created'][:10]}_{slugify(rep['topic'])}"
    rep["file_base"] = base
    md = d / f"{base}.md"
    out = {"md": str(md), "pdf": "", "pdf_error": ""}
    try:
        md.write_text(to_markdown(rep), encoding="utf-8")
    except Exception as e:
        out["md"] = ""
        out["md_error"] = str(e)
    try:
        from core import kaka_research_panel as P
        pdf = d / f"{base}.pdf"
        ok, err = P.write_pdf(to_html(rep, dark=False), str(pdf))
        if ok:
            out["pdf"] = str(pdf)
        else:
            out["pdf_error"] = err
    except Exception as e:
        out["pdf_error"] = f"{type(e).__name__}: {e}"
    try:
        (d / "last.json").write_text(json.dumps(_public(rep), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    rep["paths"] = out
    return out


def _public(rep: dict) -> dict:
    keep = dict(rep)
    keep["sources"] = [{k: s.get(k) for k in ("n", "url", "title", "text")} for s in rep.get("sources") or []]
    for s in keep["sources"]:
        s["text"] = (s.get("text") or "")[:1500]
    return keep


def load_last() -> Optional[dict]:
    with _LOCK:
        if _ST["last"] is not None:
            return _ST["last"]
    try:
        rep = json.loads((research_dir() / "last.json").read_text(encoding="utf-8"))
        if isinstance(rep, dict) and rep.get("sections"):
            with _LOCK:
                _ST["last"] = rep
            return rep
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ the job
class Job:
    def __init__(self, topic: str, lang: str = "hinglish", kind: str = "new",
                 point: int = 0, focus: str = "",
                 progress: Optional[Callable[[dict], None]] = None,
                 done: Optional[Callable[[dict], None]] = None):
        self.topic = topic.strip()
        self.lang = lang or "hinglish"
        self.kind = kind
        self.point = point
        self.focus = focus.strip()
        self.progress_cb = progress
        self.done_cb = done
        self.stop = threading.Event()
        self.state = {"stage": "start", "questions": [], "done": [], "msg": ""}
        self.result: Optional[dict] = None
        self.thread: Optional[threading.Thread] = None

    def _p(self, stage: str, msg: str, **kw):
        self.state.update(stage=stage, msg=msg, **kw)
        _log(f"SYS: Research: {msg}")
        if self.progress_cb:
            try:
                self.progress_cb(dict(self.state, topic=self.topic))
            except Exception:
                pass

    def _gather(self, questions, sources, deadline, offset=0):
        findings = [None] * len(questions)
        done = []
        with ThreadPoolExecutor(max_workers=min(6, len(questions) + 1),
                                thread_name_prefix="kaka-research") as ex:
            futs = {ex.submit(research_question, offset + i + 1, q, sources, deadline, self.stop): i
                    for i, q in enumerate(questions)}
            pending = set(futs)
            while pending and time.monotonic() < deadline + 3:
                fin, pending = _fwait(pending, timeout=0.5)
                for f in fin:
                    i = futs[f]
                    try:
                        findings[i] = f.result()
                    except Exception:
                        findings[i] = None
                    done.append(i)
                    self._p("gather", f"sub-question {len(done)}/{len(questions)} done: {questions[i][:70]}",
                            done=sorted(done), sources=len(sources.items))
                if self.stop.is_set():
                    break
            for f in pending:          # overran the budget: carry on without them
                f.cancel()
            ex.shutdown(wait=False, cancel_futures=True)
        return [f if f else {"i": offset + i + 1, "q": questions[i], "summary": "", "nums": [], "engine": "timeout"}
                for i, f in enumerate(findings)]

    def run_new(self) -> dict:
        t0 = time.monotonic()
        self._p("plan", f"'{self.topic}' ko sub-sawalon mein tod raha hoon...")
        questions = plan(self.topic, self.lang)
        self._p("plan", f"{len(questions)} sub-questions ready", questions=questions, done=[])
        sources = Sources()
        deadline = time.monotonic() + GATHER_BUDGET
        wiki_box = {}

        def _wiki():
            wiki_box["w"] = wikipedia(self.topic)
        wt = threading.Thread(target=_wiki, daemon=True)
        wt.start()
        findings = self._gather(questions, sources, deadline)
        wt.join(max(0.1, deadline - time.monotonic()))
        w = wiki_box.get("w")
        if w:
            sources.add(w["url"], w["title"], w["text"])
        if self.stop.is_set():
            return {"ok": False, "stopped": True}
        self._p("synth", f"{len(sources.items)} sources padh liye - report likh raha hoon...",
                sources=len(sources.items))
        if not sources.items and not any(f.get("summary") for f in findings):
            return {"ok": False, "error": "koi page ya search result nahi mila (internet check karo)"}
        body = synthesize(self.topic, questions, findings, sources, self.lang)
        rep = {"topic": self.topic, "lang": self.lang,
               "created": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "questions": questions, "deeper": [],
               "sources": [{k: s[k] for k in ("n", "url", "title", "text")} for s in sources.items],
               **body}
        if not rep.get("summary"):
            rep["summary"] = rep.get("overview", "")[:500]
        paths = save(rep)
        with _LOCK:
            _ST["last"] = rep
        secs = time.monotonic() - t0
        self._p("done", f"report ready ({secs:.0f} s, {len(rep['sources'])} sources)")
        return {"ok": True, "report": rep, "paths": paths, "seconds": secs}

    def run_deepen(self) -> dict:
        t0 = time.monotonic()
        rep = load_last()
        if not rep:
            return {"ok": False, "error": "abhi koi purani research yaad nahi - pehle ek topic pe deep research karwao"}
        secs_all = rep.get("sections") or []
        title = self.focus
        if self.point and 1 <= self.point <= len(secs_all):
            title = secs_all[self.point - 1]["title"]
            if self.focus:
                title = f"{title} - {self.focus}"
        if not title:
            title = rep["topic"]
        self.topic = rep["topic"]
        self._p("plan", f"'{title}' pe aur gehri research...")
        sub = plan(f"{title} ({rep['topic']})", self.lang, n_min=2, n_max=3,
                   context=f"Already covered in the report: {rep.get('overview', '')[:600]}")
        self._p("plan", f"{len(sub)} naye sub-questions", questions=sub, done=[])
        start = max([s["n"] for s in rep.get("sources") or []] or [0]) + 1
        sources = Sources(start=start)
        old = Sources()
        old.items = [dict(s, key=Sources._key(s["url"]), questions=[]) for s in rep.get("sources") or []]
        deadline = time.monotonic() + GATHER_BUDGET * 0.8
        findings = self._gather(sub, sources, deadline)
        if self.stop.is_set():
            return {"ok": False, "stopped": True}
        # sources already in the report keep their old number
        renum, merged = {}, list(rep.get("sources") or [])
        nxt = start
        for s in sources.items:
            dup = next((o for o in old.items if o["key"] == s["key"]), None)
            if dup:
                renum[s["n"]] = dup["n"]
            else:
                renum[s["n"]] = nxt
                merged.append({"n": nxt, "url": s["url"], "title": s["title"], "text": s["text"]})
                nxt += 1
        for s in sources.items:
            s["n"] = renum[s["n"]]
        for f in findings:
            f["nums"] = sorted({renum.get(n, n) for n in f["nums"]})
        both = Sources()
        both.items = [dict(s, key=Sources._key(s["url"]), questions=[]) for s in merged]
        ctx_nums = set()
        for f in findings:
            ctx_nums.update(f["nums"])
        if self.point and 1 <= self.point <= len(secs_all):
            ctx_nums.update(int(x) for x in re.findall(r"\[(\d+)", secs_all[self.point - 1]["body"]))
        use = Sources()
        use.items = [s for s in both.items if s["n"] in ctx_nums] or both.items[-8:]
        self._p("synth", f"{len(use.items)} sources - deeper section likh raha hoon...")
        body = synthesize(rep["topic"], sub, findings, use, self.lang, focus=title)
        deeper = {"title": title, "point": self.point, "intro": body.get("overview", ""),
                  "sections": body.get("sections") or [], "key_facts": body.get("key_facts") or [],
                  "summary": body.get("summary", ""), "llm": body.get("llm", False)}
        rep.setdefault("deeper", []).append(deeper)
        rep["sources"] = merged
        paths = save(rep)
        with _LOCK:
            _ST["last"] = rep
        secs = time.monotonic() - t0
        self._p("done", f"deeper section added ({secs:.0f} s)")
        return {"ok": True, "report": rep, "paths": paths, "seconds": secs,
                "deeper": deeper}

    def run(self) -> dict:
        try:
            res = self.run_deepen() if self.kind == "deepen" else self.run_new()
        except Exception as e:
            import traceback
            traceback.print_exc()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.result = res
        with _LOCK:
            if _ST["running"] is self:
                _ST["running"] = None
        if self.done_cb:
            try:
                self.done_cb(res)
            except Exception as e:
                _log(f"[Research] done callback failed: {e}")
        return res

    def start(self) -> "Job":
        with _LOCK:
            _ST["running"] = self
        self.thread = threading.Thread(target=self.run, daemon=True, name="kaka-deep-research")
        self.thread.start()
        return self


def current() -> Optional[Job]:
    with _LOCK:
        return _ST["running"]


def last() -> Optional[dict]:
    return load_last()
