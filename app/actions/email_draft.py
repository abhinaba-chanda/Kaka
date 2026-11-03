"""
Kaka actions - write an email draft, or write a document and open it.
(Kaka update 8)

draft_email: "Rahul ko mail likho ki kal meeting 5 baje hai" -> the model writes
  a proper subject + polite body; this opens Gmail's compose window with To,
  Subject and Body filled in (Gmail is a website, so the browser is right here),
  or the PC's mail app (mailto:). It NEVER sends by itself. If he then says
  "bhej do", confirm_send=true presses Ctrl+Enter (Gmail's send shortcut) in
  that compose window.

write_document: a letter / application / essay / notes -> saved as a real file
  (Kaka Saved > Documents) and opened in Notepad, or Word (.docx when
  python-docx is present, else .rtf which Word opens).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from core import outcome

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LAST_DRAFT: dict = {}


def _saved(sub: str) -> Path:
    try:
        import kaka_paths
        return Path(kaka_paths.data_dir("Saved", sub))
    except Exception:
        p = Path.home() / "Documents" / "Kaka" / sub
        p.mkdir(parents=True, exist_ok=True)
        return p


def _slug(text: str) -> str:
    w = re.findall(r"[A-Za-z0-9]+", text or "")[:6]
    return "_".join(w)[:48] or "document"


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 500):
        q = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not q.exists():
            return q
    return p


def _open_url(url: str) -> bool:
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)        # default browser, no shell
        else:
            webbrowser.open(url)
        return True
    except Exception as e:
        print(f"[email] open failed: {e}")
        return False


def draft_email(parameters=None, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    if str(p.get("confirm_send", "")).lower() in ("1", "true", "yes"):
        return _send_draft(player)
    to = str(p.get("to") or "").strip()
    subject = str(p.get("subject") or "").strip()
    body = str(p.get("body") or "").strip()
    client = str(p.get("client") or "gmail").strip().lower()
    if not body:
        return outcome.failed("draft_email", "no email body was written - write the "
                              "full email (subject + polite body) in the call")
    addrs = [a.strip() for a in re.split(r"[;,]", to) if a.strip()]
    bad = [a for a in addrs if not _EMAIL_RE.match(a)]
    if not addrs or bad:
        who = ", ".join(bad) or "the person"
        return outcome.ask("draft_email", f"I need the email address of {who}. Ask him "
                           f"for it in one short Hinglish line (e.g. 'Rahul ka email "
                           f"address kya hai?'), then call again.")
    to_s = ",".join(addrs)
    if client in ("mailto", "mail", "outlook", "default", "mail app"):
        url = "mailto:" + urllib.parse.quote(to_s, safe="@,") + "?" + urllib.parse.urlencode(
            {"subject": subject, "body": body}, quote_via=urllib.parse.quote)
        where = "the PC's mail app"
    else:
        url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(
            {"view": "cm", "fs": "1", "to": to_s, "su": subject, "body": body},
            quote_via=urllib.parse.quote)
        where = "Gmail (browser)"
    if not _open_url(url):
        return outcome.failed("draft_email", f"could not open {where}")
    _LAST_DRAFT.clear()
    _LAST_DRAFT.update(to=to_s, subject=subject, at=time.monotonic(), client=client)
    if player:
        try:
            player.write_log(f"Kaka: email draft khola ({where}) - To: {to_s} | {subject}")
        except Exception:
            pass
    if str(p.get("go_ahead", "")).lower() in ("1", "true", "yes") and client not in (
            "mailto", "mail", "outlook", "default", "mail app"):
        # update 13: his command said send it now - no question; Gmail compose
        # needs a few seconds to load before Ctrl+Enter
        threading.Thread(target=_send_when_ready, args=(player,), daemon=True,
                         name="mail-send").start()
        return outcome.started("draft_email", opened_in=where, to=to_s, subject=subject,
                               note="draft opened; sending in a few seconds because he "
                                    "already said send - ask nothing; the result follows")
    q = f"{to_s} ko '{subject or 'ye'}' wala mail bhej doon?"
    return outcome.done("draft_email", opened_in=where, to=to_s, subject=subject,
                        sent=False,
                        note=(f"NOT sent. Ask exactly ONE short question: \"{q}\" (you may "
                              f"add a 5-word gist before it). On any yes call again with "
                              f"confirm_send=true (Gmail only); otherwise he sends it."))


def _send_when_ready(player) -> None:
    """update 13: go-ahead email - wait for Gmail compose, then the same send
    step as after a yes."""
    result = ""
    for wait in (7.0, 4.0, 4.0):
        time.sleep(wait)
        result = _send_draft(player)
        if "compose window was not found" not in result:
            break
    if player:
        try:
            player.write_log(f"Kaka: email go-ahead send -> {result[:120]}")
        except Exception:
            pass
        if hasattr(player, "request_say"):
            try:
                player.request_say("Tell the user in ONE short Hinglish line what this "
                                   "says happened to the email, no question: " + result)
            except Exception:
                pass


def _send_draft(player) -> str:
    d = dict(_LAST_DRAFT)
    if not d or time.monotonic() - d.get("at", 0) > 1200:
        return outcome.failed("draft_email", "no open email draft to send")
    if d.get("client") not in ("gmail", ""):
        return outcome.failed("draft_email", "the mail app draft must be sent by him "
                              "(press Send in the mail window)")
    try:
        from core import appidentity
        import pyautogui
        hit = None
        for name in ("chrome", "msedge", "brave", "firefox", "opera"):
            for m in appidentity.running(name):
                if any(("gmail" in t.lower() or "compose" in t.lower() or "new message" in t.lower())
                       for t in (m.titles or [])):
                    hit = m
                    break
            if hit:
                break
        if hit is None or not appidentity.focus(hit):
            return outcome.failed("draft_email", "the Gmail compose window was not found, "
                                  "so nothing was sent - he can press Send himself")
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "enter")
        _LAST_DRAFT.clear()
        return outcome.unverified("draft_email", "Ctrl+Enter (Gmail send) pressed in the "
                                  "compose window - he can see whether it went")
    except Exception as e:
        return outcome.failed("draft_email", f"could not send: {e}")


def write_document(parameters=None, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    content = str(p.get("content") or "").strip()
    title = str(p.get("title") or "").strip()
    app = str(p.get("open_in") or "notepad").strip().lower()
    if not content:
        return outcome.failed("write_document", "no content was written - write the full "
                              "text in 'content'")
    folder = _saved("Documents")
    name = _slug(title or content[:60])
    word = "word" in app or "docx" in app
    path = None
    if word:
        try:
            import docx  # python-docx
            d = docx.Document()
            if title:
                d.add_heading(title, level=1)
            for para in content.split("\n"):
                d.add_paragraph(para)
            path = _unique(folder / f"{name}.docx")
            d.save(str(path))
        except Exception:
            path = _unique(folder / f"{name}.rtf")
            esc = (content.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}"))
            esc = "".join(c if ord(c) < 128 else f"\\u{ord(c)}?" for c in esc)
            head = ""
            if title:
                t2 = "".join(c if ord(c) < 128 else f"\\u{ord(c)}?" for c in title)
                head = "\\b " + t2 + "\\b0\\par\\par "
            path.write_text("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Calibri;}}\\f0\\fs24 " + head +
                            esc.replace("\n", "\\par ") + "}", encoding="ascii")
    else:
        path = _unique(folder / f"{name}.txt")
        path.write_text((title + "\n\n" if title else "") + content, encoding="utf-8")
    opened = ""
    try:
        if word:
            from core import winapps
            t = winapps.resolve("Word")
            if t is not None and t.kind == "exe":
                subprocess.Popen([t.value, str(path)])
                opened = "Word"
            elif hasattr(os, "startfile"):
                os.startfile(str(path))
                opened = "the default app"
        else:
            np = os.path.expandvars(r"%WINDIR%\System32\notepad.exe")
            subprocess.Popen([np if os.path.isfile(np) else "notepad.exe", str(path)])
            opened = "Notepad"
    except Exception as e:
        print(f"[write_document] open failed: {e}")
    if player:
        try:
            player.write_log(f"Kaka: saved {path}" + (f" - opened in {opened}" if opened else ""))
        except Exception:
            pass
    if not path.exists():
        return outcome.failed("write_document", "the file could not be saved")
    return outcome.done("write_document", saved=str(path), opened_in=opened or "not opened")


def _kaka_user_name() -> str:          # KAKA: user name (app/kaka_user.py)
    try:
        import kaka_user
    except Exception:
        try:
            import os as _o, sys as _s
            _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
            import kaka_user
        except Exception:
            return "dost"
    try:
        return kaka_user.user_name()
    except Exception:
        return "dost"


TOOL = {
    "name": "draft_email",
    "description": (
        "Writes an EMAIL and opens it ready to send - never sends by itself. You "
        "write a clear subject and a polite, complete body (English unless he asks "
        "for Hindi/Hinglish), signed '" + _kaka_user_name() + "'. Opens Gmail compose in the browser "
        "with To/Subject/Body filled (client='mailto' for the PC's mail app). "
        "'Rahul ko mail likho ki kal meeting 5 baje hai' -> this tool. If the email "
        "address is not known, it asks for it. Opening the draft needs no question. "
        "After it opens, ask ONE question with name + subject ('Rahul ko meeting wala "
        "mail bhej doon?'); on any yes call again with confirm_send=true. If his "
        "command said 'abhi bhej do / seedha bhejo / bina pooche' -> go_ahead=true "
        "(sends after it loads, no question)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "to": {"type": "STRING", "description": "Recipient email address(es), comma separated"},
            "subject": {"type": "STRING", "description": "Email subject"},
            "body": {"type": "STRING", "description": "Full email body, with greeting and sign-off"},
            "client": {"type": "STRING", "description": "gmail (default) | mailto"},
            "confirm_send": {"type": "BOOLEAN",
                             "description": "true ONLY after he clearly said yes to sending the open draft"},
            "go_ahead": {"type": "BOOLEAN",
                         "description": "true when his command itself said send now / without asking"},
        },
    },
    "handler": draft_email,
}
