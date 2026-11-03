"""Build step for Kaka-Setup.exe: make the installer engine from the .bat.

Kaka-Setup.exe does not re-implement the installer. It runs the very same
PowerShell part that lives inside Kaka-Setup-V1.1.bat, so the exe and the bat
always do the same thing. This script reads the bat, takes its PowerShell part
(everything from the #KAKU-PS-START marker) and adds a few small hooks so the
window can talk to it:

  * questions (name, city, "Continue anyway?" ...) are answered by the window
    instead of the black console (GuiAsk)
  * the console output is UTF-8 so the window shows every character
  * "restart as administrator" restarts Kaka-Setup.exe instead of the bat
  * the Python 3.13 installer packed inside the exe is used before downloading
  * Kaka is opened by the window's "Launch Kaka" tick box on the last page

Every hook is an exact text match; if the bat ever changes so a hook no longer
fits, this script stops with an error instead of building a wrong installer.

    python build_engine.py ../Kaka-Setup-V1.1.bat build/

writes build/kaka_setup_engine.ps1 and build/Kaka-Setup.bat (exact copy).

Copyright (C) 2026 Abhinaba Chanda
"""
import hashlib
import shutil
import sys
from pathlib import Path

MARK = "#KAKU-PS-" + "START"

PRELUDE = r"""
# ---- Kaka-Setup.exe hooks (added by installer/build_engine.py) ----
$script:KakaGui = ($env:KAKA_GUI -eq '1')
if ($script:KakaGui) { try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { } }
function GuiAsk ($q, $default) {
    $ans = $null
    if ($env:KAKA_GUI_ANSWERS) {
        try {
            $map = $env:KAKA_GUI_ANSWERS | ConvertFrom-Json
            foreach ($p in $map.PSObject.Properties) { if (([string]$q).StartsWith($p.Name)) { $ans = [string]$p.Value; break } }
        } catch { }
    }
    if ($null -eq $ans) {
        $o = (@{ q = [string]$q; d = [string]$default } | ConvertTo-Json -Compress)
        [Console]::Out.WriteLine('@@KAKA-ASK@@' + $o)
        [Console]::Out.Flush()
        $ans = [Console]::In.ReadLine()
        if ($null -eq $ans) { $ans = '' }
    }
    $shown = $ans; if ($shown -eq '') { $shown = '(Enter)' }
    Write-Host "    $q [$default] $shown"
    $ans = $ans.Trim()
    if ($ans -eq '') { return $default }
    return $ans
}
# ---- end of Kaka-Setup.exe hooks ----
"""

PATCHES = [
    (
        "ask",
        "    if ($Unattended) { Write-Host \"    $q [$default] (auto)\"; return $default }\n",
        "    if ($Unattended) { Write-Host \"    $q [$default] (auto)\"; return $default }\n"
        "    if ($script:KakaGui) { return (GuiAsk $q $default) }\n",
    ),
    (
        "admin-relaunch",
        "            Start-Process -FilePath $env:KAKU_BAT -ArgumentList ('/folder=' + (Hex-Encode $Folder)) -Verb RunAs -ErrorAction Stop\n",
        "            $relaunch = $env:KAKU_BAT; if ($script:KakaGui -and $env:KAKA_GUI_EXE) { $relaunch = $env:KAKA_GUI_EXE }\n"
        "            Start-Process -FilePath $relaunch -ArgumentList ('/folder=' + (Hex-Encode $Folder)) -Verb RunAs -ErrorAction Stop\n",
    ),
    (
        "bundled-python",
        "    $v = Get-LatestPy313\n"
        "    $exe = P2 $tmp \"python-$v-amd64.exe\"\n"
        "    Say \"Downloading Python $v from python.org (about 28 MB)...\"\n"
        "    $okDl = Get-File \"https://www.python.org/ftp/python/$v/python-$v-amd64.exe\" $exe \"Python $v\" 'python'\n",
        "    if ($script:KakaGui -and $env:KAKA_GUI_PY_EXE -and (Test-Path $env:KAKA_GUI_PY_EXE)) {\n"
        "        $v = [string]$env:KAKA_GUI_PY_VER\n"
        "        $exe = P2 $tmp \"python-$v-amd64.exe\"\n"
        "        Copy-Item -LiteralPath $env:KAKA_GUI_PY_EXE -Destination $exe -Force\n"
        "        Say \"Using the Python $v installer packed inside Kaka-Setup.exe (no download needed).\"\n"
        "        $okDl = $true\n"
        "    } else {\n"
        "    $v = Get-LatestPy313\n"
        "    $exe = P2 $tmp \"python-$v-amd64.exe\"\n"
        "    Say \"Downloading Python $v from python.org (about 28 MB)...\"\n"
        "    $okDl = Get-File \"https://www.python.org/ftp/python/$v/python-$v-amd64.exe\" $exe \"Python $v\" 'python'\n"
        "    }\n",
    ),
    (
        "launch",
        "if ($Mock) { Info '(test) Kaka launch skipped'; $launched = $true }\n",
        "if ($Mock) { Info '(test) Kaka launch skipped'; $launched = $true }\n"
        "elseif ($script:KakaGui) {\n"
        "    $lj = (@{ target = [string]$lnkTarget; exe = [string]$Exe; pyw = [string]$Pyw; launcher = [string]$Launcher; workdir = [string]$Src } | ConvertTo-Json -Compress)\n"
        "    [Console]::Out.WriteLine('@@KAKA-LAUNCH@@' + $lj)\n"
        "    [Console]::Out.Flush()\n"
        "}\n",
    ),
    (
        "keys-text",
        "Say 'API keys: Setup never asks for them. In Kaka:'\n",
        "if ($script:KakaGui) { Say 'API keys (if you did not paste one on the key page):' } else { Say 'API keys: Setup never asks for them. In Kaka:' }\n",
    ),
]


def build(bat_path: Path, out_dir: Path) -> None:
    raw = bat_path.read_bytes()
    text = raw.decode("utf-8")
    # the bat is CRLF on disk; PowerShell does not care, the hooks are matched on LF
    text = text.replace("\r\n", "\n")
    i = text.rfind(MARK)
    if i < 0:
        sys.exit("build_engine: the PowerShell marker was not found in " + str(bat_path))
    ps = text[i:]
    first_nl = ps.index("\n") + 1
    ps = ps[:first_nl] + PRELUDE + ps[first_nl:]
    for name, old, new in PATCHES:
        n = ps.count(old)
        if n != 1:
            sys.exit(f"build_engine: hook '{name}' matched {n} times (expected exactly 1). The bat changed; update installer/build_engine.py.")
        ps = ps.replace(old, new)
    for ch in ps:
        if ord(ch) > 126 and ch not in "\n":
            sys.exit("build_engine: non-ASCII character in the engine script")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "kaka_setup_engine.ps1").write_bytes(ps.replace("\n", "\r\n").encode("ascii"))
    shutil.copyfile(bat_path, out_dir / "Kaka-Setup.bat")
    print("engine:", out_dir / "kaka_setup_engine.ps1", len(ps), "chars")
    print("bat sha256:", hashlib.sha256(raw).hexdigest(), len(raw), "bytes")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    bat = Path(sys.argv[1]) if len(sys.argv) > 1 else here.parent / "Kaka-Setup-V1.1.bat"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "build"
    build(bat, out)
