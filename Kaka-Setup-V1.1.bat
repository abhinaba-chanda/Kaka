@echo off
setlocal
title KAKA by Abhinaba Chanda - Setup
rem ------------------------------------------------------------------------
rem  Kaka-Setup-V1.1.bat - one-file installer for "Kaka by Abhinaba Chanda"
rem  https://github.com/abhinaba-chanda/Kaka
rem  Double-click it. It asks where to install, checks your PC, installs
rem  Python 3.13 if needed, downloads Kaka, its packages and tools, and
rem  makes shortcuts. Run it again later to repair, update or uninstall.
rem  It never asks for API keys - you add those inside Kaka (gear > API KEYS).
rem  Log: %USERPROFILE%\kaka-setup-log.txt
rem ------------------------------------------------------------------------
set "KAKU_BAT=%~f0"
set "KAKU_ARGS=%*"
where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Windows PowerShell was not found on this PC. Kaka Setup needs it.
  echo  Fix: use Windows 10 or 11 ^(PowerShell is built in^), then run this file again.
  echo.
  pause
  exit /b 9
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:KAKU_BAT); $i=$s.LastIndexOf('#KAKU-PS-' + 'START'); if ($i -lt 0) { Write-Host 'This file looks damaged. Please download Kaka-Setup.bat again.'; exit 9 }; & ([ScriptBlock]::Create($s.Substring($i))); exit $LASTEXITCODE"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" goto ok
if "%RC%"=="20" goto handoff
echo  ===== Kaka Setup stopped (code %RC%) =====
echo  The reason and the fix are shown above.
echo  Full log: %USERPROFILE%\kaka-setup-log.txt
goto end
:handoff
echo  Setup continues in the new administrator window. You can close this one.
goto end
:ok
echo  ===== Kaka Setup finished =====
echo  Log: %USERPROFILE%\kaka-setup-log.txt
:end
echo.
if not defined KAKA_NO_PAUSE pause
exit /b %RC%
#KAKU-PS-START
# Kaka by Abhinaba Chanda - Setup (PowerShell part). Runs inside Kaka-Setup.bat.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

$Repo      = 'abhinaba-chanda/Kaka'
$RepoUrl   = "https://github.com/$Repo"
$PyFallback = '3.13.17'
$NeedGB    = 3
$DefaultFolder = 'C:\Kaka'
$Dash      = [string][char]0x2014
$OnWindows = ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT)
$Mock      = ($env:KAKA_SETUP_MOCK -eq '1')
$Unattended = ($env:KAKA_SETUP_UNATTENDED -eq '1')
$Home2     = $env:USERPROFILE; if (-not $Home2) { $Home2 = $HOME }
$LogFile   = [IO.Path]::Combine($Home2, 'kaka-setup-log.txt')
try { $Host.UI.RawUI.WindowTitle = "KAKA by Abhinaba Chanda $Dash Setup" } catch { }
try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

# ------------------------------------------------------------------ output helpers
function Say  ($m) { Write-Host "    $m" }
function Ok   ($m) { Write-Host '    [ OK ] ' -ForegroundColor Green -NoNewline; Write-Host $m }
function Info ($m) { Write-Host '    [ i  ] ' -ForegroundColor Cyan -NoNewline; Write-Host $m }
function Warn ($m) { Write-Host '    [ !  ] ' -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Bad  ($m) { Write-Host '    [ X  ] ' -ForegroundColor Red -NoNewline; Write-Host $m }
function Fix  ($m) { Write-Host '    Fix:   ' -ForegroundColor Magenta -NoNewline; Write-Host $m }
$script:StepNo = 0
$script:StepTotal = 8
function Step ($title) {
    $script:StepNo++
    Write-Host ''
    Write-Host ("  [{0}/{1}] " -f $script:StepNo, $script:StepTotal) -ForegroundColor Black -BackgroundColor Cyan -NoNewline
    Write-Host " $title" -ForegroundColor Cyan
    Write-Host ('  ' + ('-' * 66)) -ForegroundColor DarkGray
}
function Finish ($code) {
    if ($code -ne 0 -and $code -ne 20) {
        Write-Host ''
        Write-Host "    Full log: $LogFile" -ForegroundColor DarkGray
    }
    try { Stop-Transcript | Out-Null } catch { }
    exit $code
}
function Die ($what, $fix, $code) {
    Write-Host ''
    Bad $what
    if ($fix) { Fix $fix }
    Finish $code
}
function Banner {
    Write-Host ''
    Write-Host '   _  __    _    _  __    _    ' -ForegroundColor Cyan
    Write-Host '  | |/ /   / \  | |/ /   / \   ' -ForegroundColor Cyan
    Write-Host "  | ' /   / _ \ | ' /   / _ \  " -ForegroundColor Cyan
    Write-Host '  | . \  / ___ \| . \  / ___ \ ' -ForegroundColor Cyan
    Write-Host '  |_|\_\/_/   \_\_|\_\/_/   \_\' -ForegroundColor Cyan
    Write-Host ''
    Write-Host "  KAKA by Abhinaba Chanda $Dash Setup" -ForegroundColor White
    Write-Host '  Your personal desktop AI assistant for Windows' -ForegroundColor Gray
    Write-Host "  $RepoUrl" -ForegroundColor DarkGray
    Write-Host ('  ' + ('=' * 66)) -ForegroundColor DarkCyan
    Write-Host "  Log file: $LogFile" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------ input helpers
function Ask ($q, $default) {
    if ($Unattended) { Write-Host "    $q [$default] (auto)"; return $default }
    Write-Host "    $q " -ForegroundColor White -NoNewline
    if ($default) { Write-Host "[$default] " -ForegroundColor DarkGray -NoNewline }
    $a = Read-Host
    if ($null -eq $a) { $a = '' }
    $a = $a.Trim()
    if ($a -eq '') { return $default }
    return $a
}
function AskYN ($q, $defaultYes) {
    $d = 'N'; if ($defaultYes) { $d = 'Y' }
    while ($true) {
        $a = Ask "$q (Y/N)" $d
        if ($a -match '^(y|yes|haan|ha|h)$') { return $true }
        if ($a -match '^(n|no|nahi|na)$') { return $false }
        Warn 'Please type Y or N.'
    }
}

# ------------------------------------------------------------------ small utils
function P2 ($a, $b) {
    if (-not $OnWindows) { $b = $b -replace '\\', '/' }
    return [IO.Path]::Combine($a, $b)
}
function VenvPy ($venv, $gui) {
    if ($OnWindows) {
        if ($gui) { return (P2 $venv 'Scripts\pythonw.exe') }
        return (P2 $venv 'Scripts\python.exe')
    }
    return (P2 $venv 'bin/python')
}
function Hex-Encode ($s) { return (([Text.Encoding]::UTF8.GetBytes($s) | ForEach-Object { $_.ToString('x2') }) -join '') }
function Hex-Decode ($h) {
    $b = New-Object byte[] ($h.Length / 2)
    for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16) }
    return [Text.Encoding]::UTF8.GetString($b)
}
function Test-Admin {
    if (-not $OnWindows) { return $false }
    try { return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) } catch { return $false }
}
function Test-Writable ($dir) {
    try {
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
        $t = P2 $dir ('.kaka-write-test-' + [Guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText($t, 'ok')
        Remove-Item $t -Force -ErrorAction SilentlyContinue
        return $true
    } catch { return $false }
}
function Get-FreeGB ($path) {
    if ($Mock -and $env:KAKA_MOCK_FREE_GB) { return [double]$env:KAKA_MOCK_FREE_GB }
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
        $d = New-Object IO.DriveInfo $root
        return [math]::Round($d.AvailableFreeSpace / 1GB, 1)
    } catch { return -1 }
}
function Fmt-MB ($bytes) { return ('{0:N1} MB' -f ($bytes / 1MB)) }

# Download with a live % line. $key names the mock file in test runs.
function Get-File ($url, $dest, $label, $key) {
    if ($Mock) {
        $src = [Environment]::GetEnvironmentVariable('KAKA_MOCK_' + $key)
        if (-not $src -or -not (Test-Path $src)) { Bad "(test) no mock file for $key"; return $false }
        $len = (Get-Item $src).Length
        foreach ($p in 25, 50, 75, 100) { [Console]::Write(("`r    {0,-34} {1,3}%  {2} of {3}   " -f $label, $p, (Fmt-MB ($len * $p / 100)), (Fmt-MB $len))) }
        [Console]::WriteLine('')
        Copy-Item $src $dest -Force
        return $true
    }
    for ($try = 1; $try -le 3; $try++) {
        $client = $null; $resp = $null; $in = $null; $out = $null
        try {
            Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
            $handler = New-Object System.Net.Http.HttpClientHandler
            $handler.AllowAutoRedirect = $true
            $client = New-Object System.Net.Http.HttpClient($handler)
            $client.Timeout = [TimeSpan]::FromMinutes(30)
            $client.DefaultRequestHeaders.UserAgent.ParseAdd('Kaka-Setup/1.0')
            $resp = $client.GetAsync($url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
            if (-not $resp.IsSuccessStatusCode) { throw ("server answered " + [int]$resp.StatusCode) }
            $total = $resp.Content.Headers.ContentLength
            $in = $resp.Content.ReadAsStreamAsync().Result
            $out = [IO.File]::Create($dest)
            $buf = New-Object byte[] 262144
            $got = 0L
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $last = -1000
            while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                $out.Write($buf, 0, $n)
                $got += $n
                if (($sw.ElapsedMilliseconds - $last) -ge 250) {
                    $last = $sw.ElapsedMilliseconds
                    $speed = ''
                    if ($sw.Elapsed.TotalSeconds -gt 0.5) { $speed = ('{0:N1} MB/s' -f (($got / 1MB) / $sw.Elapsed.TotalSeconds)) }
                    if ($total -and $total -gt 0) {
                        $pct = [int](100 * $got / $total)
                        [Console]::Write(("`r    {0,-34} {1,3}%  {2} of {3}  {4}   " -f $label, $pct, (Fmt-MB $got), (Fmt-MB $total), $speed))
                    } else {
                        [Console]::Write(("`r    {0,-34} {1}  {2}   " -f $label, (Fmt-MB $got), $speed))
                    }
                }
            }
            $out.Close(); $out = $null
            if ($total -and $total -gt 0 -and $got -ne $total) { throw "download stopped early ($got of $total bytes)" }
            [Console]::Write(("`r    {0,-34} 100%  {1}                    " -f $label, (Fmt-MB $got)))
            [Console]::WriteLine('')
            return $true
        } catch {
            [Console]::WriteLine('')
            $msg = $_.Exception.Message
            if ($_.Exception.InnerException) { $msg = $_.Exception.InnerException.Message }
            Warn "Download failed (try $try of 3): $msg"
            Start-Sleep -Seconds (2 * $try)
        } finally {
            if ($out) { $out.Close() }
            if ($in) { $in.Dispose() }
            if ($resp) { $resp.Dispose() }
            if ($client) { $client.Dispose() }
        }
    }
    Remove-Item $dest -Force -ErrorAction SilentlyContinue
    return $false
}
function Get-Text ($url) {
    if ($Mock) {
        if ($url -like '*releases/latest*') { return '{"tag_name":"v1.0"}' }
        if ($url -like '*checksums*') { $f = $env:KAKA_MOCK_ffsum; if ($f) { return [IO.File]::ReadAllText($f) } }
        return ''
    }
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 30 -Headers @{ 'User-Agent' = 'Kaka-Setup/1.0' }
        if ($r.Content -is [byte[]]) { return [Text.Encoding]::UTF8.GetString($r.Content) }
        return [string]$r.Content
    } catch { return $null }
}
function Get-Sha256 ($path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $fs = [IO.File]::OpenRead($path)
    try { return ([BitConverter]::ToString($sha.ComputeHash($fs))).Replace('-', '').ToLower() } finally { $fs.Close() }
}

