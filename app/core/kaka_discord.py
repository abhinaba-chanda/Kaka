r"""
core/kaka_discord.py - Kaka update 20 (feature 29): Discord remote control.

Abhinaba DMs his own Discord bot from his phone ("PC lock karo", "status",
"screenshot", "volume 30", "gaana next", "reminder 10 minute baad chai",
"note karo doodh", "recording shuru", "shutdown" ...) and Kaka does it on the PC,
from anywhere.

    * The bot runs in ONE background daemon thread with its own asyncio loop.
      discord.py is imported only inside that thread and only when a token
      exists - Kaka's startup cost is ~0.
    * Token: Windows user variable KAKA_DISCORD_TOKEN, saved through the API
      KEYS panel (row "DISCORD BOT TOKEN", kaka_keys.set_secret - the same
      helper the spare KAKA_EXTRA_KEY_n slots use). config/api_keys.json is
      never read or written for it.
    * Obeys ONLY his Discord user id (F:\Kaka\Data\discord.json "owner_id",
      no default in the public copy - put your own id there), only in DMs to the bot. Everyone else, guild
      channels and bots are ignored silently.
    * Fast built-ins are parsed locally (no AI call). Everything else goes
      through a one-shot Gemini REST call with Kaka's own tool declarations
      (the running action registry), the chosen tool runs through that registry
      and the result comes back as text.
    * Update-13 rule: shutdown / restart / PC sleep and anything that sends,
      deletes, buys, posts, installs or overwrites -> the bot asks "pakka?
      haan/na" and waits 2 min; only haan/ha/yes runs it.
    * Kaka logs + pops up "Discord se command aaya: ..." and speaks one short line
      for actions that change something - never while asleep (commands still
      run while Kaka sleeps, without waking him).
    * Robust: exponential reconnect backoff (2 s .. 5 min), no crash when
      Discord is blocked/offline, a wrong token stops retries until the token
      changes, stops cleanly with Kaka.

    python -m core.kaka_discord --install   # pip install discord.py (timeout, one line)
    python -m core.kaka_discord --steps     # the bot setup steps for him
    python -m core.kaka_discord --check     # selfcheck line
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Callable, Optional

TOKEN_VAR = "KAKA_DISCORD_TOKEN"
OWNER_DEFAULT = 0                # public copy: set YOUR Discord user id in <Data>\discord.json "owner_id"
PIP_SPEC = "discord.py==2.7.1"
CONFIRM_SECONDS = 120.0
BACKOFF_START = 2.0
BACKOFF_CAP = 300.0
TOKEN_POLL = 5.0
MAX_FILE = 9_500_000             # Discord bot upload limit is 10 MB

SETUP_STEPS = (
    "DISCORD BOT BANANE KE STEPS (5 min, ek baar):\n"
    "1. Phone/PC browser mein kholo: https://discord.com/developers/applications -> New Application -> naam \"Kaka\" -> Create.\n"
    "2. Left menu -> Bot -> Reset Token -> Yes -> Copy (ye token kisi ko mat bhejna, WhatsApp pe bhi nahi).\n"
    "3. Usi Bot page pe neeche \"Message Content Intent\" ON karo -> Save Changes.\n"
    "4. Left menu -> OAuth2 -> URL Generator -> Scopes mein \"bot\" tick karo -> neeche bani link copy karo.\n"
    "5. Woh link kholo -> apna private server chuno (na ho to Discord app mein + dabake ek bana lo) -> Authorize.\n"
    "6. PC pe Kaka -> Settings -> API KEYS -> DISCORD BOT TOKEN box mein token paste -> SAVE -> TEST.\n"
    "7. Discord app mein server ke member list se Kaka bot pe tap -> Message -> likho: status\n"
    "Kaka sirf tumhari apni Discord ID ki baat sunega, baaki sab ignore."
)

HELP_TEXT = (
    "Kaka remote commands:\n"
    "- PC lock karo | status | screenshot\n"
    "- webcam photo / camera / photo lo (PC ke camera se photo)\n"
    "- volume 30 | awaaz kam / badhao | mute / unmute\n"
    "- gaana pause / play / next / previous\n"
    "- reminder 10 minute baad chai | reminders batao\n"
    "- note karo <baat> | notes padho\n"
    "- recording shuru / band\n"
    "- PC sleep | shutdown | restart (pehle pakka poochunga)\n"
    "- chor alarm yahan (server channel mein likho - alerts wahin aayenge) | chor alarm dm | chor alarm kahan\n"
    "- aur kuch bhi normal Hinglish mein - Kaka apne tools se karega."
)

YES_WORDS = {"haan", "han", "ha", "haa", "haan ji", "ha ji", "haanji", "yes", "y", "ok haan",
             "haan karo", "haan kar do", "kar do", "yes do it", "yep", "yeah", "hmm haan"}
NO_WORDS = {"na", "nah", "nahi", "nai", "no", "n", "mat karo", "rehne do", "cancel", "ruk", "ruko"}

# tools that reach another person / spend / post -> one question first
CONFIRM_TOOLS = {"send_message", "whatsapp_call", "discord_call", "draft_email",
                 "screenshot_send", "game_updater"}
DANGER_WORDS = {"delete", "remove", "clear", "hatao", "hata", "shutdown", "restart", "reboot",
                "install", "uninstall", "buy", "purchase", "post", "overwrite", "send",
                "format", "erase", "wipe", "kill", "logoff", "sign_out", "signout"}
# tools Gemini is not offered from the phone
SKIP_TOOLS = {"discord_remote"}

# --------------------------------------------------------------------- state
STATE = {"status": "idle", "detail": "", "user": "", "since": 0.0, "last_cmd": "", "error": ""}
_LOCK = threading.Lock()
_THREAD: dict = {"t": None, "stop": None, "loop": None, "client": None}

# test hooks
_now: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def _ks():
    from core import kaka_services as ks
    return ks


def _log(text: str) -> None:
    try:
        _ks().log(text)
    except Exception:
        pass


def _popup(title: str, text: str) -> None:
    try:
        _ks().popup(title, text)
    except Exception:
        pass


def _set(status: str, detail: str = "", **kw) -> None:
    with _LOCK:
        if STATE.get("status") != status:
            STATE["since"] = time.time()
        STATE["status"] = status
        STATE["detail"] = detail
        STATE.update(kw)


# ------------------------------------------------------------------ settings
def _settings_path():
    return _ks().data_path("discord.json")


def settings() -> dict:
    d = {"owner_id": OWNER_DEFAULT, "enabled": True}
    try:
        got = _ks().read_json(_settings_path(), {})
        if isinstance(got, dict):
            d.update(got)
    except Exception:
        pass
    return d


def save_settings(**kw) -> bool:
    d = settings()
    d.update(kw)
    try:
        return bool(_ks().write_json(_settings_path(), d))
    except Exception:
        return False


def owner_id() -> int:
    try:
        return int(str(settings().get("owner_id") or OWNER_DEFAULT).strip())
    except Exception:
        return OWNER_DEFAULT


def token() -> str:
    """Bot token from the Windows user variable (API KEYS panel). Never logged."""
    try:
        import kaka_keys as K
        return K.get_secret(TOKEN_VAR)
    except Exception:
        return os.environ.get(TOKEN_VAR, "").strip()


def lib_installed() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("discord") is not None
    except Exception:
        return False


# --------------------------------------------------------------- owner filter
def allowed(author_id, is_dm: bool, author_is_bot: bool = False, is_self: bool = False,
            owner: Optional[int] = None) -> bool:
    """Only HIM - in DMs and in his server channels (fix 20.1). Other people,
    bots and the bot itself -> no."""
    if is_self or author_is_bot:
        return False
    try:
        return int(author_id) == int(owner if owner is not None else owner_id())
    except Exception:
        return False


def strip_mention(text: str, bot_id=None) -> str:
    """'<@123> status' -> 'status' (server messages often start with @Kaka)."""
    t = str(text or "")
    t = re.sub(r"<@[!&]?\d+>", " ", t)
    return " ".join(t.split())


# --------------------------------------------------------------- the parser
def _norm(text: str) -> str:
    t = str(text or "").lower().strip()
    t = re.sub(r"[!?.,;:\"'`*_~]+", " ", t)
    return " ".join(t.split())


_MEDIA_WORDS = r"(gaana|gana|gaane|song|music|track|spotify|media|video)"


def parse(text: str):
    """Hinglish built-ins -> (cmd, args) or None (= send it to Gemini)."""
    t = _norm(text)
    if not t:
        return None
    words = t.split()
    if t in ("help", "commands", "madad", "kya kar sakte ho", "menu", "?"):
        return ("help", {})
    # power: lock / sleep / shutdown / restart
    if re.search(r"\b(lock)\b", t) and not re.search(r"\b(unlock|caps)\b", t):
        return ("lock", {})
    if re.search(r"\b(restart|reboot|restrat)\b", t):
        return ("restart", {})
    if re.search(r"\b(shutdown|shut down|power off|poweroff)\b", t) or \
            re.search(r"\b(pc|computer|laptop|system)\b.*\bband\s*(karo|kar do|kardo)\b", t):
        if "kaka" not in t:
            return ("shutdown", {})
    if re.search(r"\b(pc|computer|laptop|system)\b.*\b(sleep|so ja|so jao|sula do|sulao)\b", t) \
            or t in ("sleep", "pc sleep", "sleep karo"):
        return ("sleep", {})
    # status
    if t in ("status", "stats", "health", "pc status", "pc health", "haal", "pc ka haal") or \
            (len(words) <= 6 and re.search(r"\b(status|battery|ram|cpu|pc kaisa|pc ka haal|garam)\b", t)
             and not re.search(r"\b(message|msg|bhejo|call|whatsapp)\b", t)):
        return ("status", {})
    # screenshot
    if re.search(r"\b(screenshot|screen shot|ss|screen dikhao|screen bhejo|screen ki photo)\b", t):
        return ("screenshot", {})
    # webcam photo (update 21)
    if (re.search(r"\b(webcam|web cam|camera|cam|selfie)\b", t) or
            re.search(r"\b(photo|foto|pic|picture)\s*(lo|le|lena|khicho|kheecho|khinch|khincho|bhejo|bhej|dikhao|click)\b", t)
            or t in ("photo", "pic")) and not re.search(r"\b(band|off|stop|bandh|gesture)\b", t):
        return ("webcam", {})
    # recording
    if re.search(r"\b(recording|record)\b", t):
        if re.search(r"\b(band|stop|ruko|rok|bas|khatam|off)\b", t):
            return ("recording", {"action": "stop"})
        if re.search(r"\b(shuru|start|chalu|on|karo|kar do)\b", t):
            return ("recording", {"action": "start"})
    # volume
    m = re.search(r"\b(volume|vol|awaaz|awaz|aawaz|sound)\s*(ko\s*)?(\d{1,3})\b", t) or \
        re.search(r"\b(\d{1,3})\s*(%|percent)?\s*(pe\s*|par\s*)?(volume|awaaz|awaz)\b", t)
    if m:
        n = [g for g in m.groups() if g and g.isdigit()][0]
        return ("volume_set", {"level": max(0, min(100, int(n)))})
    if re.search(r"\b(unmute|un mute)\b", t):
        return ("unmute", {})
    if re.search(r"\b(mute)\b", t):
        return ("mute", {})
    if re.search(r"\b(volume|awaaz|awaz|aawaz|sound)\b", t):
        if re.search(r"\b(kam|kum|down|ghatao|dheere|dheema|low|neeche)\b", t):
            return ("volume_step", {"delta": -10})
        if re.search(r"\b(badhao|badha|zyada|jyada|up|tez|high|upar|full)\b", t):
            return ("volume_step", {"delta": 10})
    # media keys
    media = bool(re.search(r"\b" + _MEDIA_WORDS + r"\b", t))
    if len(words) <= 4 or media:
        if re.search(r"\b(next|agla|aagla|skip|agle)\b", t) and (media or len(words) <= 2):
            return ("media", {"key": "next"})
        if re.search(r"\b(previous|prev|pichla|pichhla|pichle|peeche|back)\b", t) and (media or len(words) <= 2):
            return ("media", {"key": "prev"})
        if re.search(r"\b(pause|play|resume|ruko|rok do|chalao|chala do|band karo)\b", t) and \
                (media or t in ("pause", "play", "resume", "play pause")):
            return ("media", {"key": "play_pause"})
    # reminders
    if re.search(r"\breminders?\b", t) and re.search(r"\b(batao|list|dikhao|kitne|kaun|sab)\b", t) \
            and not re.search(r"\b(baad|baje|kal|minute|min|ghante)\b", t):
        return ("reminder_list", {})
    if re.search(r"\b(reminder|yaad dila|yaad dilana|yaad dilao|alarm)\b", t):
        return ("reminder_add", {"body": t})
    # notes
    m = re.match(r"^(note karo|note kar lo|note kar|note likho|note|likh lo|likho)\s+(.+)$", t)
    if m and not re.match(r"^(padho|batao|dikhao|list)\b", m.group(2)):
        raw = re.sub(r"^\s*(note karo|note kar lo|note kar|note likho|note|likh lo|likho)\s*[:\-]?\s*",
                     "", str(text).strip(), flags=re.I)
        return ("note_add", {"body": raw.strip() or m.group(2)})
    if re.search(r"\bnotes?\b", t) and re.search(r"\b(padho|batao|dikhao|list|sab|saare)\b", t):
        return ("notes_list", {})
    return None


_TIME_BITS = [
    r"\b(reminder|reminders|yaad\s+dila(na|o|do|ye|na hai)?|yaad\s+karana|alarm(\s+laga(\s*do|o)?)?)\b",
    r"\b(\d+|ek|do|teen|char|paanch|dedh|dhai|aadha)\s*(minute|minutes|min|mins|ghante|ghanta|hour|hours|hr|sec|second|seconds)\s*(baad|mein|me)?\b",
    r"\b(subah|sham|shaam|raat|dopahar|dopehar|morning|evening|night)\b",
    r"\b\d{1,2}([:.]\d{2})?\s*(baje|bje|am|pm)\b",
    r"\b(aaj|kal|parso|parson)\b",
    r"\b(ko|pe|par|ki|ke)\s*$",
]


def reminder_message(text: str) -> str:
    t = _norm(text)
    for p in _TIME_BITS:
        t = re.sub(p, " ", t)
    t = " ".join(t.split())
    t = re.sub(r"^(ki|ke|ko|ka|ke liye|liye)\s+", "", t)
    return t or "Reminder"


# --------------------------------------------------------- confirm + replies
class Reply:
    def __init__(self, text: str = "", file: Optional[str] = None,
                 after: Optional[Callable[[], None]] = None, question: bool = False):
        self.text = text
        self.file = file
        self.after = after          # run AFTER the reply is sent (sleep/shutdown)
        self.question = question

    def __repr__(self):
        return f"Reply({self.text!r}, file={self.file!r})"


def _clean(result) -> str:
    s = str(result or "").strip()
    s = re.sub(r"\[(ASK_ONCE|KAKA_NOTICE|UNTRUSTED[^\]]*|/UNTRUSTED[^\]]*)\]", "", s)
    s = re.split(r"\s->\s|\s\u2192\s", s)[0] if (" -> " in s or " \u2192 " in s) else s
    s = re.sub(r"\(Say exactly this to him\.?\)", "", s)
    return s.strip()[:1900] or "Ho gaya."


def _ask_once_question(result: str) -> str:
    m = re.search(r'"([^"]{3,200})"', str(result))
    return m.group(1) if m else "Pakka karun?"


class Remote:
    """All command logic, no Discord in here (unit-testable).
    handle(text) is synchronous - the bot runs it in a worker thread."""

    def __init__(self, registry_getter: Optional[Callable] = None):
        self.pending: Optional[dict] = None
        self._registry_getter = registry_getter
        self.spoken: list = []

    # ---- plumbing
    def registry(self):
        if self._registry_getter is not None:
            return self._registry_getter()
        try:
            j = _ks().jarvis()
            return getattr(j, "_action_registry", None) if j is not None else None
        except Exception:
            return None

    def has_tool(self, name: str) -> bool:
        reg = self.registry()
        try:
            if reg is not None and reg.has(name):
                return True
        except Exception:
            pass
        return self._tool_module(name) is not None

    def _tool_module(self, name: str):
        """Fallback when no registry (Kaka not bound / tests): actions/<name>.py."""
        try:
            import importlib
            mod = importlib.import_module(f"actions.{name}")
            tool = getattr(mod, "TOOL", None)
            if isinstance(tool, dict) and tool.get("name") == name and callable(tool.get("handler")):
                return mod
        except Exception:
            return None
        return None

    def run_tool(self, name: str, args: dict) -> Optional[str]:
        """Run a Kaka tool by name. None when the tool does not exist."""
        said: list = []

        def _speak(text, *a, **k):
            said.append(str(text))
        ctx = {"player": None, "speak": _speak, "response": None, "session_memory": None}
        try:
            j = _ks().jarvis()
            ctx["player"] = getattr(j, "ui", None) if j is not None else None
        except Exception:
            pass
        reg = self.registry()
        try:
            if reg is not None and reg.has(name):
                r = reg.run(name, dict(args or {}), ctx)
                return str(r or ("; ".join(said) if said else "Done."))
        except Exception as e:
            return f"Tool '{name}' failed: {e}"
        mod = self._tool_module(name)
        if mod is None:
            return None
        try:
            from core import action_loader as AL
            r = AL._call_handler(mod.TOOL["handler"], dict(args or {}), ctx)
            return str(r or ("; ".join(said) if said else "Done."))
        except Exception as e:
            return f"Tool '{name}' failed: {e}"

    def announce(self, text: str, speak: Optional[str] = None) -> None:
        """Log + HUD popup for every command; one short spoken line only for
        actions that change something, never waking Kaka from sleep."""
        _log(f"SYS: Discord se command aaya: {str(text)[:90]}")
        _popup("Discord", f"Discord se command aaya: {str(text)[:200]}")
        if speak:
            self.spoken.append(speak)
            try:
                ks = _ks()
                if not ks._asleep():
                    ks.say_async(speak)
            except Exception:
                pass

    # ---- confirm flow
    def _ask(self, question: str, fn: Callable[[], Reply], label: str) -> Reply:
        self.pending = {"fn": fn, "label": label, "until": _now() + CONFIRM_SECONDS,
                        "question": question, "id": object()}
        return Reply(f"{question} (haan/na - 2 min)", question=True)

    def expire(self, pid=None) -> bool:
        """Bot timer: drop the question if it is still the same one."""
        p = self.pending
        if p is not None and (pid is None or p.get("id") is pid) and _now() >= p["until"]:
            self.pending = None
            return True
        return False

    def handle(self, text: str) -> Reply:
        t0 = time.monotonic()
        try:
            r = self._handle(text)
        except Exception as e:
            r = Reply(f"Kuch gadbad ho gayi: {type(e).__name__}: {str(e)[:150]}")
        try:
            _ks().timing("discord command", time.monotonic() - t0, _norm(text)[:30])
        except Exception:
            pass
        STATE["last_cmd"] = _norm(text)[:60]
        return r

    def _handle(self, text: str) -> Reply:
        t = _norm(text)
        prefix = ""
        if self.pending is not None:
            p, self.pending = self.pending, None
            if _now() >= p["until"]:
                prefix = "(Pichhla sawaal expire ho gaya tha, woh nahi kiya.)\n"
            elif t in YES_WORDS or t.startswith(("haan", "yes")):
                self.announce(f"{p['label']} (haan bola)")
                return p["fn"]()
            elif t in NO_WORDS or t.startswith(("nahi", "na ", "no ")):
                self.announce(f"{p['label']} - mana kiya")
                return Reply("Theek hai, nahi kiya.")
            else:
                prefix = f"(\"{p['label']}\" nahi kiya - haan nahi bola.)\n"
        r = self._dispatch(text)
        if prefix:
            r.text = prefix + (r.text or "")
        return r

    def _dispatch(self, text: str) -> Reply:
        cmd = parse(text)
        if cmd is None:
            return self.ai(text)
        name, a = cmd
        fn = getattr(self, "cmd_" + name, None)
        if fn is None:
            return self.ai(text)
        return fn(text, **a)

    # ---- built-ins
    def cmd_help(self, text):
        return Reply(HELP_TEXT)

    def cmd_lock(self, text):
        self.announce(text, speak=None)
        if os.name != "nt" and not _TEST.get("power"):
            return Reply("PC lock nahi ho paaya (Windows nahi mila).")
        return Reply("PC lock kar diya \U0001f512", after=self._power("lock"))

    def _power(self, kind: str) -> Callable[[], None]:
        def _go():
            if _TEST.get("power"):
                _TEST["power"](kind)
                return
            try:
                import subprocess
                if kind == "lock":
                    import ctypes
                    if not ctypes.windll.user32.LockWorkStation():
                        _log("ERR: PC lock nahi hua.")
                elif kind == "sleep":
                    import ctypes
                    ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
                elif kind == "shutdown":
                    subprocess.Popen(["shutdown", "/s", "/t", "10"], creationflags=0x08000000)
                elif kind == "restart":
                    subprocess.Popen(["shutdown", "/r", "/t", "10"], creationflags=0x08000000)
            except Exception as e:
                _log(f"ERR: {kind} nahi hua: {e}")
        return _go

    def cmd_sleep(self, text):
        def _do():
            self.announce("PC sleep", speak=None)
            return Reply("PC sleep mein ja raha hai. (Ab Discord se wapas nahi jagega - "
                         "PC ka button dabana padega.)", after=self._power("sleep"))
        return self._ask("PC sleep kar dun? Phir Discord se jagaa nahi paoge.", _do, "PC sleep")

    def cmd_shutdown(self, text):
        def _do():
            self.announce("PC shutdown", speak="Discord se shutdown aaya, 10 second mein PC band ho raha hai.")
            return Reply("PC 10 second mein band ho raha hai.", after=self._power("shutdown"))
        return self._ask("PC shutdown kar dun? Pakka?", _do, "PC shutdown")

    def cmd_restart(self, text):
        def _do():
            self.announce("PC restart", speak="Discord se restart aaya, 10 second mein PC restart ho raha hai.")
            return Reply("PC 10 second mein restart ho raha hai. Kaka khud wapas aa jayega.",
                         after=self._power("restart"))
        return self._ask("PC restart kar dun? Pakka?", _do, "PC restart")

    def cmd_status(self, text):
        self.announce(text)
        return Reply(status_text())

    def cmd_screenshot(self, text):
        self.announce(text)
        path, err = take_screenshot()
        if not path:
            return Reply(f"Screenshot nahi aaya - {err}")
        return Reply("Ye raha screen \U0001f4f8", file=path)

    def cmd_webcam(self, text):
        self.announce(text)
        try:
            from core import kaka_face as F
        except Exception as e:
            return Reply(f"Webcam photo feature PC pe nahi hai (Update-21 chahiye): {e}")
        path, err = F.webcam_photo()
        if not path:
            return Reply(f"Webcam photo nahi aayi - {err}")
        return Reply("Ye raha webcam \U0001f4f7", file=path)

    def _pc(self):
        from core import kaka_pc
        return kaka_pc

    def cmd_volume_set(self, text, level):
        self.announce(text, speak=f"Discord se volume {level} kiya.")
        try:
            r = self._pc().volume_set(level)
        except Exception as e:
            return Reply(f"Volume nahi badla: {e}")
        if not r.get("ok"):
            return Reply("Volume nahi badal paaya.")
        lv = r.get("level")
        return Reply(f"Volume {lv}% kar diya." + ("" if r.get("exact") else " (lagbhag)"))

    def cmd_volume_step(self, text, delta):
        self.announce(text, speak="Discord se awaaz " + ("badhayi." if delta > 0 else "kam ki."))
        try:
            r = self._pc().volume_step(delta)
        except Exception as e:
            return Reply(f"Volume nahi badla: {e}")
        if not r.get("ok"):
            return Reply("Volume nahi badal paaya.")
        lv = r.get("level")
        word = "badha di" if delta > 0 else "kam kar di"
        return Reply(f"Awaaz {word}" + (f", ab {lv}%." if lv is not None else "."))

    def cmd_mute(self, text):
        self.announce(text)
        try:
            r = self._pc().set_mute(True)
        except Exception as e:
            return Reply(f"Mute nahi hua: {e}")
        if not r.get("ok"):
            return Reply("Mute nahi ho paaya.")
        return Reply("Mute kar diya." if not r.get("toggled") else "Mute key daba di (toggle).")

    def cmd_unmute(self, text):
        self.announce(text)
        try:
            r = self._pc().set_mute(False)
        except Exception as e:
            return Reply(f"Unmute nahi hua: {e}")
        if not r.get("ok"):
            return Reply("Unmute nahi ho paaya.")
        return Reply("Unmute kar diya." if not r.get("toggled") else "Mute key daba di (toggle).")

    def cmd_media(self, text, key):
        label = {"play_pause": "pause/play", "next": "agla gaana", "prev": "pichhla gaana"}[key]
        self.announce(text)
        try:
            ok = self._pc().media_key(key)
        except Exception:
            ok = False
        return Reply(f"Gaana: {label} \u2714" if ok else "Media key nahi dab paayi.")

    def cmd_reminder_list(self, text):
        self.announce(text)
        r = self.run_tool("reminder", {"action": "list"})
        return Reply(_clean(r) if r is not None else "Reminder feature nahi mila.")

    def cmd_reminder_add(self, text, body=""):
        t = body or _norm(text)
        try:
            from core import kaka_reminders as R
            due = R.parse_when(t)
        except Exception:
            due = None
        if due is None:
            return self.ai(text)          # let Gemini read the time words
        msg = reminder_message(t)
        kind = "alarm" if "alarm" in t else "reminder"
        self.announce(text, speak=f"Discord se reminder set kiya: {msg}.")
        r = self.run_tool("reminder", {"action": "add", "when": t, "message": msg, "kind": kind})
        return Reply(_clean(r) if r is not None else "Reminder feature nahi mila.")

    def cmd_note_add(self, text, body=""):
        note = body or ""
        if not self.has_tool("notes"):
            return Reply("Notes feature abhi PC pe nahi hai (Update-19 chahiye).")
        self.announce(text, speak=f"Discord se note add kiya: {note[:60]}.")
        r = self.run_tool("notes", {"action": "add", "text": note})
        return Reply(_clean(r) if r is not None else "Notes feature nahi mila.")

    def cmd_notes_list(self, text):
        if not self.has_tool("notes"):
            return Reply("Notes feature abhi PC pe nahi hai (Update-19 chahiye).")
        self.announce(text)
        r = self.run_tool("notes", {"action": "list"})
        return Reply(_clean(r) if r is not None else "Notes feature nahi mila.")

    def cmd_recording(self, text, action):
        if not self.has_tool("screen_record"):
            return Reply("Screen recording feature abhi PC pe nahi hai.")
        self.announce(text, speak=("Discord se recording shuru ki." if action == "start"
                                   else "Discord se recording band ki."))
        r = self.run_tool("screen_record", {"action": action})
        return Reply(_clean(r) if r is not None else "Screen recording feature nahi mila.")

    # ---- free text -> Gemini picks a Kaka tool
    def declarations(self) -> list:
        reg = self.registry()
        decls = []
        try:
            if reg is not None:
                decls = list(reg.get_tool_declarations())
        except Exception:
            decls = []
        out = []
        for d in decls:
            if not isinstance(d, dict) or d.get("name") in SKIP_TOOLS:
                continue
            out.append({"name": d["name"], "description": str(d.get("description", ""))[:900],
                        "parameters": d.get("parameters") or {"type": "OBJECT", "properties": {}}})
        return out

    def needs_confirm(self, name: str, args: dict) -> bool:
        if name in CONFIRM_TOOLS:
            return True
        for k in ("action", "mode", "operation", "command", "op", "type"):
            v = str((args or {}).get(k) or "").lower()
            if v and any(w in re.split(r"[^a-z_]+", v) for w in DANGER_WORDS):
                return True
        return False

    def ai(self, text: str) -> Reply:
        self.announce(text)
        ok, kind, a, b = gemini_route(text, self.declarations())
        if not ok:
            return Reply(f"Samajh nahi paaya / jawab nahi mila: {a}")
        if kind == "text":
            return Reply(str(a)[:1900])
        name, args = a, dict(b or {})
        if not self.has_tool(name):
            return Reply(f"'{name}' tool PC pe nahi mila.")

        def _run(extra: Optional[dict] = None) -> Reply:
            r = self.run_tool(name, {**args, **(extra or {})})
            if r is None:
                return Reply(f"'{name}' tool PC pe nahi mila.")
            if "[ASK_ONCE]" in r:
                q = _ask_once_question(r)
                return self._ask(q, lambda: _run({"go_ahead": True}), f"{name}")
            return Reply(_clean(r))

        if self.needs_confirm(name, args):
            summ = ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items() if v not in (None, ""))[:200]
            return self._ask(f"Pakka? {name}({summ})", lambda: _run({"go_ahead": True}), name)
        return _run()


_TEST: dict = {}


# ---------------------------------------------------------------- PC status
def status_text() -> str:
    """U19 PC health if installed, else own ctypes battery + RAM + CPU."""
    try:
        from core import kaka_health as H
        rep = H.report("all")
        txt = str(rep.get("detail") or rep.get("text") or "").strip()
        if txt:
            return "PC status:\n" + txt[:1800]
    except Exception:
        pass
    lines = []
    b = _battery()
    if b is None:
        lines.append("Battery: pata nahi chala")
    elif b.get("no_battery"):
        lines.append("Battery: nahi hai (desktop / charger pe)")
    else:
        s = f"Battery: {b['percent']}%" + (" (charging)" if b.get("plugged") else " (charger nahi laga)")
        if b.get("secs_left") and not b.get("plugged"):
            m = int(b["secs_left"]) // 60
            s += f", ~{m // 60}h {m % 60}m bacha"
        lines.append(s)
    ram = _ram()
    if ram:
        used, total = ram
        lines.append(f"RAM: {used / 2**30:.1f} / {total / 2**30:.1f} GB use ho rahi ({100 * used // max(total, 1)}%)")
    cpu = _cpu_percent()
    if cpu is not None:
        lines.append(f"CPU: {cpu}%")
    lines.append("(Top apps ke liye Update-19 PC health chahiye)")
    return "PC status:\n" + "\n".join(lines)


def _battery() -> Optional[dict]:
    if _TEST.get("battery") is not None:
        return _TEST["battery"]
    try:
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        if s.BatteryFlag == 128 or s.BatteryLifePercent == 255:
            return {"no_battery": True}
        return {"percent": int(s.BatteryLifePercent), "plugged": s.ACLineStatus == 1,
                "secs_left": None if s.BatteryLifeTime == 0xFFFFFFFF else int(s.BatteryLifeTime)}
    except Exception:
        return None


def _ram():
    if _TEST.get("ram") is not None:
        return _TEST["ram"]
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return (int(m.ullTotalPhys - m.ullAvailPhys), int(m.ullTotalPhys))
    except Exception:
        return None


def _cpu_percent(window: float = 0.4):
    if _TEST.get("cpu") is not None:
        return _TEST["cpu"]
    try:
        import ctypes
        from ctypes import wintypes

        def _times():
            i, k, u = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
            f = lambda x: (x.dwHighDateTime << 32) | x.dwLowDateTime
            return f(i), f(k), f(u)
        i1, k1, u1 = _times()
        time.sleep(window)
        i2, k2, u2 = _times()
        busy = (k2 - k1) + (u2 - u1)
        return int(round(100 * (busy - (i2 - i1)) / busy)) if busy else None
    except Exception:
        return None


# --------------------------------------------------------------- screenshot
def take_screenshot():
    """-> (png path | None, error text). U19 kaka_shot if present, else the U18
    grabbers (mss / PIL / PowerShell) from core/kaka_screen."""
    if _TEST.get("shot") is not None:
        return _TEST["shot"]()
    try:
        from core import kaka_shot as S
        path, info = S.take("screen")
        if path:
            return _fit(path), ""
        err = (info or {}).get("error") or "pata nahi kyun"
    except ImportError:
        err = ""
    except Exception as e:
        err = str(e)[:120]
    try:
        from core import kaka_screen as KS
        rect = KS._virtual_screen()
        png, errs = None, []
        for name, fn in KS.GRABBERS:
            try:
                png = fn(rect)
                if png:
                    break
            except Exception as e:
                errs.append(f"{name}: {type(e).__name__}")
        if not png:
            return None, err or ("grabbers fail: " + "; ".join(errs))[:200]
        from datetime import datetime
        p = _ks().data_path("Screenshots", f"kaka-discord-{datetime.now():%Y%m%d-%H%M%S}.png")
        p.write_bytes(png)
        return _fit(str(p)), ""
    except Exception as e:
        return None, err or str(e)[:150]


def _fit(path: str) -> str:
    """Discord bots may upload up to 10 MB; a huge PNG becomes a JPEG copy."""
    try:
        if os.path.getsize(path) <= MAX_FILE:
            return path
        from PyQt6.QtGui import QImage
        img = QImage(path)
        out = os.path.splitext(path)[0] + "-discord.jpg"
        if not img.isNull() and img.save(out, "JPG", 85) and os.path.getsize(out) <= MAX_FILE:
            return out
    except Exception:
        pass
    return path


# ------------------------------------------------------- Gemini tool routing
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


_SYSTEM = ("Tum Kaka ho, {user} ka Windows PC assistant. Ye command usne apne phone se "
           "Discord DM mein bheja hai (woh PC ke saamne nahi hai). Agar kuch karna hai to "
           "EK sahi tool call karo, us tool ke rules ke hisaab se args bharo (uske "
           "Hinglish words jaise ke taise). Agar sirf sawaal hai to chhota Hinglish jawab do "
           "(max 4 vakya), koi tool mat chalao. Kabhi go_ahead/confirm_send khud true mat karo.")


def gemini_route(text: str, decls: list, timeout: float = 25.0):
    """-> (ok, "tool", name, args) | (ok, "text", answer, None) | (False, "", reason, None)."""
    try:
        from core import kaka_ai as A
    except Exception as e:
        return False, "", f"AI module load nahi hua ({e})", None
    key = A._key()
    if not key:
        return False, "", A._REASONS["nokey"], None
    body = {"contents": [{"role": "user", "parts": [{"text": str(text)}]}],
            "systemInstruction": {"parts": [{"text": _SYSTEM.replace("{user}", _kaka_user_name())}]},
            "generationConfig": {"temperature": 0.2}}
    if decls:
        body["tools"] = [{"functionDeclarations": decls}]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    deadline = time.monotonic() + timeout
    kinds = []
    for m in A._models():
        left = deadline - time.monotonic()
        if left < 1.5:
            kinds.append("timeout")
            break
        try:
            status, js = A._http(A._URL.format(m=m, k=key), body, min(left, 15.0))
        except Exception as e:
            kinds.append("timeout" if "timed out" in str(e).lower() else "offline")
            if kinds[-1] == "offline":
                break
            continue
        if status == 200 and isinstance(js, dict):
            try:
                parts = ((js.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            except Exception:
                parts = []
            for p in parts:
                fc = p.get("functionCall") if isinstance(p, dict) else None
                if fc and fc.get("name"):
                    A.last_model = m
                    return True, "tool", str(fc["name"]), dict(fc.get("args") or {})
            txt = A._answer_text(js)
            if txt:
                A.last_model = m
                return True, "text", txt, None
            kinds.append("empty")
            continue
        k = A._classify(status, js)
        kinds.append(k)
        A._cool(m, k)
        if k == "key":
            break
    for k in ("key", "offline", "quota", "timeout", "down", "empty"):
        if k in kinds:
            return False, "", A._REASONS[k], None
    return False, "", A._REASONS["other"], None


# ------------------------------------------------------------ the bot thread
def _classify_exc(e: BaseException) -> str:
    names = {c.__name__ for c in type(e).__mro__}
    if "LoginFailure" in names:
        return "bad_token"
    if "PrivilegedIntentsRequired" in names:
        return "intent"
    s = str(e)
    if "401" in s and "Unauthorized" in s:
        return "bad_token"
    return "net"


# ------------------------------------------------------------------ bot photo = Kaka logo
AVATAR_FILE = "kaka-logo-avatar.png"   # in app/config; set once, marker in Data
_AVATAR = {"tried": False}


def _avatar_marker():
    return _ks().data_path("discord-avatar.json")


async def _avatar_once(client) -> str:
    """Make the bot's Discord photo Kaka's logo, once. Returns what happened.
    Marker (bot id + logo hash) in Data so it never repeats; rate limit /
    network errors are silent and retried at most once per hour."""
    try:
        import hashlib
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "config" / AVATAR_FILE
        if not p.exists() or client.user is None:
            return "no_file"
        raw = p.read_bytes()
        h = hashlib.sha256(raw).hexdigest()[:16]
        bot = str(client.user.id)
        mk = _avatar_marker()
        got = _ks().read_json(mk, {})
        got = got if isinstance(got, dict) else {}
        if got.get("sha") == h and got.get("bot") == bot:
            return "done_before"
        if got.get("try_sha") == h and time.time() - float(got.get("try_at", 0) or 0) < 3600:
            return "wait"
        _ks().write_json(mk, dict(got, try_sha=h, try_at=time.time()))
        await client.user.edit(avatar=raw)
        _ks().write_json(mk, {"sha": h, "bot": bot, "at": time.time()})
        _log("SYS: Discord bot ki photo ab Kaka ka naya logo hai.")
        return "set"
    except Exception as e:
        try:
            print(f"[Discord] bot photo baad mein ({type(e).__name__})")
        except Exception:
            pass
        return "error"


def _avatar_start(client) -> None:
    if _AVATAR["tried"]:
        return
    _AVATAR["tried"] = True
    try:
        import asyncio
        asyncio.get_running_loop().create_task(_avatar_once(client))
    except Exception:
        pass


async def _session(tok: str, remote: Remote, stop: threading.Event, content_intent: bool = True):
    """One connected life of the bot. Returns 'stopped' | 'token_changed';
    raises on login/connection errors (the supervisor classifies them)."""
    import asyncio
    import discord
    intents = discord.Intents.none()
    intents.guilds = True
    intents.dm_messages = True
    intents.guild_messages = True
    intents.message_content = bool(content_intent)
    client = discord.Client(intents=intents)
    _THREAD["client"] = client
    _THREAD["loop"] = asyncio.get_running_loop()
    outcome = {"why": "stopped"}

    @client.event
    async def on_ready():
        _set("online", "", user=str(client.user), error="")
        _log(f"SYS: Discord remote online ({client.user}) - phone se DM karo.")
        _avatar_start(client)

    @client.event
    async def on_message(m):
        try:
            if not allowed(m.author.id, getattr(m, "guild", None) is None,
                           bool(getattr(m.author, "bot", False)),
                           client.user is not None and m.author.id == client.user.id):
                return
            await handle_message(remote, m)
        except Exception as e:
            _log(f"ERR: Discord message handle nahi hua: {e}")

    async def _watch():
        n = 0
        while not stop.is_set():
            await asyncio.sleep(1.0)
            n += 1
            if n % int(TOKEN_POLL) == 0:
                if token() != tok:
                    outcome["why"] = "token_changed"
                    break
                if not settings().get("enabled", True):
                    break
        await client.close()

    watcher = asyncio.create_task(_watch())
    try:
        await client.start(tok, reconnect=True)
    finally:
        watcher.cancel()
        try:
            if not client.is_closed():
                await client.close()
        except Exception:
            pass
        _THREAD["client"] = None
    return outcome["why"]


async def handle_message(remote: Remote, m) -> None:
    """Run the command off-loop, reply (chunks of 2000, + file), then the
    'after' action (sleep/shutdown) once the reply is out."""
    import asyncio
    loop = asyncio.get_running_loop()
    text = strip_mention(str(getattr(m, "content", "") or "")).strip()
    if not text:
        if getattr(m, "attachments", None):
            await m.channel.send("Abhi sirf text commands samajhta hoon.")
        return
    if chor_where_word(text):                       # fix 21.2: chor alarm -> server channel
        await m.channel.send(chor_target_cmd(m, chor_where_word(text)))
        return
    try:
        cm = m.channel.typing()
    except Exception:
        cm = None
    if cm is not None:
        async with cm:
            r = await loop.run_in_executor(None, remote.handle, text)
    else:
        r = await loop.run_in_executor(None, remote.handle, text)
    await send_reply(m.channel, r)
    if r.question and remote.pending is not None:
        pid = remote.pending.get("id")

        async def _expire():
            await asyncio.sleep(CONFIRM_SECONDS + 0.5)
            if remote.expire(pid):
                try:
                    await m.channel.send("2 minute ho gaye, haan nahi aaya - nahi kiya.")
                except Exception:
                    pass
        loop.create_task(_expire())
    if r.after is not None:
        await asyncio.sleep(0.5)
        await loop.run_in_executor(None, r.after)


async def send_reply(channel, r: Reply) -> None:
    text = (r.text or "").strip() or "Ho gaya."
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or ["Ho gaya."]
    for i, c in enumerate(chunks):
        last = i == len(chunks) - 1
        if last and r.file:
            try:
                f = _discord_file(r.file)
                await channel.send(c, file=f)
                continue
            except Exception as e:
                c = c + f"\n(File nahi bhej paaya: {str(e)[:120]})"
        await channel.send(c)


# ------------------------------------------------- chor alarm -> server channel (fix 21.2)
_CHOR = r"(chor|chorr|chore|chhor|thief|intruder)\s*(alarm|alert|aleart|alarm alert)s?"
_CHOR_HEAD = r"^(kaka\s+)?(" + _CHOR + r"|alerts?|aleart)\s+"
_CHOR_HERE = _CHOR_HEAD + r"(yahan|yaha|yahaan|yahan pe|yahan par|idhar|here|is channel( me| mein| pe)?|this channel|channel( me| mein)?|set)(\s+(bhejo|aaye|aayenge|chahiye|karo))?$"
_CHOR_DM = _CHOR_HEAD + r"(dm|dm me|dm mein|dm pe|dm par|direct|private)(\s+(bhejo|aaye|aayenge|chahiye|karo))?$"
_CHOR_WHERE = _CHOR_HEAD + r"(kahan|kaha|kidhar|where)(\s+(jaata|jata|aata|ata|jaate|aate|jayega|aayega)(\s+hai)?)?$"


def chor_where_word(text: str) -> str:
    """'chor alarm yahan' -> 'here', 'chor alarm dm' -> 'dm', 'chor alarm kahan' -> 'where', else ''."""
    t = _norm(strip_mention(text))
    if not t:
        return ""
    if re.match(_CHOR_HERE, t):
        return "here"
    if re.match(_CHOR_DM, t):
        return "dm"
    if re.match(_CHOR_WHERE, t):
        return "where"
    return ""


def alarm_channel_id() -> int:
    try:
        v = settings().get("alarm_channel_id")
        return int(str(v).strip()) if v not in (None, "", 0, "0") else 0
    except Exception:
        return 0


def chor_target_text() -> str:
    cid = alarm_channel_id()
    if not cid:
        return "Chor alarm alerts abhi tumhare DM mein aate hain. Server channel mein 'chor alarm yahan' likho to wahan aayenge."
    s = settings()
    name = s.get("alarm_channel_name") or str(cid)
    g = s.get("alarm_guild_name") or ""
    return (f"Chor alarm alerts #{name}" + (f" ({g})" if g else "") + " channel mein aate hain. "
            "Channel mein na ja paaye to DM mein aayenge. Wapas DM ke liye: chor alarm dm")


def chor_target_cmd(m, word: str) -> str:
    """Owner-only (on_message already filters; checked again here)."""
    try:
        if int(m.author.id) != int(owner_id()) or bool(getattr(m.author, "bot", False)):
            return ""
    except Exception:
        return ""
    if word == "where":
        return chor_target_text()
    guild = getattr(m, "guild", None)
    if word == "dm" or guild is None:
        save_settings(alarm_channel_id="", alarm_channel_name="", alarm_guild_name="")
        _log("SYS: Chor alarm alerts ab Discord DM mein jayenge.")
        if word == "here":
            return "OK, chor alarm alerts ab is DM mein aayenge. Server channel chahiye to us channel mein 'chor alarm yahan' likho."
        return "OK, chor alarm alerts ab tumhare DM mein aayenge."
    ch = m.channel
    name = str(getattr(ch, "name", "") or "")
    gname = str(getattr(guild, "name", "") or "")
    save_settings(alarm_channel_id=str(ch.id), alarm_channel_name=name, alarm_guild_name=gname)
    _log(f"SYS: Chor alarm alerts ab Discord server channel #{name} mein jayenge.")
    msg = "OK, chor alarm alerts ab is channel mein aayenge."
    try:
        me = getattr(guild, "me", None)
        perms = ch.permissions_for(me) if me is not None else None
        if perms is not None and not getattr(perms, "attach_files", True):
            msg += (" Dhyan do: is channel mein mujhe photo bhejne ki permission (Attach Files) nahi hai - "
                    "server settings mein Kaka role ko Attach Files do, warna photo DM mein aayegi.")
    except Exception:
        pass
    return msg + " Wapas DM ke liye: chor alarm dm"


async def _send_strict(channel, text: str, file: Optional[str]) -> None:
    """Like send_reply but a failed photo is an error (the caller then uses the DM)."""
    text = (text or "").strip() or "Ho gaya."
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or ["Ho gaya."]
    for i, c in enumerate(chunks):
        if i == len(chunks) - 1 and file:
            await channel.send(c, file=_discord_file(file))
        else:
            await channel.send(c)


def _why(e: BaseException) -> str:
    s = f"{type(e).__name__}: {e}"
    if "50013" in s or "Missing Permissions" in s or "Forbidden" in type(e).__name__:
        return "permission nahi hai"
    if "10003" in s or "Unknown Channel" in s or "NotFound" in type(e).__name__:
        return "channel mila nahi (delete ho gaya?)"
    return s[:100]


def send_owner(text: str, file: Optional[str] = None, timeout: float = 45.0) -> bool:
    """Chor alarm delivery from any thread. Fix 21.2: to the server channel he
    chose with 'chor alarm yahan'; no channel / channel fails -> his DM, so an
    alert is never lost. False when the bot is not online (the caller queues it)."""
    if _TEST.get("send_owner") is not None:
        return bool(_TEST["send_owner"](text, file))
    client, loop = _THREAD.get("client"), _THREAD.get("loop")
    if client is None or loop is None or STATE.get("status") != "online":
        return False
    import asyncio

    async def _go():
        note = ""
        cid = alarm_channel_id()
        if cid:
            try:
                ch = client.get_channel(cid) or await client.fetch_channel(cid)
                await _send_strict(ch, text, file)
                return
            except Exception as e:
                why = _why(e)
                _log(f"ERR: Chor alarm server channel mein nahi gaya ({why}) - DM mein bheja.")
                note = (f"\n(Server channel mein nahi gaya: {why}. Isliye DM mein bheja. "
                        "Channel theek karke wahan 'chor alarm yahan' likho.)")
        oid = owner_id()
        u = client.get_user(oid) or await client.fetch_user(oid)
        ch = u.dm_channel or await u.create_dm()
        await send_reply(ch, Reply(text + note, file=file))
    try:
        asyncio.run_coroutine_threadsafe(_go(), loop).result(timeout)
        return True
    except Exception as e:
        _log(f"ERR: Discord DM nahi gaya: {str(e)[:120]}")
        return False


def _discord_file(path: str):
    if _TEST.get("file") is not None:
        return _TEST["file"](path)
    import discord
    return discord.File(path, filename=os.path.basename(path))


def _run_once(tok: str, remote: Remote, stop: threading.Event, content_intent: bool) -> str:
    import asyncio
    return asyncio.run(_session(tok, remote, stop, content_intent))


def supervise(stop: threading.Event, remote: Optional[Remote] = None, max_loops: int = 0) -> None:
    """The bot thread: wait for a token, connect, reconnect with backoff,
    park on a wrong token until it changes. Never raises."""
    remote = remote or Remote()
    delay = BACKOFF_START
    bad_token = ""
    content_intent = True
    loops = 0
    told_no_token = False
    while not stop.is_set():
        loops += 1
        if max_loops and loops > max_loops:
            return
        if not settings().get("enabled", True):
            _set("off", "Discord remote band hai ('discord remote on' bolo)")
            _sleep(TOKEN_POLL)
            continue
        tok = token()
        if not tok:
            _set("no_token", "Discord token nahi hai - API KEYS mein DISCORD BOT TOKEN daalo")
            if not told_no_token:
                _log("SYS: Discord token nahi hai - API KEYS mein daalo (DISCORD BOT TOKEN).")
                told_no_token = True
            _sleep(TOKEN_POLL)
            continue
        told_no_token = False
        if tok == bad_token:
            _sleep(TOKEN_POLL)
            continue
        if not lib_installed() and not _TEST.get("run_once"):
            _set("no_lib", "discord.py install nahi hai - Kaka-Update-20 dobara chalao")
            _log("SYS: Discord remote: discord.py install nahi hai - Kaka-Update-20.bat dobara chalao.")
            _sleep(60.0)
            continue
        _set("connecting", "Discord se jud raha hoon")
        t_start = _now()
        try:
            runner = _TEST.get("run_once") or _run_once
            why = runner(tok, remote, stop, content_intent)
            if stop.is_set():
                break
            if why == "token_changed":
                delay = BACKOFF_START
                continue
            # closed without error (disabled / Discord ended it): short pause
        except BaseException as e:            # noqa: BLE001 - never crash Kaka
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                break
            kind = _classify_exc(e)
            if kind == "bad_token":
                bad_token = tok
                _set("bad_token", "Discord token galat hai - API KEYS mein naya token daalo",
                     error=type(e).__name__)
                _log("ERR: Discord token galat hai - API KEYS mein naya token daalo.")
                _popup("Discord", "Discord token galat hai - API KEYS mein naya DISCORD BOT TOKEN daalo.")
                continue
            if kind == "intent" and content_intent:
                content_intent = False   # DMs to a bot carry their text anyway
                _log("SYS: Discord: Message Content Intent band hai - portal mein ON kar do "
                     "(abhi DMs bina uske chal rahe hain).")
                continue
            _set("offline", f"Discord tak nahi pahunch paaya, {int(delay)} s mein dobara",
                 error=f"{type(e).__name__}: {str(e)[:80]}")
            _log(f"SYS: Discord remote offline ({type(e).__name__}) - {int(delay)} s mein dobara.")
        if _now() - t_start > 60:
            delay = BACKOFF_START
        _sleep(delay)
        delay = min(BACKOFF_CAP, delay * 2)
    _set("stopped", "")


def start() -> bool:
    """Start the bot thread (idempotent). Cheap: imports nothing heavy."""
    with _LOCK:
        th = _THREAD.get("t")
        if th is not None and th.is_alive():
            return False
        stop = threading.Event()
        _THREAD["stop"] = stop
        th = threading.Thread(target=supervise, args=(stop,), name="kaka-u20-discord", daemon=True)
        _THREAD["t"] = th
    th.start()
    try:
        import atexit
        atexit.register(stop_bot)
    except Exception:
        pass
    return True


def stop_bot(timeout: float = 3.0) -> None:
    stop = _THREAD.get("stop")
    if stop is not None:
        stop.set()
    client, loop = _THREAD.get("client"), _THREAD.get("loop")
    if client is not None and loop is not None:
        try:
            import asyncio
            fut = asyncio.run_coroutine_threadsafe(client.close(), loop)
            fut.result(timeout=timeout)
        except Exception:
            pass
    th = _THREAD.get("t")
    if th is not None and th is not threading.current_thread():
        th.join(timeout=timeout)


def running() -> bool:
    th = _THREAD.get("t")
    return th is not None and th.is_alive()


def status_line() -> str:
    s = STATE.get("status")
    if s == "online":
        return f"Discord remote online hai ({STATE.get('user') or 'bot'}) - phone se DM karo."
    if s in ("no_token", "bad_token", "no_lib", "off", "offline", "connecting"):
        return STATE.get("detail") or s
    if not settings().get("enabled", True):
        return "Discord remote band hai ('discord remote on' bolo)."
    if not token():
        return "Discord token nahi hai - API KEYS mein DISCORD BOT TOKEN daalo."
    return "Discord remote shuru ho raha hai."


def _auto_start() -> None:
    """ks.on_start hook (2 s after Kaka opens, background thread)."""
    if settings().get("enabled", True):
        start()


try:
    from core import kaka_services as _ks_mod
    _ks_mod.on_start(_auto_start)
except Exception:
    pass


# ------------------------------------------------------------ installer side
def selfcheck() -> tuple:
    """(ready, reason) for the installer. Fast, read-only."""
    try:
        if not lib_installed():
            return False, "discord.py install nahi hua (internet / pip)"
        tok = token()
        if not tok:
            return True, "ready - ab API KEYS mein DISCORD BOT TOKEN daalo (steps neeche)"
        return True, "ready - token mila, Kaka khulte hi bot online"
    except Exception as e:
        return False, f"check fail: {type(e).__name__}"


def install(timeout: float = 240.0) -> tuple:
    """pip install discord.py into THIS python (the Kaka venv). (ok, line)."""
    if lib_installed():
        try:
            from importlib.metadata import version
            return True, f"discord.py already installed ({version('discord.py')})"
        except Exception:
            return True, "discord.py already installed"
    import subprocess
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", "--prefer-binary", "--timeout", "30", PIP_SPEC]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return False, f"pip timed out after {int(timeout)} s"
    except Exception as e:
        return False, f"pip could not run: {type(e).__name__}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        return False, f"pip failed: {tail[0][:160]}"
    import importlib
    importlib.invalidate_caches()
    if not lib_installed():
        return False, "pip ran but discord is not importable"
    return True, f"installed {PIP_SPEC}"


def _main(argv) -> int:
    if "--install" in argv:
        ok, line = install()
        print(("DISCORD-INSTALL OK: " if ok else "DISCORD-INSTALL SKIPPED: ") + line)
        return 0 if ok else 1
    if "--steps" in argv:
        print(SETUP_STEPS)
        return 0
    ok, why = selfcheck()
    print(("ready" if ok else "skipped") + f" ({why})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
