"""Build Kaka-Setup.exe (run on Windows with Python 3.12).

    pip install -r installer/requirements-build.txt
    python installer/build.py            # -> installer/dist/Kaka-Setup.exe

Steps: make the installer engine from ../Kaka-Setup-V1.1.bat (build_engine.py),
put the newest Python 3.13 installer from python.org next to it (so a PC
without Python does not have to download it during Setup; Setup still checks
its signature before running it), then pack everything into one windowed exe
with the Kaka icon and version information. No UPX.

Copyright (C) 2026 Abhinaba Chanda
"""
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
UA = {"User-Agent": "Kaka-Setup-build/1.1"}


def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def latest_python313():
    """Same rule as the bat: the newest 3.13.x that has a 64-bit Windows installer."""
    txt = get("https://www.python.org/ftp/python/").decode("utf-8", "replace")
    vers = sorted({int(m) for m in re.findall(r'href="3\.13\.(\d+)/"', txt)}, reverse=True)
    for n in vers[:4]:
        v = "3.13.%d" % n
        try:
            req = urllib.request.Request("https://www.python.org/ftp/python/%s/python-%s-amd64.exe" % (v, v), headers=UA, method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return v
        except Exception:
            pass
    raise SystemExit("no Python 3.13 Windows installer found on python.org")


def main():
    subprocess.check_call([sys.executable, str(HERE / "build_engine.py"), str(HERE.parent / "Kaka-Setup-V1.1.bat"), str(BUILD)])
    for old in BUILD.glob("python-3.13.*-amd64.exe"):
        old.unlink()
    if "--no-python" not in sys.argv:
        v = latest_python313()
        dest = BUILD / ("python-%s-amd64.exe" % v)
        print("downloading Python", v)
        dest.write_bytes(get("https://www.python.org/ftp/python/%s/python-%s-amd64.exe" % (v, v), timeout=300))
        print("  ", dest.name, dest.stat().st_size, "bytes")
    sep = ";" if sys.platform == "win32" else ":"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
            "--name", "Kaka-Setup",
            "--icon", str(HERE / "assets" / "kaka.ico"),
            "--version-file", str(HERE / "version_info.txt"),
            "--distpath", str(HERE / "dist"), "--workpath", str(BUILD / "pyi"), "--specpath", str(BUILD),
            "--add-data", "%s%sassets" % (HERE / "assets", sep),
            "--add-data", "%s%sengine" % (BUILD / "kaka_setup_engine.ps1", sep),
            "--add-data", "%s%sengine" % (BUILD / "Kaka-Setup.bat", sep),
            "--hidden-import", "PySide6.QtSvg"]
    for py in BUILD.glob("python-3.13.*-amd64.exe"):
        args += ["--add-data", "%s%sengine" % (py, sep)]
    for mod in ("tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf"):
        args += ["--exclude-module", mod]
    args.append(str(HERE / "kaka_setup.py"))
    subprocess.check_call(args)
    exe = HERE / "dist" / ("Kaka-Setup.exe" if sys.platform == "win32" else "Kaka-Setup")
    print("built", exe, exe.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
