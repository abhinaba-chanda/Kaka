r"""
kaka_paths - one place that decides where Kaka keeps the things it CREATES.

Added for Abhinaba (Nov 2026). Everything Kaka makes (uploads from the phone,
screenshots, projects, reminders, browser profiles, logs, temp audio, model
caches) lives under one data folder on the same drive as the app:

    <install folder>\Data\   (app in <install folder>\app, e.g. C:\Kaka\app)

Set the environment variable KAKA_DATA to use another folder (Kaka-Setup.bat
sets it to <install folder>\Data). An older layout that keeps Data one level
above the app's folder is still found.
This file never changes where Kaka LOOKS for your own files (Desktop,
Downloads, Documents...) - only where it writes its own.

If the data folder cannot be created, every helper falls back to the old
place, so nothing breaks.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_APP = Path(__file__).resolve().parent


def _root() -> Path:
    env = os.environ.get("KAKA_DATA", "").strip()
    if env:
        return Path(env)
    here = _APP.parent                    # the install folder (holds app\)
    if (here / "Data").is_dir():
        return here / "Data"
    # older layout: <Kaka>\<folder>\app with Data one level higher
    up = here.parent / "Data"
    try:
        if up.is_dir() and any((up / m).exists() for m in ("Cache", "Logs", "kaka-timing.txt", "notes.json")):
            return up
    except Exception:
        pass
    return here / "Data"


DATA_ROOT = _root()


def data_dir(*parts: str, fallback: Path | None = None) -> Path:
    """DATA_ROOT/parts..., created. Falls back to `fallback` (created) on error."""
    p = DATA_ROOT.joinpath(*parts)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        if fallback is not None:
            try:
                fallback.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            return fallback
        raise


def uploads() -> Path:
    return data_dir("Uploads", fallback=Path.home() / "Downloads" / "Kaka Uploads")


def screenshots() -> Path:
    return data_dir("Screenshots", fallback=Path.home() / "Desktop")


def saved() -> Path:
    """Text files Kaka writes for you (YouTube summaries, flight results, code)."""
    return data_dir("Saved", fallback=Path.home() / "Desktop")


def projects() -> Path:
    return data_dir("Projects", fallback=Path.home() / "Desktop" / "KakaProjects")


def reminders() -> Path:
    return data_dir("Reminders", fallback=Path.home() / ".jarvis" / "reminders")


def browser_profiles() -> Path:
    return data_dir("BrowserProfiles", fallback=Path.home() / ".jarvis_profiles")


def logs() -> Path:
    return data_dir("Logs", fallback=Path(tempfile.gettempdir()))


def cache() -> Path:
    return data_dir("Cache", fallback=Path(tempfile.gettempdir()))


def _has_whisper(hub: Path) -> bool:
    try:
        return any(hub.glob("models--*faster-whisper*"))
    except Exception:
        return False


_applied = False


def apply() -> None:
    """Point temp files and model caches at the data folder. Call once, early
    (before faster_whisper / huggingface_hub are imported). Never raises."""
    global _applied
    if _applied:
        return
    _applied = True
    try:
        tmp = data_dir("Temp")
        os.environ["TMP"] = str(tmp)
        os.environ["TEMP"] = str(tmp)
        tempfile.tempdir = str(tmp)
    except Exception:
        pass
    try:
        if not os.environ.get("HF_HOME"):
            new_home = data_dir("Cache", "huggingface")
            old_hub = Path.home() / ".cache" / "huggingface" / "hub"
            # Only switch when it will not cost a fresh ~500 MB download: the
            # Whisper model is already on the data drive, or not on C: either.
            if _has_whisper(new_home / "hub") or not _has_whisper(old_hub):
                os.environ["HF_HOME"] = str(new_home)
    except Exception:
        pass


def summary() -> str:
    return str(DATA_ROOT)