# Run a program and show its output live (and in the log). Returns the exit code.
function Run-Live ($exe, [string[]]$argList, $filter) {
    $script:LiveCount = 0
    & $exe @argList 2>&1 | ForEach-Object {
        $line = [string]$_
        if ($line.Trim() -eq '') { return }
        if ($filter -eq 'pip') {
            if ($line -match '^\s*(Collecting|Downloading|Using cached|Installing collected|Successfully|Building|Requirement already)') {
                if ($line -match '^\s*Collecting') { $script:LiveCount++; $line = ('[{0,3}] {1}' -f $script:LiveCount, $line.Trim()) }
                if ($line.Length -gt 110) { $line = $line.Substring(0, 107) + '...' }
                if ($line -match 'Successfully') { Write-Host "      $line" -ForegroundColor Green } else { Write-Host "      $line" -ForegroundColor Gray }
            } elseif ($line -match '(?i)error|failed|could not') {
                Write-Host "      $line" -ForegroundColor Yellow
            }
        } elseif ($filter -eq 'setup') {
            if ($line -match '^SETUP\|([^|]*)\|([^|]*)\|(.*)$') {
                $script:SetupLines += ,@($Matches[1], $Matches[2], $Matches[3])
                if ($Matches[2] -eq 'ok') { Ok $Matches[3] } else { Warn $Matches[3] }
            } elseif ($line -match '^PROG\|[^|]*\|(.*)$') {
                Write-Host "      $($Matches[1])" -ForegroundColor Gray
            } else {
                if ($line.Length -gt 110) { $line = $line.Substring(0, 107) + '...' }
                Write-Host "      $line" -ForegroundColor Gray
            }
        } else {
            if ($line.Length -gt 110) { $line = $line.Substring(0, 107) + '...' }
            Write-Host "      $line" -ForegroundColor Gray
        }
    }
    return $LASTEXITCODE
}

