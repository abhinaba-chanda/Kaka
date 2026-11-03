"""KAKA v1.1: Kaka operates the PC by itself - "YouTube pe lofi laga do",
"WhatsApp Web khol ke Rahul ko msg likh do", "Notepad khol ke shopping list
likh do".

How: a screenshot of the main screen (downscaled to <= 1600 px) goes to Gemini
vision (his existing key, core/kaka_ai.py) with the goal and the steps so far;
Gemini answers ONE next step as JSON (click x,y / type / press / hotkey /
scroll / open_url / open_app / wait / done). Coordinates are scaled back to
real screen pixels (the process is DPI-aware, so 1920x1080 at 125% works),
pyautogui does it, a new screenshot is taken, repeat.

Safety (his rules):
  * at most MAX_STEPS steps (15), then it stops and says how far it got
  * Esc key, "ruk jao Kaka" (pc_agent action=stop) or moving the mouse into a
    screen corner (pyautogui FAILSAFE) stops it at once
  * before any step that SENDS, DELETES, BUYS, POSTS or INSTALLS (clicking
    Send / Delete / Buy / Pay / Post / Install, or Enter in a chat box) it
    PAUSES and asks - spoken + CONFIRM / CANCEL on the HUD; it continues only
    after his yes ("haan" -> pc_agent action=confirm, or the CONFIRM button)
  * every step is shown on the HUD log
Screenshots stay in memory only. Nothing here raises out of run().
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
from typing import Callable, Optional

MAX_STEPS = 15
MAX_SIDE = 1600
CONFIRM_WAIT_S = 90.0
RISKY_WORDS = ("send", "bhej", "delete", "remove", "hatao", "buy", "purchase", "order",
               "pay", "checkout", "place order", "post", "publish", "tweet", "upload",
               "install", "uninstall", "submit", "share", "reply", "comment")
CHAT_WORDS = ("whatsapp", "message", "msg", "chat", "telegram", "discord", "messenger",
              "instagram", "mail", "email", "comment", "post", "tweet", "reply", "bhej")

SYSTEM = (
    "You control a Windows PC for the user, one step at a time, by looking at "
    "screenshots. The screenshot is {w}x{h} pixels; give x,y in THOSE pixels. "
    "Ignore the assistant's own window named KAKA (a dark HUD) - never click it. "
    "Answer with ONLY one JSON object, no markdown:\n"
    '{{"say": "<3-8 word Hinglish step for the HUD>", "action": "<click|double_click|'
    'right_click|type|press|hotkey|scroll|open_url|open_app|wait|done|fail>", '
    '"x": int, "y": int, "text": "<text to type, url, or app name>", '
    '"keys": ["ctrl","l"], "amount": int, "risky": bool, "target": "<what you click>"}}\n'
    "Rules: type only into a field you clicked first. To open a website use "
    "open_url. To open an installed app use open_app. press = one key (enter, tab, "
    "esc, down...). scroll amount: negative = down. risky=true for ANY step that "
    "sends a message/email, posts, deletes, buys/pays, installs or submits a form "
    "(e.g. clicking Send, pressing Enter in a chat box). When the goal is "
    "complete (or only the risky final step is left and was done) answer "
    'action=done with a short "say". If it is impossible, action=fail with the '
    "reason in say. The user asked to TYPE a message but not to send it -> stop "
    "with done after typing."
)


class Stopped(Exception):
    pass


def _ks():
    try:
        from core import kaka_services as ks
        return ks
    except Exception:
        return None


def set_dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def grab_primary():
    """-> (PIL.Image of the primary screen in real pixels, (left, top))."""
    from PIL import Image
    try:
        import mss
        with mss.mss() as s:
            mon = s.monitors[1]
            raw = s.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return img, (mon["left"], mon["top"])
    except Exception:
        from PIL import ImageGrab
        return ImageGrab.grab(), (0, 0)


def downscale(img, max_side: int = MAX_SIDE):
    """-> (jpeg bytes, scale real/shot, (w, h) of the shot)."""
    w, h = img.size
    scale = max(w, h) / float(max_side) if max(w, h) > max_side else 1.0
    if scale > 1.0:
        from PIL import Image
        img = img.resize((int(round(w / scale)), int(round(h / scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue(), scale, img.size


def parse_step(txt: str) -> Optional[dict]:
    if not txt:
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def is_risky(step: dict, goal: str) -> bool:
    """Model flag OR our own check - never trust the model alone."""
    if step.get("risky"):
        return True
    a = str(step.get("action") or "").lower()
    tgt = (str(step.get("target") or "") + " " + str(step.get("say") or "")).lower()
    g = (goal or "").lower()
    if a in ("click", "double_click", "right_click"):
        return any(re.search(r"\b" + re.escape(w) + r"\b", tgt) for w in RISKY_WORDS)
    if a == "press" and str(step.get("text") or "").lower() in ("enter", "return"):
        if any(re.search(r"\b" + re.escape(w) + r"\b", tgt) for w in RISKY_WORDS):
            return True
        if re.search(r"\b(search|dhundo|address bar|url)\b", tgt):
            return False                   # Enter in a search box opens a result, sends nothing
        return any(w in g for w in CHAT_WORDS)
    if a == "hotkey":
        keys = [str(k).lower() for k in (step.get("keys") or [])]
        if "enter" in keys or "return" in keys:
            return True
        if "delete" in keys or ("shift" in keys and "del" in keys):
            return True
    if a == "press" and str(step.get("text") or "").lower() in ("delete", "del"):
        return True
    return False


class Actuator:
    """Real mouse/keyboard (pyautogui). Tests pass a fake."""

    def __init__(self):
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        self.pg = pyautogui

    def click(self, x, y, kind="click"):
        if kind == "double_click":
            self.pg.doubleClick(x, y)
        elif kind == "right_click":
            self.pg.rightClick(x, y)
        else:
            self.pg.click(x, y)

    def type(self, text):
        try:                          # paste keeps Hinglish / emoji / unicode intact
            import pyperclip
            pyperclip.copy(text)
            self.pg.hotkey("ctrl", "v")
        except Exception:
            self.pg.write(text, interval=0.02)

    def press(self, key):
        self.pg.press(key)

    def hotkey(self, keys):
        self.pg.hotkey(*keys)

    def scroll(self, amount, x=None, y=None):
        if x is not None and y is not None:
            self.pg.moveTo(x, y)
        self.pg.scroll(int(amount) * 120)

    def open_url(self, url):
        import webbrowser
        if not re.match(r"^[a-z]+://", url):
            url = "https://" + url
        webbrowser.open(url)

    def open_app(self, name):
        try:
            from actions.open_app import open_app
            open_app({"app_name": name})
            return
        except Exception:
            pass
        self.pg.press("win")
        time.sleep(0.6)
        self.type(name)
        time.sleep(0.8)
        self.pg.press("enter")


class Agent:
    def __init__(self, goal: str, vision: Optional[Callable] = None,
                 grab: Optional[Callable] = None, actuator=None,
                 ask: Optional[Callable[[str], None]] = None,
                 log: Optional[Callable[[str], None]] = None,
                 max_steps: int = MAX_STEPS, settle: float = 1.2,
                 esc_pressed: Optional[Callable[[], bool]] = None):
        self.goal = goal.strip()
        self.vision = vision or self._gemini_vision
        self.grab = grab or grab_primary
        self.act = actuator
        self.ask = ask
        self.log = log or self._hud
        self.max_steps = max_steps
        self.settle = settle
        self.esc_pressed = esc_pressed or _esc_down
        self.stop_flag = threading.Event()
        self.answer = threading.Event()
        self.approved = False
        self.waiting_confirm = ""
        self.history: list = []
        self.result = ""

    # -- hooks
    @staticmethod
    def _gemini_vision(img_bytes, prompt, system):
        from core import kaka_ai
        return kaka_ai.vision(img_bytes, prompt, timeout=25.0, system=system)

    @staticmethod
    def _hud(msg):
        ks = _ks()
        if ks:
            ks.log(msg)
        else:
            print(msg)

    # -- control from outside (tool / HUD / Esc)
    def stop(self):
        self.stop_flag.set()
        self.answer.set()

    def confirm(self, yes: bool):
        self.approved = bool(yes)
        self.answer.set()

    def _check(self):
        if self.stop_flag.is_set():
            raise Stopped("ruk gaya")
        try:
            if self.esc_pressed():
                self.stop_flag.set()
                raise Stopped("Esc dabaya")
        except Stopped:
            raise
        except Exception:
            pass

    def _wait(self, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self._check()
            time.sleep(0.05)

    def _ask_confirm(self, step) -> bool:
        what = step.get("target") or step.get("say") or step.get("action")
        q = f"'{what}' - ye karun? (goal: {self.goal})"
        self.waiting_confirm = q
        self.approved = False
        self.answer.clear()
        self.log(f"SYS: PC control ruka - pehle poochna hai: {what}")
        if self.ask:
            try:
                self.ask(q)
            except Exception:
                pass
        end = time.monotonic() + CONFIRM_WAIT_S
        while time.monotonic() < end and not self.answer.is_set():
            self._check()
            time.sleep(0.1)
        self.waiting_confirm = ""
        self._check()
        return self.approved

    def _do(self, step, scale, offset):
        a = str(step.get("action") or "").lower()

        def real(v, off):
            return int(round(float(v) * scale)) + off
        if a in ("click", "double_click", "right_click"):
            self.act.click(real(step["x"], offset[0]), real(step["y"], offset[1]), a)
        elif a == "type":
            self.act.type(str(step.get("text") or ""))
        elif a == "press":
            self.act.press(str(step.get("text") or step.get("key") or "enter").lower())
        elif a == "hotkey":
            self.act.hotkey([str(k).lower() for k in (step.get("keys") or [])][:4])
        elif a == "scroll":
            x = step.get("x")
            y = step.get("y")
            self.act.scroll(int(step.get("amount") or -3),
                            real(x, offset[0]) if x is not None else None,
                            real(y, offset[1]) if y is not None else None)
        elif a == "open_url":
            self.act.open_url(str(step.get("text") or ""))
        elif a == "open_app":
            self.act.open_app(str(step.get("text") or ""))
        elif a == "wait":
            pass
        else:
            raise ValueError(f"unknown action {a!r}")

    def run(self) -> str:
        try:
            if self.act is None:
                self.act = Actuator()
            set_dpi_aware()
            self.log(f"SYS: PC control shuru: {self.goal} (Esc = ruko)")
            for i in range(1, self.max_steps + 1):
                self._check()
                img, offset = self.grab()
                shot, scale, (w, h) = downscale(img)
                del img
                hist = "\n".join(f"{n}. {s}" for n, s in enumerate(self.history, 1)) or "(none yet)"
                prompt = (f"GOAL: {self.goal}\nSteps done so far:\n{hist}\n"
                          f"Step {i} of max {self.max_steps}. What is the ONE next step?")
                ok, txt = self.vision(shot, prompt, SYSTEM.format(w=w, h=h))
                del shot
                self._check()
                step = parse_step(txt) if ok else None
                if not step:
                    self.result = f"Screen samajh nahi paya ({txt if not ok else 'jawab galat'})."
                    self.log("SYS: PC control: " + self.result)
                    return self.result
                a = str(step.get("action") or "").lower()
                say = str(step.get("say") or a)[:80]
                if a == "done":
                    self.result = f"Ho gaya: {say}"
                    self.log(f"SYS: PC control step {i}: done - {say}")
                    return self.result
                if a == "fail":
                    self.result = f"Nahi ho paya: {say}"
                    self.log(f"SYS: PC control step {i}: {self.result}")
                    return self.result
                if is_risky(step, self.goal):
                    if not self._ask_confirm(step):
                        self.result = f"Ruk gaya - '{step.get('target') or say}' nahi kiya (aapne haan nahi bola)."
                        self.log("SYS: PC control: " + self.result)
                        return self.result
                    self.log(f"SYS: PC control: haan mila, kar raha hoon - {say}")
                self.log(f"SYS: PC control step {i}/{self.max_steps}: {say}")
                self._do(step, scale, offset)
                desc = f"{a}" + (f" '{step.get('text')}'" if step.get("text") else "") + \
                       (f" on {step.get('target')}" if step.get("target") else "")
                self.history.append(desc[:160])
                self._wait(self.settle if a not in ("open_url", "open_app", "wait") else 3.0)
            self.result = f"{self.max_steps} steps ho gaye, kaam poora nahi hua - yahin ruk gaya."
            self.log("SYS: PC control: " + self.result)
            return self.result
        except Stopped as e:
            self.result = f"Ruk gaya ({e})."
            self.log("SYS: PC control: " + self.result)
            return self.result
        except Exception as e:
            name = type(e).__name__
            if name == "FailSafeException":
                self.result = "Ruk gaya (mouse corner mein le gaye - failsafe)."
            else:
                self.result = f"PC control mein dikkat: {name}: {str(e)[:100]}"
            self.log("SYS: PC control: " + self.result)
            return self.result


def _esc_down() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
    except Exception:
        return False


# ---------------------------------------------------------------- one at a time
_CUR = {"agent": None, "thread": None}


def clear_banner() -> None:
    """Hide our CONFIRM / CANCEL banner (answered by voice, or the run ended)."""
    try:
        from core import confirm as C
        with C._lock:
            if C._pending is None or not str(C._pending.title).startswith("PC control"):
                return
            C._pending = None
        if C._hide_cb:
            C._hide_cb()
    except Exception:
        pass


def current() -> Optional[Agent]:
    a, t = _CUR["agent"], _CUR["thread"]
    return a if (a is not None and t is not None and t.is_alive()) else None


def start(goal: str, done_cb: Optional[Callable[[str], None]] = None, **kw) -> str:
    if current() is not None:
        return "busy"

    def _ask(q):
        ks = _ks()
        try:
            from core import confirm
            confirm.request("pc-agent", "PC control: ye karun?", q,
                            lambda: (_CUR["agent"] and _CUR["agent"].confirm(True)) or "ok")
        except Exception:
            pass
        if ks:
            ks.say_async(f"Ek baar poocho: {q} Haan bole toh pc_agent action=confirm, "
                         f"nahi bole toh action=cancel.")
    kw.setdefault("ask", _ask)
    agent = Agent(goal, **kw)

    def _run():
        res = agent.run()
        clear_banner()
        if done_cb:
            try:
                done_cb(res)
            except Exception:
                pass
    t = threading.Thread(target=_run, name="kaka-pc-agent", daemon=True)
    _CUR["agent"], _CUR["thread"] = agent, t
    t.start()
    return "started"
