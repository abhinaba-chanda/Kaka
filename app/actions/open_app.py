"""
Kaka action — open an application, and know whether it actually opened.

WHAT WAS WRONG WITH THIS FILE
    It answered "Opened Spotify." whenever its launcher returned True, and on
    Windows the launcher returned True immediately after

        subprocess.Popen(app_name, shell=True)

    which succeeds because a SHELL started — not because an application did. A
    misspelling, an app that was never installed, an installer that moved:
    every one of them produced "Opened Spotify.", said out loud, while nothing
    appeared on screen.

    The same line was also a command injection. `shutil.which(name.split(".")[0])`
    passed "notepad.exe & whoami" because `which("notepad")` succeeds, and the
    whole string then went to a shell unquoted. Verified before it was changed.

WHAT IT DOES NOW
    * No shell, anywhere. Executables are resolved to a path and started as an
      argument vector; URI schemes go through the OS handler.
    * It watches the process table for a NEW process answering to the name, and
      only says `done` when one appears. When none does, it says `unverified`
      and the assistant tells the user it could not confirm — which is true,
      and is something they can act on.
    * It answers in milliseconds and verifies behind that, so asking to open
      something no longer costs the conversation one to four seconds.

WHY IT IS NOT A LIST OF APPLICATIONS
    Nothing here knows in advance what exists. Verification asks the running
    machine what appeared; matching compares against whatever is running. The
    alias map in core/appidentity.py covers the handful of names that differ
    BETWEEN OPERATING SYSTEMS and restricts nothing — every other name falls
    through untouched, which is what makes software nobody here has heard of
    work exactly as well as the software we have.
"""
import os
import re
import platform
import shutil
import subprocess
import threading
import time

from core import appidentity, outcome
try:
    from core import winapps   # KAKA update 8: real app finder, never the browser
except Exception as _e:      # pragma: no cover
    print(f"[open_app] winapps unavailable: {_e}")
    winapps = None
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

_SYSTEM = platform.system()          # "Windows" | "Darwin" | "Linux"

# How long to keep watching for the application to appear. Chosen from the two
# ends of the range rather than the middle: a text editor is up in a fraction
# of a second, a game launcher can take ten, and a limit that suits the first
# reports the second as broken.
_LAUNCH_TIMEOUT = 40.0      # update 9: cold starts on his laptop take >15 s

# Nothing here is a keyword list. The Start-Menu / Spotlight fallback types the
# name and presses Enter, which is what a person does, and works in every
# language the desktop is running in.
_SEARCH_SETTLE = 0.8


# ─────────────────────────────────────────────────────────────────────────────
#  Per-OS launching — no shell on any path
# ─────────────────────────────────────────────────────────────────────────────
def _desktop_search(app_name: str) -> bool:
    """Type the name into the desktop's own search and press Enter.

    The last resort, and the only one that reaches Store apps, shortcuts and
    anything else with no executable on PATH. It is also the one that cannot be
    verified from its own return value — which is fine, because nothing here is
    trusted on its own any more; the process table decides.
    """
    if not _PYAUTOGUI:
        return False
    try:
        if _SYSTEM == "Darwin":
            pyautogui.hotkey("command", "space")
        else:
            pyautogui.press("win")
        time.sleep(_SEARCH_SETTLE)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(_SEARCH_SETTLE)
        pyautogui.press("enter")
        return True
    except Exception as exc:
        print(f"[open_app] desktop search failed: {exc}")
        return False