# ------------------------------------------------------------------ Python 3.13
function Test-Py ($p) {
    if (-not $p) { return $false }
    if ($p -like '*\WindowsApps\*') { return $false }   # the Store "python" stub
    if (-not (Test-Path $p)) { return $false }
    try {
        $v = & $p -c "import sys,struct;print('%d.%d|%d' % (sys.version_info[0],sys.version_info[1],struct.calcsize('P')*8))" 2>$null
        return ([string]$v).Trim() -eq '3.13|64'
    } catch { return $false }
}
function Find-Python313 {
    $c = @()
    if ($env:KAKA_SETUP_PY) { $c += $env:KAKA_SETUP_PY }
    if ($OnWindows) {
        try {
            $pyl = Get-Command py -ErrorAction SilentlyContinue
            if ($pyl) {
                $e = & $pyl.Source -3.13 -c "import sys;print(sys.executable)" 2>$null
                if ($e) { $c += ([string]$e).Trim() }
            }
        } catch { }
        foreach ($hive in 'HKCU:', 'HKLM:') {
            try {
                $k = Get-ItemProperty "$hive\Software\Python\PythonCore\3.13\InstallPath" -ErrorAction Stop
                if ($k.ExecutablePath) { $c += $k.ExecutablePath }
                elseif ($k.'(default)') { $c += (P2 $k.'(default)' 'python.exe') }
            } catch { }
        }
        if ($env:LOCALAPPDATA) { $c += (P2 $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe') }
        if ($env:ProgramFiles) { $c += (P2 $env:ProgramFiles 'Python313\python.exe') }
        try { foreach ($g in (Get-Command python.exe -All -ErrorAction SilentlyContinue)) { $c += $g.Source } } catch { }
    } else {
        foreach ($n in 'python3.13', 'python3') { $g = Get-Command $n -ErrorAction SilentlyContinue; if ($g) { $c += $g.Source } }
    }
    foreach ($p in ($c | Select-Object -Unique)) { if (Test-Py $p) { return $p } }
    return $null
}
function Get-LatestPy313 {
    $txt = Get-Text 'https://www.python.org/ftp/python/'
    $vers = @()
    if ($txt) {
        foreach ($m in [regex]::Matches($txt, 'href="3\.13\.(\d+)/"')) { $vers += [int]$m.Groups[1].Value }
    }
    $vers = $vers | Sort-Object -Descending | Select-Object -First 3
    foreach ($n in $vers) {
        $v = "3.13.$n"
        try {
            $r = Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$v/python-$v-amd64.exe" -Method Head -UseBasicParsing -TimeoutSec 20
            if ($r.StatusCode -eq 200) { return $v }
        } catch { }
    }
    return $PyFallback
}
function Install-Python313 ($tmp) {
    if ($Mock) { Info '(test) Python install skipped'; return (Find-Python313) }
    $v = Get-LatestPy313
    $exe = P2 $tmp "python-$v-amd64.exe"
    Say "Downloading Python $v from python.org (about 28 MB)..."
    $okDl = Get-File "https://www.python.org/ftp/python/$v/python-$v-amd64.exe" $exe "Python $v" 'python'
    if ($okDl) {
        $sig = Get-AuthenticodeSignature $exe
        if ($sig.Status -ne 'Valid' -or ([string]$sig.SignerCertificate.Subject) -notlike '*Python Software Foundation*') {
            Warn 'The Python installer signature did not check out, so it was not run.'
            $okDl = $false
        }
    }
    if ($okDl) {
        Say 'Installing Python 3.13 for your user only (no admin needed, 1-3 min)...'
        $argsPy = '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Shortcuts=0'
        $p = Start-Process -FilePath $exe -ArgumentList $argsPy -PassThru
        $t = 0
        while (-not $p.HasExited -and $t -lt 900) { Start-Sleep -Seconds 2; $t += 2; [Console]::Write(("`r    Installing Python... {0}s   " -f $t)) }
        [Console]::WriteLine('')
        if (-not $p.HasExited) { try { $p.Kill() } catch { } ; Warn 'The Python installer took too long.' }
        elseif ($p.ExitCode -ne 0) { Warn ("The Python installer ended with code " + $p.ExitCode + '.') }
        $found = Find-Python313
        if ($found) { return $found }
    }
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if ($wg) {
        Say 'Trying winget instead: winget install Python.Python.3.13 ...'
        $null = Run-Live $wg.Source @('install', '--id', 'Python.Python.3.13', '-e', '--scope', 'user', '--silent', '--accept-package-agreements', '--accept-source-agreements') ''
        $found = Find-Python313
        if ($found) { return $found }
    }
    return $null
}

# ------------------------------------------------------------------ Kaka helpers
function Stop-Kaka ($dir) {
    if (-not $OnWindows) { return 0 }
    $n = 0
    if ($env:LOCALAPPDATA) {
        $pidFile = P2 $env:LOCALAPPDATA 'Kaka\kaka.pid'
        if (Test-Path $pidFile) {
            $p = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($p -and $p -match '^\d+$') {
                $pr = Get-Process -Id ([int]$p) -ErrorAction SilentlyContinue
                if ($pr -and $pr.ProcessName -like 'python*') { & taskkill /PID $p /T /F 2>&1 | Out-Null; $n++ }
            }
        }
    }
    try {
        $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            ($_.Name -like 'python*.exe') -and $_.CommandLine -and (($_.CommandLine -like "*$dir\app\*") -or ($_.CommandLine -like "*$dir\Kaka-src\*") -or ($_.ExecutablePath -like "$dir\*"))
        }
        foreach ($p in $procs) { & taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null; $n++ }
    } catch { }
    if ($n -gt 0) { Start-Sleep -Seconds 2 }
    return $n
}
function Copy-Tree ($src, $dst, [string[]]$skipNames) {
    $src = (Resolve-Path $src).Path
    $count = 0
    foreach ($f in (Get-ChildItem -LiteralPath $src -Recurse -File -Force)) {
        $rel = $f.FullName.Substring($src.Length).TrimStart('\', '/')
        if ($skipNames -contains $f.Name) { continue }
        $to = P2 $dst $rel
        $toDir = Split-Path $to -Parent
        if (-not (Test-Path $toDir)) { New-Item -ItemType Directory -Force -Path $toDir | Out-Null }
        Copy-Item -LiteralPath $f.FullName -Destination $to -Force
        $count++
    }
    return $count
}
function New-Shortcut ($lnk, $target, $arguments, $workDir, $icon, $desc) {
    if (-not $OnWindows) {
        [IO.File]::WriteAllText($lnk + '.txt', "target=$target`nargs=$arguments`nworkdir=$workDir`nicon=$icon`n")
        return $true
    }
    try {
        $ws = New-Object -ComObject WScript.Shell
        $s = $ws.CreateShortcut($lnk)
        $s.TargetPath = $target
        $s.Arguments = $arguments
        $s.WorkingDirectory = $workDir
        if ($icon -and (Test-Path $icon)) { $s.IconLocation = "$icon,0" }
        $s.Description = $desc
        $s.Save()
        return $true
    } catch { Warn "Could not make shortcut $lnk ($($_.Exception.Message))"; return $false }
}
function Set-UserEnv ($name, $value) {
    if ($OnWindows -and -not $Mock) {
        [Environment]::SetEnvironmentVariable($name, $value, 'User')
    } else {
        $f = P2 $Home2 '.kaka-setup-mock-env.txt'
        Add-Content -Path $f -Value "$name=$value"
    }
    Set-Item -Path "env:$name" -Value $value
}
function Get-UserEnv ($name) {
    if ($OnWindows -and -not $Mock) { return [Environment]::GetEnvironmentVariable($name, 'User') }
    return $null
}
function Remove-UserEnvIfInside ($name, $folder) {
    $v = Get-UserEnv $name
    if ($v -and ($v.TrimEnd('\') -like ($folder.TrimEnd('\') + '*'))) {
        [Environment]::SetEnvironmentVariable($name, $null, 'User')
        Ok "Removed user variable $name"
    }
}
function Get-SpecialDir ($which) {
    if ($Mock) { $d = P2 $env:KAKA_MOCK_HOME $which; New-Item -ItemType Directory -Force -Path $d | Out-Null; return $d }
    if ($which -eq 'StartMenu') { return [Environment]::GetFolderPath('Programs') }
    return [Environment]::GetFolderPath($which)
}

# ================================================================== START
Banner
$script:SetupLines = @()

# ---- arguments (the admin relaunch passes the chosen folder, hex-encoded)
$PresetFolder = $null
$argText = [string]$env:KAKU_ARGS
if ($argText -match '/folder=([0-9a-fA-F]+)') { try { $PresetFolder = Hex-Decode $Matches[1] } catch { } }
if ($argText -match '/uninstall') { $env:KAKA_SETUP_MODE = 'uninstall' }
if ($env:KAKA_SETUP_FOLDER) { $PresetFolder = $env:KAKA_SETUP_FOLDER }

# ------------------------------------------------------------------ STEP 1: folder
Step 'Choose where to install Kaka'
$known = @()
$cands = @($env:KAKA_ROOT)
if ($OnWindows) { try { foreach ($dv in [IO.DriveInfo]::GetDrives()) { if ($dv.DriveType -eq 'Fixed' -and $dv.IsReady) { $cands += (P2 $dv.RootDirectory.FullName 'Kaka') } } } catch { } }
$cands += (P2 $Home2 'Kaka')
foreach ($k in $cands) {
    if ($k -and (Test-Path (P2 $k 'app\main.py'))) { $known += $k }
}
$known = @($known | Select-Object -Unique)
$suggest = $DefaultFolder
if ($known.Count -gt 0) {
    $suggest = $known[0]
    Info ("Kaka is already installed here: " + ($known -join ', '))
}
$Folder = $null
if ($PresetFolder) {
    $Folder = $PresetFolder
    Info "Folder: $Folder"
} elseif ($Unattended -or -not $OnWindows) {
    $Folder = Ask 'Install folder' $suggest
} else {
    Say 'A folder window is opening. Pick a folder on any drive (or make a new one).'
    Say "Default: $suggest   (Kaka uses about 3 GB there)"
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $owner = New-Object System.Windows.Forms.Form
        $owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.WindowState = 'Minimized'
        $owner.Show(); $owner.Hide()
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = "Kaka by Abhinaba Chanda $Dash choose the install folder (Kaka keeps its app, Data and Tools in it)"
        $dlg.ShowNewFolderButton = $true
        $start = $suggest
        while ($start -and -not (Test-Path $start)) { $start = Split-Path $start -Parent }
        if ($start) { $dlg.SelectedPath = $start }
        $res = $dlg.ShowDialog($owner)
        $owner.Dispose()
        if ($res -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.SelectedPath) {
            $Folder = $dlg.SelectedPath
            # picked the parent of the default (e.g. C:\) -> use C:\Kaka
            if ($Folder.TrimEnd('\') -eq (Split-Path $suggest -Parent).TrimEnd('\') -and -not (Test-Path (P2 $Folder 'app\main.py'))) { $Folder = $suggest }
        } else {
            Warn 'No folder picked in the window.'
            $Folder = Ask 'Type the install folder (Enter = default)' $suggest
        }
    } catch {
        Warn 'The folder window could not open here.'
        $Folder = Ask 'Type the install folder (Enter = default)' $suggest
    }
}
$Folder = [string]$Folder
$Folder = $Folder.Trim().Trim('"')
if (-not $Folder) { $Folder = $suggest }
try { $Folder = [IO.Path]::GetFullPath($Folder) } catch { Die "That folder name is not valid: $Folder" 'Run Kaka-Setup.bat again and pick a normal folder like C:\Kaka or D:\Kaka.' 2 }
$Folder = $Folder.TrimEnd('\', '/')
if ($Folder.Length -le 2) { $Folder = $Folder + '\Kaka' }                      # a drive root like D:
elseif ((Split-Path $Folder -Leaf) -ieq 'app' -and (Test-Path (P2 $Folder 'main.py'))) { $Folder = Split-Path $Folder -Parent }   # picked the app folder itself
if ($Folder -match '[^\x20-\x7E]') { Warn 'This folder name has non-English letters. Some Python packages break on such paths; a plain name like C:\Kaka is safer.' }
if ($OnWindows -and ($Folder -like "$env:ProgramFiles*" -or $Folder -like "${env:ProgramFiles(x86)}*" -or $Folder -like "$env:windir*")) {
    Warn 'Program Files / Windows folders need admin rights for every update. C:\Kaka or D:\Kaka is better.'
}
# a non-empty folder that is not Kaka -> put Kaka in a subfolder
$KakaItems = @('app', 'Kaka-src', 'docs', '.venv', 'Kaka', 'Data', 'Tools', 'Kaka-Setup.bat', 'README.md', 'LICENSE', '.gitignore')
$OldApp = $null   # an older Kaka layout: <folder>\<something>\app\main.py
if ((Test-Path $Folder) -and -not (Test-Path (P2 $Folder 'app\main.py'))) {
    foreach ($d in @(Get-ChildItem -LiteralPath $Folder -Directory -ErrorAction SilentlyContinue)) {
        if (Test-Path (P2 $d.FullName 'app\main.py')) { $OldApp = $d.FullName; break }
    }
}
if ((Test-Path $Folder) -and -not $OldApp -and -not (Test-Path (P2 $Folder 'app\main.py'))) {
    $items = @(Get-ChildItem -LiteralPath $Folder -Force -ErrorAction SilentlyContinue | Where-Object { $KakaItems -notcontains $_.Name })
    if ($items.Count -gt 0) {
        $sub = P2 $Folder 'Kaka'
        Info "That folder already has other files, so Kaka will go in $sub"
        $Folder = $sub
    }
}
$Dir      = $Folder
$App      = P2 $Dir 'app'
$Src      = P2 $Dir 'Kaka-src'
$Venv     = P2 $Dir '.venv'
$Data     = P2 $Folder 'Data'
$Tools    = P2 $Folder 'Tools'
$FfDir    = P2 $Tools 'ffmpeg'
$KeysFile = P2 $App 'config\api_keys.json'
$Existing = Test-Path (P2 $App 'main.py')

Write-Host ''
Write-Host '    Kaka will be installed in:' -ForegroundColor White
Write-Host "      $Folder" -ForegroundColor Green
Write-Host "        app\       Kaka itself (+ Kaka-src launcher, .venv Python)" -ForegroundColor DarkGray
Write-Host "        Data\      your notes, reminders, recordings, face data" -ForegroundColor DarkGray
Write-Host "        Tools\     ffmpeg (screen recording)" -ForegroundColor DarkGray
Write-Host ''

# ---- mode: install / repair / uninstall
$Mode = 'install'
if ($env:KAKA_SETUP_MODE) { $Mode = $env:KAKA_SETUP_MODE.ToLower() }
elseif ($Existing) {
    Info 'Kaka is already installed in this folder.'
    Say '  1  Repair / update   (new Kaka files + packages; keeps your Data and API keys)'
    Say '  2  Uninstall'
    Say '  3  Exit without changes'
    $c = Ask 'Choose 1, 2 or 3' '1'
    switch ($c) { '2' { $Mode = 'uninstall' } '3' { Say 'Nothing was changed.'; Finish 0 } default { $Mode = 'repair' } }
} else {
    if (-not (AskYN 'Install Kaka here?' $true)) { Say 'Nothing was changed. Run Kaka-Setup.bat again to pick another folder.'; Finish 0 }
}
if ($Mode -eq 'uninstall' -and -not $Existing) { Die "Kaka is not installed in $Folder, so there is nothing to uninstall." 'Run Kaka-Setup.bat again and pick the folder where Kaka is installed.' 2 }

# ---- admin only if the folder is not writable
if (-not (Test-Writable $Folder)) {
    if (Test-Admin) { Die "Windows does not allow writing to $Folder." 'Pick another folder, for example C:\Kaka or D:\Kaka.' 3 }
    Warn "Your Windows user cannot write to $Folder."
    if (AskYN 'Restart Setup as administrator for this folder?' $true) {
        try {
            Start-Process -FilePath $env:KAKU_BAT -ArgumentList ('/folder=' + (Hex-Encode $Folder)) -Verb RunAs -ErrorAction Stop
            Finish 20
        } catch { Die 'Administrator permission was not given.' 'Pick a folder you own (for example C:\Kaka or D:\Kaka), or click Yes on the Windows permission box.' 3 }
    }
    Die "Cannot write to $Folder." 'Pick a folder you own, for example C:\Kaka or D:\Kaka.' 3
}

# ================================================================== UNINSTALL
if ($Mode -eq 'uninstall') {
    $script:StepTotal = 1
    $script:StepNo = 0
    Step 'Uninstall Kaka'
    if (-not (AskYN "Remove Kaka from ${Folder}?" ($Unattended -and $env:KAKA_SETUP_MODE -eq 'uninstall'))) { Say 'Nothing was changed.'; Finish 0 }
    $n = Stop-Kaka $Dir
    if ($n -gt 0) { Ok 'Stopped the running Kaka' }
    $keepData = $true
    $ans = Ask 'Your Data folder (notes, recordings, face data) is KEPT. Type DELETE to remove it too, or press Enter to keep it' ''
    if ($ans -ceq 'DELETE') { $keepData = $false }
    if ($keepData -and (Test-Path $KeysFile)) {
        $bk = P2 $Data 'Backup'
        New-Item -ItemType Directory -Force -Path $bk | Out-Null
        Copy-Item -LiteralPath $KeysFile -Destination (P2 $bk 'api_keys.json') -Force
        Ok "Your API key file was moved to $bk (a new install offers to bring it back)"
    }
    foreach ($l in @((P2 (Get-SpecialDir 'Desktop') 'Kaka.lnk'), (P2 (Get-SpecialDir 'Startup') 'Kaka.lnk'), (P2 (Get-SpecialDir 'StartMenu') 'Kaka by Abhinaba Chanda.lnk'), (P2 (Get-SpecialDir 'StartMenu') 'Kaka Setup - repair or uninstall.lnk'))) {
        foreach ($x in @($l, ($l + '.txt'))) { if (Test-Path $x) { Remove-Item $x -Force -ErrorAction SilentlyContinue; Ok "Removed $x" } }
    }
    if ($OnWindows -and -not $Mock) {
        try {
            $run = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Kaka' -ErrorAction Stop
            if ([string]$run.Kaka -like "*$Dir*") { Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Kaka'; Ok 'Removed the AUTO-START entry' }
        } catch { }
        foreach ($v in 'KAKA_DATA', 'KAKA_TOOLS', 'KAKA_ROOT') { Remove-UserEnvIfInside $v $Folder }
    }
    Say 'Deleting the app and its Python environment (can take a minute)...'
    foreach ($it in $KakaItems) {
        if ($it -eq 'Data' -or $it -eq 'Kaka-Setup.bat') { continue }
        $pth = P2 $Folder $it
        if (Test-Path $pth) { Remove-Item -LiteralPath $pth -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not $keepData) { Remove-Item -LiteralPath $Data -Recurse -Force -ErrorAction SilentlyContinue }
    if ((Test-Path $App) -or (Test-Path $Venv)) { Warn "Some files in $Folder are in use and stayed. Restart the PC and delete them by hand." } else { Ok "Removed the app, its Python environment and Tools from $Folder" }
    $selfCopy = P2 $Folder 'Kaka-Setup.bat'
    if ((Test-Path $selfCopy) -and ($selfCopy -ne $env:KAKU_BAT)) { Remove-Item $selfCopy -Force -ErrorAction SilentlyContinue }
    if ($keepData) { Info "Your Data folder is still in $Data" }
    if ($OnWindows -and ($env:KAKU_BAT -eq $selfCopy)) {
        Start-Process cmd.exe -ArgumentList '/c', "ping -n 4 127.0.0.1 >nul & del /f /q `"$selfCopy`" & rd `"$Folder`"" -WindowStyle Hidden
    } elseif (-not (Get-ChildItem -LiteralPath $Folder -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $Folder -Force -ErrorAction SilentlyContinue }
    Info 'Python 3.13 was left installed (other programs may use it). Remove it in Settings > Apps if you want.'
    Write-Host ''
    Write-Host '    Kaka was uninstalled. Thank you for trying Kaka by Abhinaba Chanda!' -ForegroundColor Green
    Finish 0
}

# ------------------------------------------------------------------ your name (Kaka calls you by it)
function Clean-Name ($n) {
    $s = [string]$n
    $s = $s -replace '[\x00-\x1f\x7f"''`{}\[\]<>\\|]', ' '
    $s = ($s -replace '\s+', ' ').Trim()
    if ($s.Length -gt 40) { $s = $s.Substring(0, 40).Trim() }
    return $s
}
$CurName = ''
if (Test-Path $KeysFile) {
    try { $CurName = Clean-Name ((Get-Content -LiteralPath $KeysFile -Raw -Encoding UTF8 | ConvertFrom-Json).user_name) } catch { }
}
if (-not $CurName) { $CurName = Clean-Name (Get-UserEnv 'KAKA_USER_NAME') }
$NameDefault = $CurName
if ($env:KAKA_SETUP_NAME) { $NameDefault = Clean-Name $env:KAKA_SETUP_NAME }
if (-not $NameDefault) { $NameDefault = Clean-Name $env:USERNAME }
if (-not $NameDefault) { $NameDefault = 'dost' }
Write-Host ''
Write-Host '    Your name' -ForegroundColor White
if ($CurName) { Info "Abhi Kaka aapko '$CurName' bulata hai. Enter dabao to yahi naam rahega." }
$UserName = ''
while (-not $UserName) {
    $UserName = Clean-Name (Ask 'Aapka naam kya hai? (Kaka aapko isi naam se bulayega)' $NameDefault)
    if (-not $UserName) { Warn 'Naam khaali nahi chalega, phir se likho.' }
}
Ok "Kaka aapko '$UserName' bulayega"

# ------------------------------------------------------------------ your city (weather, morning brief)
$CurCity = ''
if (Test-Path $KeysFile) {
    try { $CurCity = Clean-Name ((Get-Content -LiteralPath $KeysFile -Raw -Encoding UTF8 | ConvertFrom-Json).user_city) } catch { }
}
if (-not $CurCity) { $CurCity = Clean-Name (Get-UserEnv 'KAKA_USER_CITY') }
$CityDefault = $CurCity
if ($env:KAKA_SETUP_CITY) { $CityDefault = Clean-Name $env:KAKA_SETUP_CITY }
if (-not $CityDefault) { $CityDefault = 'Kolkata' }
Write-Host ''
Write-Host '    Your city' -ForegroundColor White
if ($CurCity) { Info "Abhi city '$CurCity' hai. Enter dabao to yahi rahega." }
$UserCity = Clean-Name (Ask 'Aapka city kaunsa hai? (weather isi city ka batayega)' $CityDefault)
if (-not $UserCity) { $UserCity = 'Kolkata' }
Ok "Kaka '$UserCity' ka mausam batayega"

# ------------------------------------------------------------------ STEP 2: requirements
Step 'Checking your PC'
$script:rows = @()
function Row ($name, $state, $detail) { $script:rows += ,@($name, $state, $detail) }
$blockers = @()

# Windows version
$osName = 'unknown'; $osOk = $false; $build = 0
if ($Mock) { $osName = 'Windows 11 Home (test)'; $build = 22631; $osOk = $true }
elseif ($OnWindows) {
    try { $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop; $osName = $os.Caption; $build = [int]$os.BuildNumber } catch { $build = [Environment]::OSVersion.Version.Build }
    $osOk = ([Environment]::OSVersion.Version.Major -ge 10) -and [Environment]::Is64BitOperatingSystem
}
if ($osOk) { Row 'Windows 10/11 64-bit' 'OK' ("$osName (build $build)") }
else { Row 'Windows 10/11 64-bit' 'FAIL' "$osName"; $blockers += 'Kaka needs Windows 10 or 11, 64-bit.' }

# RAM (info)
$ramGB = 0
if ($Mock) { $ramGB = 16 } elseif ($OnWindows) { try { $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB) } catch { } }
if ($ramGB -ge 8) { Row 'Memory (RAM)' 'OK' "$ramGB GB" } elseif ($ramGB -gt 0) { Row 'Memory (RAM)' 'INFO' "$ramGB GB (8 GB or more is smoother)" } else { Row 'Memory (RAM)' 'INFO' 'could not read' }

# free space
$freeGB = Get-FreeGB $Folder
$needNow = $NeedGB; if ($Existing) { $needNow = 1 }
if ($freeGB -lt 0) { Row 'Free disk space' 'INFO' 'could not read' }
elseif ($freeGB -ge $needNow) { Row 'Free disk space' 'OK' ("$freeGB GB free on " + [IO.Path]::GetPathRoot($Folder) + " (needs about $NeedGB GB)") }
else { Row 'Free disk space' 'FAIL' ("only $freeGB GB free on " + [IO.Path]::GetPathRoot($Folder) + " (needs about $NeedGB GB)"); $blockers += "Not enough free space. Free up space or pick another drive (Kaka needs about $NeedGB GB)." }

# internet
$net = $true
if (-not $Mock) {
    foreach ($u in 'https://github.com', 'https://pypi.org/simple/pip/') {
        try { $null = Invoke-WebRequest -Uri $u -Method Head -UseBasicParsing -TimeoutSec 20 -Headers @{ 'User-Agent' = 'Kaka-Setup/1.0' } } catch { $net = $false }
    }
}
if ($net) { Row 'Internet (GitHub, PyPI)' 'OK' 'reachable' } else { Row 'Internet (GitHub, PyPI)' 'FAIL' 'not reachable'; $blockers += 'No internet (github.com / pypi.org not reachable). Connect to the internet, turn off any VPN/proxy that blocks them, and run Setup again.' }

# Python 3.13
$BasePy = Find-Python313
if ($BasePy) { Row 'Python 3.13 (64-bit)' 'OK' $BasePy } else { Row 'Python 3.13 (64-bit)' 'MISSING' 'will be installed for your user (about 28 MB)' }

# Git (optional)
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Row 'Git (optional)' 'OK' 'found (not needed: Setup downloads a zip)' } else { Row 'Git (optional)' 'INFO' 'not needed: Setup downloads a zip' }

# GPU
$gpuNames = @()
if ($Mock) { $gpuNames = @('NVIDIA GeForce RTX 3050 (test)') } elseif ($OnWindows) { try { $gpuNames = @(Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object { $_.Name }) } catch { } }
$nvidia = @($gpuNames | Where-Object { $_ -match 'NVIDIA' })
if ($nvidia.Count -gt 0) { Row 'NVIDIA GPU (optional)' 'OK' ($nvidia[0] + ' - fast NVENC screen recording') }
elseif ($gpuNames.Count -gt 0) { Row 'NVIDIA GPU (optional)' 'INFO' ("no NVIDIA (" + $gpuNames[0] + ") - recording uses the CPU (x264), fine") }
else { Row 'NVIDIA GPU (optional)' 'INFO' 'not found - recording uses the CPU (x264)' }

# webcam / mic (info only)
$cams = @(); $mics = @()
if ($Mock) { $cams = @('USB Camera (test)'); $mics = @('Microphone (test)') }
elseif ($OnWindows) {
    try { $cams = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop | Where-Object { ($_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image') -and $_.Status -eq 'OK' } | ForEach-Object { $_.Name }) } catch { }
    try { $mics = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop | Where-Object { $_.PNPClass -eq 'AudioEndpoint' -and $_.Name -match '(?i)microphone|mic|headset|array' -and $_.Status -eq 'OK' } | ForEach-Object { $_.Name }) } catch { }
}
if ($cams.Count -gt 0) { Row 'Webcam (optional)' 'OK' $cams[0] } else { Row 'Webcam (optional)' 'INFO' 'none found - face greet, chor alarm, gestures need one' }
if ($mics.Count -gt 0) { Row 'Microphone' 'OK' $mics[0] } else { Row 'Microphone' 'INFO' 'none found - plug one in to talk; typing works too' }

# print the table
Write-Host ''
Write-Host ('    {0,-26} {1,-8} {2}' -f 'Check', 'Result', 'Details') -ForegroundColor White
Write-Host ('    ' + ('-' * 66)) -ForegroundColor DarkGray
foreach ($r in $script:rows) {
    $col = 'Gray'
    switch ($r[1]) { 'OK' { $col = 'Green' } 'FAIL' { $col = 'Red' } 'MISSING' { $col = 'Yellow' } 'INFO' { $col = 'Cyan' } }
    $d = [string]$r[2]; if ($d.Length -gt 60) { $d = $d.Substring(0, 57) + '...' }
    Write-Host ('    {0,-26} ' -f $r[0]) -NoNewline
    Write-Host ('{0,-8} ' -f $r[1]) -ForegroundColor $col -NoNewline
    Write-Host $d -ForegroundColor DarkGray
}
if ($blockers.Count -gt 0) {
    Write-Host ''
    foreach ($b in $blockers) { Bad $b }
    if ($osOk -and $net) {
        if (-not (AskYN 'Continue anyway?' $false)) { Finish 4 }
    } else {
        Finish 4
    }
}

# ------------------------------------------------------------------ STEP 3: options
Step 'Choose options'
$opts = [ordered]@{}
$opts['desktop']    = @($true,  'Desktop shortcut "Kaka"')
$opts['startup']    = @($false, 'Start Kaka when Windows starts')
$opts['browsers']   = @($true,  'Browser control (Playwright Chromium + Firefox, ~400 MB)')
$ffNote = 'CPU recording'; if ($nvidia.Count -gt 0) { $ffNote = 'NVENC on your NVIDIA GPU' }
$opts['ffmpeg']     = @($true,  "Screen recording: ffmpeg (~190 MB download, $ffNote)")
$opts['face']       = @($true,  'Face greet + chor alarm (face models, ~10 MB)')
$opts['gestures']   = @($true,  'Hand gestures (MediaPipe + hand model, ~45 MB)')
$opts['discord']    = @($true,  'Discord phone remote + alarm alerts (discord.py, ~3 MB)')
$opts['wake']       = @($true,  'Wake word "Kaka" (Vosk + English model, ~40 MB)')
if ($cams.Count -eq 0 -and -not $Mock) { $opts['face'][0] = $false; $opts['gestures'][0] = $false }
$startLnk = P2 (Get-SpecialDir 'Startup') 'Kaka.lnk'
if ($Existing) { $opts['startup'][0] = ((Test-Path $startLnk) -or (Test-Path ($startLnk + '.txt'))) }
if ($env:KAKA_SETUP_OPTIONS) {   # e.g. "desktop,ffmpeg" (tests / power users)
    $want = $env:KAKA_SETUP_OPTIONS.ToLower().Split(',') | ForEach-Object { $_.Trim() }
    foreach ($k in @($opts.Keys)) { $opts[$k][0] = ($want -contains $k) }
}
$keys = @($opts.Keys)
while ($true) {
    Write-Host ''
    for ($i = 0; $i -lt $keys.Count; $i++) {
        $o = $opts[$keys[$i]]
        $box = '[ ]'; $col = 'DarkGray'
        if ($o[0]) { $box = '[x]'; $col = 'Green' }
        Write-Host ('     {0}  ' -f ($i + 1)) -NoNewline
        Write-Host $box -ForegroundColor $col -NoNewline
        Write-Host (' ' + $o[1])
    }
    Write-Host ''
    if ($Unattended) { break }
    $a = Ask 'Type a number to switch it on/off, or press Enter to continue' ''
    if ($a -eq '') { break }
    foreach ($tok in ($a -split '[ ,]+')) {
        $n = 0
        if ([int]::TryParse($tok, [ref]$n) -and $n -ge 1 -and $n -le $keys.Count) { $opts[$keys[$n - 1]][0] = -not $opts[$keys[$n - 1]][0] }
        elseif ($tok) { Warn "'$tok' is not a number from the list." }
    }
}
function Opt ($k) { return [bool]$opts[$k][0] }

$Tmp = P2 ([IO.Path]::GetTempPath()) ('kaka-setup-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

# ------------------------------------------------------------------ STEP 4: Python
Step 'Python 3.13'
if ($BasePy) { Ok "Using your Python: $BasePy" }
else {
    $BasePy = Install-Python313 $Tmp
    if (-not $BasePy) { Die 'Python 3.13 could not be installed automatically.' 'Install it yourself from https://www.python.org/downloads/ (Windows installer 64-bit, tick "Add python.exe to PATH"), then run Kaka-Setup.bat again.' 5 }
    Ok "Python 3.13 installed: $BasePy"
}

# ------------------------------------------------------------------ STEP 5: download Kaka
Step 'Downloading Kaka'
# newest Kaka = the main branch; the latest release is the fallback
$zip = P2 $Tmp 'kaka-src.zip'
$got = Get-File "$RepoUrl/archive/refs/heads/main.zip" $zip 'Kaka (latest)' 'repo'
if (-not $got) {
    $tag = $null
    $relJson = Get-Text "https://api.github.com/repos/$Repo/releases/latest"
    if ($relJson -and $relJson -match '"tag_name"\s*:\s*"([^"]+)"') { $tag = $Matches[1] }
    if ($tag) {
        Warn "Trying the $tag release instead."
        $got = Get-File "$RepoUrl/archive/refs/tags/$tag.zip" $zip "Kaka $tag (release)" 'repo'
    }
}
if (-not $got) { Die 'Kaka could not be downloaded from GitHub.' "Check your internet, then run Setup again. Or download the ZIP from $RepoUrl yourself." 6 }
$ex = P2 $Tmp 'src'
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $ex)
} catch { Die "The Kaka download is damaged ($($_.Exception.Message))." 'Run Setup again (it downloads a fresh copy).' 6 }
$top = Get-ChildItem -LiteralPath $ex -Directory | Select-Object -First 1
if (-not $top -or -not (Test-Path (P2 $top.FullName 'app/main.py'))) { Die 'The Kaka download does not look right (app\main.py missing).' "Run Setup again, or report it at $RepoUrl/issues" 6 }
if ($Mode -eq 'repair') {
    $n = Stop-Kaka $Dir
    if ($n -gt 0) { Ok 'Stopped the running Kaka so its files can be updated' }
}
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$hadKeys = Test-Path $KeysFile
# never overwrite your settings, keys or Discord file; the zip does not carry them anyway
$copied = Copy-Tree $top.FullName $Dir @('api_keys.json', 'discord.json', 'Kaka-Setup.bat')
Ok "$copied Kaka files copied to $Dir"
if ($hadKeys -and (Test-Path $KeysFile)) { Ok 'Your API keys were kept' }
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$discEx = P2 $Dir 'docs\config-examples\discord.example.json'
if ((Test-Path $discEx) -and -not (Test-Path (P2 $Data 'discord.example.json'))) { Copy-Item $discEx (P2 $Data 'discord.example.json') }
if ($OldApp) {
    Info "An older Kaka is in $OldApp. The new one uses the same Data folder ($Data)."
    $oldKeys = P2 $OldApp 'app\config\api_keys.json'
    if (-not (Test-Path $KeysFile) -and (Test-Path $oldKeys) -and (AskYN 'Copy your API key file from the older Kaka?' $true)) {
        Copy-Item $oldKeys $KeysFile -Force; Ok 'API key file copied from the older Kaka'
    }
    Info "When the new Kaka works, you can delete $OldApp (close Kaka first)."
}
if (-not (Test-Path $KeysFile)) {
    $bk = P2 $Data 'Backup\api_keys.json'
    if ((Test-Path $bk) -and (AskYN 'Found your API key file from an earlier Kaka install. Bring it back?' $true)) {
        Copy-Item $bk $KeysFile -Force; Ok 'Your earlier API key file is back'
    } else {
        Info 'No API keys yet. That is fine: Kaka asks for them when it opens.'
    }
}
Ok "Data folder: $Data"

# the app's own path settings: data, tools and launcher root follow the chosen folder
foreach ($pair in @(@('KAKA_DATA', $Data), @('KAKA_TOOLS', $Tools), @('KAKA_ROOT', $Dir))) {
    $old = Get-UserEnv $pair[0]
    if ($old -and $old -ne $pair[1]) { Info ("Changing user variable {0}: {1} -> {2}" -f $pair[0], $old, $pair[1]) }
    Set-UserEnv $pair[0] $pair[1]
}
Ok 'Kaka paths set (KAKA_DATA, KAKA_TOOLS, KAKA_ROOT)'

# your name: into the settings file (kaka_user.py keeps every other setting) + a user variable
Set-UserEnv 'KAKA_USER_NAME' $UserName
$nameSaved = $false
$kuPy = P2 $App 'kaka_user.py'
if ((Test-Path $kuPy) -and $BasePy) {
    $env:KAKA_SET_NAME = $UserName
    try { $kuOut = (& $BasePy $kuPy 'set' 2>&1 | Out-String); $nameSaved = ($LASTEXITCODE -eq 0) -and ($kuOut -match 'NAME\|saved\|') } catch { }
    Remove-Item env:KAKA_SET_NAME -ErrorAction SilentlyContinue
}
if (-not $nameSaved -and -not (Test-Path $KeysFile)) {
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path $KeysFile) | Out-Null
        $j = '{' + "`n" + '    "user_name": ' + (ConvertTo-Json $UserName) + "`n" + '}'
        [IO.File]::WriteAllText($KeysFile, $j, (New-Object System.Text.UTF8Encoding $false))
        $nameSaved = $true
    } catch { }
}
if ($nameSaved) { Ok "Your name saved: $UserName (change it later in Kaka: gear > Customise > YOUR NAME)" }
else { Warn "Your name is kept in the KAKA_USER_NAME user variable (the settings file was not changed). Kaka still uses it." }

# your city: into the settings file (kaka_user.py keeps every other setting) + a user variable
Set-UserEnv 'KAKA_USER_CITY' $UserCity
$citySaved = $false
if ((Test-Path $kuPy) -and $BasePy) {
    $env:KAKA_SET_CITY = $UserCity
    try { $kuOut = (& $BasePy $kuPy 'setcity' 2>&1 | Out-String); $citySaved = ($LASTEXITCODE -eq 0) -and ($kuOut -match 'CITY\|saved\|') } catch { }
    Remove-Item env:KAKA_SET_CITY -ErrorAction SilentlyContinue
}
if ($citySaved) { Ok "Your city saved: $UserCity (change it later: gear > Customise > YOUR CITY, or say 'Kaka, mera city Delhi kar do')" }
else { Warn "Your city is kept in the KAKA_USER_CITY user variable (the settings file was not changed). Kaka still uses it." }

# ------------------------------------------------------------------ STEP 6: Python packages
Step 'Python packages (5-15 minutes the first time)'
$Py = VenvPy $Venv $false
$Pyw = VenvPy $Venv $true
$venvOk = $false
if (Test-Path $Py) { $venvOk = Test-Py $Py }
if ((Test-Path $Venv) -and -not $venvOk) {
    Warn 'The old Python environment is broken or not 3.13, making a fresh one.'
    Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not $venvOk) {
    Say "Creating the Python environment in $Venv ..."
    $rc = Run-Live $BasePy @('-m', 'venv', $Venv) ''
    if (-not (Test-Path $Py)) { Die 'The Python environment (.venv) could not be created.' 'Reinstall Python 3.13 from python.org (tick "Add python.exe to PATH") and run Setup again. If antivirus blocked it, allow the Kaka folder.' 7 }
    Ok 'Python environment created'
} else { Ok 'Python environment is fine, reusing it' }
if (-not (Test-Path $Pyw)) { $Pyw = $Py }

Say 'Updating pip...'
$null = Run-Live $Py @('-m', 'pip', 'install', '--upgrade', 'pip', '--progress-bar', 'off') 'pip'
Say 'Installing Kaka''s packages (PyQt6, Gemini, OpenCV, Playwright ...):'
$req = P2 $App 'requirements.txt'
$pipArgs = @('-m', 'pip', 'install', '-r', $req, '--progress-bar', 'off', '--prefer-binary')
if ($Mock -and $env:KAKA_MOCK_PIP_CMD) { $pipArgs = @('-c', $env:KAKA_MOCK_PIP_CMD) }
$rc = Run-Live $Py $pipArgs 'pip'
if ($rc -ne 0) {
    Warn 'Some packages failed, trying once more...'
    $rc = Run-Live $Py $pipArgs 'pip'
}
if ($rc -ne 0) { Die 'Kaka''s Python packages did not install (pip error, see the yellow lines above).' 'Check your internet and run Setup again (choose 1 Repair). If it still fails, send the log file to the developer.' 8 }
Ok 'All packages installed'

# ------------------------------------------------------------------ STEP 7: tools and extras
Step 'Tools and extras'
Push-Location $App
if (Opt 'browsers') {
    Say 'Browser control: downloading Playwright browsers (Chromium + Firefox)...'
    $pwArgs = @('-m', 'playwright', 'install', 'chromium', 'firefox')
    if ($Mock) { $pwArgs = @('-c', 'print(''(test) playwright install skipped'')') }
    $rc = Run-Live $Py $pwArgs ''
    if ($rc -eq 0) { Ok 'Browsers ready'; $script:SetupLines += ,@('browsers', 'ok', 'Playwright browsers ready') }
    else { Warn 'Browsers did not download. Everything else works; retry later with Setup > Repair.'; $script:SetupLines += ,@('browsers', 'skipped', 'Playwright browsers did not download') }
}
if (Opt 'ffmpeg') {
    $ffExe = P2 $FfDir 'bin\ffmpeg.exe'
    $ffWorks = $false
    if (Test-Path $ffExe) { if ($Mock) { $ffWorks = $true } else { try { & $ffExe -hide_banner -version 2>$null | Out-Null; $ffWorks = ($LASTEXITCODE -eq 0) } catch { } } }
    if ($ffWorks) { Ok "ffmpeg already in $FfDir"; $script:SetupLines += ,@('ffmpeg', 'ok', 'ffmpeg ready') }
    else {
        $ffName = 'ffmpeg-master-latest-win64-gpl.zip'
        $ffBase = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest'
        $ffZip = P2 $Tmp $ffName
        Say 'Screen recording: downloading ffmpeg from GitHub (BtbN build)...'
        $okFf = Get-File "$ffBase/$ffName" $ffZip 'ffmpeg (Windows 64-bit)' 'ffmpeg'
        if ($okFf) {
            $sums = Get-Text "$ffBase/checksums.sha256"
            $want = $null
            if ($sums) { foreach ($ln in ($sums -split "`n")) { if ($ln -match ('^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($ffName) + '\s*$')) { $want = $Matches[1].ToLower() } } }
            $have = Get-Sha256 $ffZip
            if ($want -and $want -ne $have) { Warn 'ffmpeg download failed its checksum, not used.'; $okFf = $false }
            elseif (-not $want) { Warn 'Could not read the ffmpeg checksum list; using the file from GitHub anyway.' }
            else { Ok 'ffmpeg checksum OK' }
        }
        if ($okFf) {
            try {
                Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
                $binDir = P2 $FfDir 'bin'
                New-Item -ItemType Directory -Force -Path $binDir | Out-Null
                $z = [IO.Compression.ZipFile]::OpenRead($ffZip)
                $nOut = 0
                foreach ($e in $z.Entries) {
                    if ($e.FullName -match '/bin/(ffmpeg|ffprobe)\.exe$') {
                        $to = P2 $binDir $e.Name
                        [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $to, $true)
                        $nOut++
                    }
                }
                $z.Dispose()
                if ($nOut -ge 1) { Ok "ffmpeg ready in $binDir"; $script:SetupLines += ,@('ffmpeg', 'ok', 'ffmpeg ready') }
                else { Warn 'ffmpeg.exe was not inside the download.'; $script:SetupLines += ,@('ffmpeg', 'skipped', 'ffmpeg not in zip') }
            } catch { Warn "ffmpeg could not be unpacked ($($_.Exception.Message))"; $script:SetupLines += ,@('ffmpeg', 'skipped', 'ffmpeg unpack failed') }
        } else {
            Warn 'Screen recording stays off for now. Kaka can install ffmpeg itself later (say "recording start").'
            $script:SetupLines += ,@('ffmpeg', 'skipped', 'ffmpeg download failed')
        }
    }
}
if ($Mock) { $env:KAKA_U20_SETUP_DRY = '1' }
if (Opt 'face') {
    Say 'Face greet + chor alarm: getting the face models...'
    $null = Run-Live $Py @('-u', '-c', 'import sys; sys.path.insert(0, ''.''); from core import kaka_u21_setup as S; S.step_face()') 'setup'
}
if (Opt 'gestures') {
    Say 'Hand gestures: installing MediaPipe and the hand model...'
    $null = Run-Live $Py @('-u', '-m', 'core.kaka_u20_setup', '28') 'setup'
}
if (Opt 'discord') {
    Say 'Discord phone remote: installing discord.py...'
    $null = Run-Live $Py @('-u', '-m', 'core.kaka_u20_setup', '29') 'setup'
}
if (Opt 'wake') {
    Say 'Wake word "Hey Kaka": installing Vosk and the English model (~40 MB, progress below)...'
    $null = Run-Live $Py @('-u', '-c', 'import sys; sys.path.insert(0, ''.''); from core import wake_word as w; ok, m = w.install_and_download(); print(''SETUP|wake|'' + (''ok'' if ok else ''skipped'') + ''|Wake word: '' + str(m))') 'setup'
}
Pop-Location

# ------------------------------------------------------------------ STEP 8: shortcuts and finish
Step 'Shortcuts and finishing'
$Ico = P2 $Src 'kaka.ico'
$Launcher = P2 $Src 'kaka_launcher.py'
$Exe = P2 $Dir 'Kaka\Kaka.exe'
$lnkTarget = $Pyw; $lnkArgs = "`"$Launcher`""; $lnkStartArgs = "`"$Launcher`" --startup"
if (Test-Path $Exe) { $lnkTarget = $Exe; $lnkArgs = ''; $lnkStartArgs = '--startup' }
$sm = Get-SpecialDir 'StartMenu'
if (New-Shortcut (P2 $sm 'Kaka by Abhinaba Chanda.lnk') $lnkTarget $lnkArgs $Src $Ico 'Kaka by Abhinaba Chanda') { Ok 'Start Menu: "Kaka by Abhinaba Chanda"' }
$selfCopy = P2 $Folder 'Kaka-Setup.bat'
try {
    if ($env:KAKU_BAT -and (Test-Path $env:KAKU_BAT) -and ($env:KAKU_BAT -ne $selfCopy)) { Copy-Item $env:KAKU_BAT $selfCopy -Force }
    if (Test-Path $selfCopy) {
        $null = New-Shortcut (P2 $sm 'Kaka Setup - repair or uninstall.lnk') $selfCopy '' $Folder $Ico 'Repair, update or uninstall Kaka'
        Ok "Setup saved as $selfCopy (run it again to repair, update or uninstall)"
    }
} catch { }
$deskLnk = P2 (Get-SpecialDir 'Desktop') 'Kaka.lnk'
if (Opt 'desktop') { if (New-Shortcut $deskLnk $lnkTarget $lnkArgs $Src $Ico 'Kaka by Abhinaba Chanda') { Ok 'Desktop shortcut "Kaka"' } }
if (Opt 'startup') { if (New-Shortcut $startLnk $lnkTarget $lnkStartArgs $Src $Ico 'Start Kaka with Windows') { Ok 'Kaka will start with Windows' } }
else { foreach ($x in @($startLnk, ($startLnk + '.txt'))) { if (Test-Path $x) { Remove-Item $x -Force -ErrorAction SilentlyContinue; Ok 'Kaka will not start with Windows (Startup shortcut removed)' } } }

Remove-Item -LiteralPath $Tmp -Recurse -Force -ErrorAction SilentlyContinue

# summary
Write-Host ''
Write-Host '    Summary' -ForegroundColor White
Write-Host ('    ' + ('-' * 66)) -ForegroundColor DarkGray
Ok "Kaka app:        $App"
Ok "Your data:       $Data"
Ok "Python:          $Venv"
foreach ($s in $script:SetupLines) {
    if ($s[1] -eq 'ok') { Ok $s[2] } else { Warn ($s[2] + ' (Kaka still works; retry with Setup > Repair)') }
}

# launch
$launched = $false
if ($Mock) { Info '(test) Kaka launch skipped'; $launched = $true }
else {
    try {
        if ($lnkTarget -eq $Exe) { Start-Process -FilePath $Exe -WorkingDirectory $Src | Out-Null }
        else { Start-Process -FilePath $Pyw -ArgumentList "`"$Launcher`"" -WorkingDirectory $Src | Out-Null }
        $launched = $true
    } catch { Warn "Kaka did not start by itself ($($_.Exception.Message)). Use the Kaka shortcut." }
}
Write-Host ''
Write-Host ('  ' + ('=' * 66)) -ForegroundColor Green
Write-Host '     Kaka by Abhinaba Chanda is ready' -ForegroundColor Green
Write-Host ('  ' + ('=' * 66)) -ForegroundColor Green
Write-Host ''
if ($launched) { Say 'Kaka is opening now (the first start takes up to a minute).' }
Say 'API keys: Setup never asks for them. In Kaka:'
Say '  - first start: paste your free Gemini key (https://aistudio.google.com/apikey)'
Say '  - more keys any time: gear icon > API KEYS'
Say "Help and all features: $RepoUrl"
Say 'Then just say: "Kaka, what time is it?"'
Finish 0
