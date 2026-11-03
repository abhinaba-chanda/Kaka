# Kaka-Setup.exe

The windowed installer for **Kaka by Abhinaba Chanda**. It does exactly what
`Kaka-Setup-V1.1.bat` does — same folder rules, repair / update / uninstall,
name and city questions, PC check, options, Python 3.13, packages, tools,
wake word, shortcuts and log (`%USERPROFILE%\kaka-setup-log.txt`) — because it
runs the bat's own PowerShell part. The window only asks the questions and
shows the live progress. It also lets you paste your Gemini API key (optional).

| File | What it is |
| --- | --- |
| `kaka_setup.py` | the window (PySide6) and the silent mode |
| `build_engine.py` | takes the PowerShell part out of `../Kaka-Setup-V1.1.bat` and adds the small hooks the window needs |
| `build.py` | builds `dist/Kaka-Setup.exe` (also packs the Python 3.13 installer from python.org) |
| `version_info.txt` | file properties: Kaka Setup, Copyright (C) 2026 Abhinaba Chanda |
| `ci_test.ps1` | the Windows test: install, repair through the window, uninstall |
| `assets/` | Kaka icon and logo, the GitHub mark (Octicons, MIT) |

Build on Windows (Python 3.12):

```
pip install -r installer/requirements-build.txt
python installer/build.py
```

or run the **Build Kaka-Setup.exe** workflow from the Actions tab.

Silent install (for tests and power users):

```
Kaka-Setup.exe --silent --name Abhinaba --city Kolkata --dir D:\Kaka --key YOUR_GEMINI_KEY
Kaka-Setup.exe --silent --mode repair --dir D:\Kaka
Kaka-Setup.exe --silent --mode uninstall --dir D:\Kaka
```

Copyright (C) 2026 Abhinaba Chanda.