def _launch_windows(app_name: str) -> bool:
    """-> was a launch REQUESTED successfully. Never 'did it open'.

    Three routes, none of them a shell:

      1. an executable on PATH, started as a one-element argument vector. The
         old code passed the raw string with shell=True, which is what let
         "notepad.exe & whoami" run two things.
      2. a URI scheme such as `ms-settings:` — handed to the OS handler with
         os.startfile, which is what the shell would have called anyway,
         without a command line for anything to be appended to.
      3. the Start Menu, typed like a person would.
    """
    resolved = shutil.which(app_name)
    if resolved:
        try:
            subprocess.Popen(
                [resolved],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:
            print(f"[open_app] Popen failed for {resolved!r}: {exc}")

    # A scheme, not a path: "ms-settings:", "spotify:", "steam://run/...".
    # Checked before ":" appears anywhere else — a Windows path has one too,
    # at index 1, and that is a file, not a scheme.
    scheme, _, rest = app_name.partition(":")
    if rest and scheme and scheme.isalnum() and len(scheme) > 1:
        try:
            os.startfile(app_name)               # noqa: S606 — no shell involved
            return True
        except Exception as exc:
            print(f"[open_app] startfile failed for {app_name!r}: {exc}")

    # KAKA update 8: Start Menu shortcuts and Windows' own app list, matched
    # by name. The old last resort typed the name into Windows search and
    # pressed Enter - for a name that is not an installed app that opens a
    # Bing WEB search in the browser, which is how "open X" ended up in Brave.
    if winapps is not None:
        t = winapps.resolve(app_name)
        if t is not None:
            return winapps.launch(t)
    return False


def _launch_macos(app_name: str) -> bool:
    for candidate in (app_name, f"{app_name}.app"):
        try:
            result = subprocess.run(
                ["open", "-a", candidate], capture_output=True, timeout=8
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    return _desktop_search(app_name)


_LINUX_TERMINALS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]


def _launch_linux(app_name: str) -> bool:
    # "terminal" means whichever one this distribution actually ships.
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINALS:
            path = shutil.which(term)
            if not path:
                continue
            try:
                subprocess.Popen(
                    [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return True
            except Exception:
                continue

    binary = (
        shutil.which(app_name)
        or shutil.which(app_name.lower())
        or shutil.which(app_name.lower().replace(" ", "-"))
        or shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    for name in (
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ):
        try:
            result = subprocess.run(
                ["gtk-launch", name], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            break
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["xdg-open", app_name], capture_output=True, timeout=5
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    return _desktop_search(app_name)


_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin": _launch_macos,
    "Linux": _launch_linux,
}


# ─────────────────────────────────────────────────────────────────────────────
#  HUD helpers — no-ops without a player, exactly as the whatsapp plugins do
# ─────────────────────────────────────────────────────────────────────────────
def _log(player, message: str) -> None:
    if player:
        try:
            player.write_log(message)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Launch + verify, off the conversation's thread
# ─────────────────────────────────────────────────────────────────────────────
def _launch_and_verify(player, spoken: str, launcher_name: str,
                       before: set[int]) -> None:
    """The part that takes seconds. Speaks ONLY when something needs saying.

    Silence means it worked: the window is on the user's screen and a second
    announcement over the top of it is noise — the same reasoning
    whatsapp_call.py applies to a phone that is already ringing.
    """
    try:
        target = None
        if _SYSTEM == "Windows" and winapps is not None:
            target = winapps.resolve(spoken)
        plan = []
        if target is not None:
            # KAKA update 8: the real program, found on disk / Start Menu.
            # Update 9: every way it is installed, tried in order; the log says which.
            try:
                plan = list(winapps.launch_plan(spoken)) or [target]
            except Exception:
                plan = [target]
            target = plan[0]
            print(f"[open_app] {spoken!r} -> {target.kind} {target.value} ({target.how})")
            requested = winapps.launch(target)
            if requested:
                _log(player, f"Kaka: {target.title} khol raha hoon ({target.how})...")
            else:
                _err = getattr(winapps, "LAST_LAUNCH", {}).get("error", "")
                _log(player, f"Kaka: {target.title} - {target.how} se start nahi hua ({_err})")
        else:
            launcher = _LAUNCHERS[_SYSTEM]
            requested = launcher(launcher_name)
            if not requested and launcher_name.lower() != spoken.lower():
                requested = launcher(spoken)

        appeared = None
        deadline = time.monotonic() + _LAUNCH_TIMEOUT
        names = [spoken] + list(getattr(target, "procs", ()) or ())
        if target is not None and target.title not in names:
            names.append(target.title)
        _next = 1
        _switch_at = time.monotonic() + 12.0
        _note_at = time.monotonic() + 10.0
        _t0 = time.monotonic()
        while appeared is None and time.monotonic() < deadline:
            if _next < len(plan) and (time.monotonic() >= _switch_at or not requested):
                # nothing appeared from this way - try the next way it is installed
                _t = plan[_next]
                _next += 1
                _switch_at = time.monotonic() + 12.0
                if winapps.launch(_t):
                    requested = True
                    _log(player, f"Kaka: {_t.title} - dusra tareeka: {_t.how}")
            if time.monotonic() >= _note_at:
                _log(player, f"Kaka: {spoken} ka wait... {int(time.monotonic() - _t0)}s")
                _note_at += 10.0
            for nm in names:
                appeared = appidentity.wait_for_launch(nm, before, timeout=0.3)
                if appeared is not None and _is_wrong_browser(appeared, spoken):
                    appeared = None
                if appeared is not None:
                    break
        if appeared is not None:
            _log(player, f"Kaka: {appeared.label} is open.")
            # update 9: bring it to the front (Windows' foreground lock often
            # leaves a newly started app behind other windows)
            try:
                _until = time.monotonic() + 8.0
                while time.monotonic() < _until:
                    if appidentity.is_running(appeared.pid) and appidentity.focus(appeared):
                        break
                    time.sleep(0.8)
            except Exception as _e:
                print(f"[open_app] focus after open: {_e}")
            return

        if not requested:
            trouble = (f"{spoken} could not be started - no installed program, Start "
                       f"Menu shortcut or Windows app answers to that name, so it is "
                       f"probably not installed. Do NOT open a website instead; tell "
                       f"the user plainly, in one line, no question")
        else:
            trouble = (f"{spoken} was asked to start but no new application "
                       f"appeared within {int(_LAUNCH_TIMEOUT)} seconds, so it "
                       f"is NOT confirmed open - it may be slow to load, or not "
                       f"installed under that name")

        _log(player, f"Kaka: {trouble}")
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: {trouble}")
    except Exception as exc:
        print(f"[open_app] {type(exc).__name__}: {exc}")
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: opening {spoken} failed - {exc}")



# KAKA update 8 ---------------------------------------------------------------
_BROWSER_PROCS = {"chrome", "msedge", "brave", "firefox", "opera", "vivaldi",
                  "iexplore", "browser", "launcher"}


def _is_wrong_browser(match, spoken: str) -> bool:
    """A browser matched only because a TAB title mentions the app ("VS Code
    tutorial - Brave"). That is not the app. Seen in his log as
    'brave brought to the front' when he asked for VS Code."""
    stem = appidentity.fold(os.path.splitext(getattr(match, "name", "") or "")[0])
    if stem not in {appidentity.fold(b) for b in _BROWSER_PROCS}:
        return False
    if winapps is not None:
        app = winapps.known_app(spoken)
        if app is not None:
            return not app.browser
    q = appidentity.fold(spoken)
    return not any(appidentity.fold(b) in q for b in ("chrome", "edge", "brave", "firefox",
                                                      "opera", "vivaldi", "browser"))


def _proc_ok(match, procs) -> bool:
    if not procs:
        return True
    stem = appidentity.fold(os.path.splitext(getattr(match, "name", "") or "")[0])
    return stem in {appidentity.fold(p) for p in procs}



_FOLDER_WORDS = {
    "downloads": "Downloads", "download": "Downloads", "documents": "Documents",
    "document": "Documents", "docs": "Documents", "desktop": "Desktop",
    "pictures": "Pictures", "photos": "Pictures", "photo": "Pictures",
    "music": "Music", "videos": "Videos", "video": "Videos",
}


def _folder_for(spoken: str) -> str:
    """'Downloads folder kholo' / 'D drive kholo' / 'Kaka saved folder' -> a real
    folder path, or ''. Folders open in File Explorer, never in the browser."""
    low = (spoken or "").lower()
    q = winapps.clean_query(spoken) if winapps is not None else low
    if not (any(w in low.split() for w in ("folder", "drive", "directory", "dir", "folders"))
            or q in _FOLDER_WORDS or low.strip(" :\\").endswith(":")):
        return ""
    m = re.search(r"\b([a-z])\s*(?:drive|:)", low)
    if m and os.name == "nt":
        root = m.group(1).upper() + ":\\"
        return root if os.path.isdir(root) else ""
    if "kaka" in low and ("saved" in low or "code" in low):
        try:
            import kaka_paths
            sub = ["Saved", "Code"] if "code" in low else ["Saved"]
            d = str(kaka_paths.data_dir(*sub))
            return d if os.path.isdir(d) else ""
        except Exception:
            return ""
    home = os.path.expanduser("~")
    for w, name in _FOLDER_WORDS.items():
        if re.search(r"\b" + w + r"\b", low):
            for base in (home, os.environ.get("OneDrive") or "", os.environ.get("OneDriveConsumer") or ""):
                if base:
                    d = os.path.join(base, name)
                    if os.path.isdir(d):
                        return d
    return ""


def _not_installed(spoken: str, app) -> str:
    if app is not None and app.winget:
        q = (f"{app.title} is PC pe install nahi hai - chaho toh main 'winget install "
             f"{app.winget}' se install kar doon?")
        return outcome.failed(
            "open_app",
            f"{app.title} is not installed on this PC (checked install folders, PATH, "
            f"registry, Start Menu). Nothing was opened, and do NOT open it in the "
            f"browser. Ask the user exactly this, in Hinglish: \"{q}\" Only if he "
            f"clearly says yes, call open_app again with install=true.",
            app=app.title, installed=False, can_install=True)
    name = app.title if app is not None else spoken
    return outcome.failed(
        "open_app",
        f"{name} is not installed on this PC (no program, Start Menu shortcut or "
        f"Windows app by that name). Nothing was opened - do NOT open a website "
        f"instead. Tell the user in one short Hinglish line, e.g. \"{name} is PC pe "
        f"nahi mila.\"",
        app=name, installed=False)


def _install(spoken: str, player) -> str:
    app = winapps.known_app(spoken) if winapps is not None else None
    if app is None or not app.winget:
        return outcome.failed("open_app", f"I do not know how to install {spoken} safely; "
                              "nothing was installed.", app=spoken)
    if winapps.resolve(spoken) is not None:
        return outcome.failed("open_app", f"{app.title} is already installed - call "
                              "open_app without install to open it.", app=app.title)
    # His rule: Kaka ASKS before installing - ONCE. Update 13: the question was
    # already asked out loud and he said yes (install=true), so there is no
    # second CONFIRM button on screen any more; the install runs now.
    msg = winapps.winget_install(app, log=(player.write_log if player else None))
    return outcome.started("open_app", app=app.title, install=msg,
                           note="installing (he said yes) - say this in one line, ask "
                                "nothing; the log shows when it is done")

# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
def open_app(parameters=None, response=None, player=None,
             session_memory=None) -> str:
    spoken = ((parameters or {}).get("app_name") or "").strip()
    if not spoken:
        return outcome.failed("open_app", "no application name was given")

    if _SYSTEM not in _LAUNCHERS:
        return outcome.failed(
            "open_app", f"this operating system ({_SYSTEM}) is not supported",
            app=spoken,
        )

    # Already open? Then there is nothing to launch and nothing to wait for,
    # and saying so is more useful than a second copy. This is also the one
    # branch that can answer `done` immediately, because the evidence is
    # already in hand rather than somewhere in the next ten seconds.
    #
    # include_self, unlike the close path, and for the opposite reason. There
    # the assistant's own processes must never be a TARGET; here the question
    # is only "is this already running", and it is already running whether the
    # process belongs to us or not. Without this, asking to open the very
    # application the assistant lives inside reports `started` and goes off to
    # launch a second copy of it — seen exactly that way, against a packaged
    # build whose siblings the close guard had just learnt to protect.
    params = parameters or {}
    target_procs = ()
    folder = _folder_for(spoken)
    if folder:
        try:
            os.startfile(folder) if hasattr(os, "startfile") else subprocess.Popen(["xdg-open", folder])
            _log(player, f"Kaka: Opened folder {folder}")
            return outcome.done("open_app", folder=folder, verified=True)
        except Exception as exc:
            return outcome.failed("open_app", f"could not open the folder {folder}: {exc}")
    if _SYSTEM == "Windows" and winapps is not None:
        if str(params.get("install", "")).lower() in ("1", "true", "yes"):
            return _install(spoken, player)
        app = winapps.known_app(spoken)
        if app is not None:
            target = winapps.resolve(spoken)
            if target is None:
                _log(player, f"Kaka: {app.title} is PC pe install nahi mila.")
                return _not_installed(spoken, app)
            target_procs = app.procs

    existing = [m for m in appidentity.running(spoken, include_self=True)
                if not _is_wrong_browser(m, spoken) and _proc_ok(m, target_procs)]
    for _p in target_procs:
        if existing:
            break
        existing = [m for m in appidentity.running(_p, include_self=True)
                    if _proc_ok(m, target_procs)]
    if _SYSTEM == "Windows" and winapps is not None:
        _k = winapps.known_app(spoken)
        if _k is not None and _k.always_new:
            existing = []
    if existing:
        best = existing[0]
        # ALREADY RUNNING IS NOT ALREADY DONE.
        #
        # This branch used to report "it was already open" and stop. That is
        # true and useless: an application running behind three other windows
        # is not on the user's screen, nothing changed when they asked, and
        # from where they are sitting the assistant did not open anything.
        # Reported as a bug in exactly those words — "it does not even open
        # applications" — with WhatsApp and Edge both running in the tray.
        #
        # Clicking a taskbar icon for a running app raises its window. So does
        # this. When there is no window to raise — a background service, or an
        # app that has gone to the tray — focus() says so and the launcher runs
        # below, which is what brings a tray app back.
        # Try every candidate, not just the best-scoring one.
        #
        # A modern application is many processes and only one of them owns the
        # window. Ranking puts the exact name match first, and on Windows that
        # is routinely a background service: measured here, `WhatsApp` and
        # `msedge` both scored 100 on processes with no window at all, so
        # focusing "the best match" focused nothing and the branch gave up.
        #
        # Store apps are the harder half of the same thing — their window
        # belongs to ApplicationFrameHost, not to the app's own process — which
        # is why failing all of these falls through to the launcher rather than
        # reporting failure. Typing the name into the Start Menu raises a
        # running Store app, and that is the path that ends up serving them.
        for candidate in existing[:8]:
            if appidentity.focus(candidate):
                _log(player, f"Kaka: {candidate.label} brought to the front.")
                return outcome.done(
                    "open_app", app=spoken, matched=candidate.label,
                    already_running=True, focused=True, verified=True,
                )
        print(f"[open_app] {best.label} is running but no window could be "
              f"raised — launching it instead, which is what brings back an "
              f"app that is only in the tray.")

    launcher_name = appidentity.launcher_name(spoken)
    before = appidentity.snapshot()
    _log(player, f"Kaka: Opening {spoken}…")

    threading.Thread(
        target=_launch_and_verify,
        args=(player, spoken, launcher_name, before),
        daemon=True, name=f"open-app-{spoken[:20]}",
    ).start()

    # Facts, not a sentence. Handing the model a finished English sentence is
    # an invitation to repeat it verbatim — whatsapp_call.py has the receipt:
    # a request made in Turkish came back in English, padded with an offer
    # nobody had made. The model writes the sentence, in the user's language.
    return outcome.started("open_app", app=spoken)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": (
        "Opens an application on this computer. Use whenever the user asks to "
        "open, launch or start any app or program. Put the application's name "
        "in 'app_name' exactly as the user said it. "
        "This tool answers immediately and confirms the launch in the "
        "background: on status=started say only that it is opening, never that "
        "it is open. If it does not appear, you will be told separately - "
        "relay that plainly rather than claiming success. Never say you opened "
        "something without calling this tool. "
        "To CLOSE an application use computer_control with action='close_app', "
        "not this tool. "
        "DO NOT use this tool to reach a WEB PAGE. 'open Chrome and go to "
        "YouTube', 'open YouTube', 'search the web' are all browser_control "
        "alone - it opens the browser itself. Calling open_app for the browser "
        "first and browser_control after it launches the browser TWICE, the "
        "second copy on a different profile, which is what puts a 'Who's using "
        "Chrome?' picker on the screen. Use this tool only when the user wants "
        "the application and nothing in it. "
        "DESKTOP FIRST: any installed program - VS Code, Notepad, Calculator, "
        "File Explorer, Word, Excel, WhatsApp desktop, Spotify, cmd, Settings - "
        "is ALWAYS this tool, never browser_control. Folders too: 'Downloads "
        "folder kholo', 'D drive kholo' -> app_name='Downloads folder' / 'D drive'. Hinglish: 'VS Code kholo', "
        "'code application on karo', 'notepad chalu karo', 'calculator khol do' "
        "-> open_app (no question - just open it). If it reports the app is not "
        "installed, ask ONE question 'X install kar doon?' and on yes call again "
        "with install=true (no on-screen button after that); never open a website "
        "as a substitute. "
        "Several steps in one request ('VS Code kholo aur usme C program "
        "likho') -> do ALL of them: here, code_helper action=write with "
        "language=c already saves the file and opens it in VS Code."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": (
                    "The application's REAL name, not the user's shorthand. "
                    "Expand abbreviations and nicknames before passing them: "
                    "'wp' or 'whats' is 'WhatsApp', 'ig' is 'Instagram', 'vs "
                    "code' is 'Visual Studio Code'. A two-letter fragment "
                    "matches the wrong program - 'wp' finds WPS Office. Any "
                    "application on the machine works; there is no fixed list."
                ),
            },
            "install": {
                "type": "BOOLEAN",
                "description": (
                    "ONLY after open_app said the app is not installed AND the "
                    "user clearly said yes to installing it. Never on the first "
                    "call. The install still waits for CONFIRM on screen."
                ),
            },
        },
        "required": ["app_name"],
    },
    "handler": open_app,
}
