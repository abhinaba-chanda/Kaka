"""
core/sleep_hotkey.py - Kaka update 13: one global shortcut that puts Kaka to
sleep and wakes it again, from any window.

Windows: RegisterHotKey in its own thread with its own message loop (the
hotkey message is posted to the thread that registered it). No new packages,
no admin. Ctrl+1 is his choice; if another program already owns it,
RegisterHotKey fails and Ctrl+Alt+1 is used instead - the label says which.
A global Ctrl+1 means Chrome / VS Code no longer see their own Ctrl+1 while
Kaka runs (accepted).

Other systems: nothing is registered (label ""), the HUD button still works.
The class never raises.
"""
from __future__ import annotations

import platform
import threading
from typing import Callable

_IS_WIN = platform.system() == "Windows"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_1 = 0x31

# (modifiers, virtual key, label) - tried in order
CHORDS = [
    (MOD_CONTROL | MOD_NOREPEAT, VK_1, "Ctrl+1"),
    (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_1, "Ctrl+Alt+1"),
]
_HOTKEY_ID = 0x4B4B      # "KK"


def _user32():
    import ctypes
    return ctypes.WinDLL("user32", use_last_error=True)


class SleepHotkey:
    def __init__(self, on_press: Callable[[], None], log: Callable[[str], None] | None = None,
                 chords=None, user32=None):
        self._on_press = on_press
        self._log = log or (lambda m: None)
        self._chords = list(chords or CHORDS)
        self._user32 = user32           # tests pass a fake
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()
        self.label = ""                 # the chord that is active, "" = none
        self.error = ""

    # -- the registration step, separate so it can be tested without a loop
    def _register(self, u32) -> str:
        for mods, vk, label in self._chords:
            try:
                ok = u32.RegisterHotKey(None, _HOTKEY_ID, mods, vk)
            except Exception as e:      # noqa: BLE001
                ok = False
                self.error = str(e)
            if ok:
                return label
            self._log(f"SYS: Sleep shortcut {label} pehle se kisi aur program ka hai - agla try kar raha hoon.")
        return ""

    def _loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            u32 = self._user32 or _user32()
            k32 = ctypes.WinDLL("kernel32")
            self._tid = k32.GetCurrentThreadId()
            self.label = self._register(u32)
        except Exception as e:          # noqa: BLE001
            self.error = str(e)
            self.label = ""
        self._ready.set()
        if not self.label:
            return
        try:
            msg = wintypes.MSG()
            while True:
                r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        self._on_press()
                    except Exception as e:      # noqa: BLE001
                        print(f"[SleepHotkey] {e}")
        finally:
            try:
                u32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass

    def start(self, timeout: float = 3.0) -> str:
        """Register the shortcut. Returns the active chord label, "" if none."""
        if not _IS_WIN and self._user32 is None:
            return ""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="kaka-sleep-hotkey")
            self._thread.start()
        self._ready.wait(timeout)
        return self.label

    def stop(self) -> None:
        if self._tid:
            try:
                _user32().PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
