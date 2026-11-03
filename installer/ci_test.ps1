# Tests Kaka-Setup.exe on a real Windows PC (used by .github/workflows/build-setup-exe.yml).
# 1) silent fresh install  2) repair through the real window (with screenshots)
# 3) screenshots of the uninstall pages  4) silent uninstall
# Every check prints PASS/FAIL; any FAIL makes the script exit 1.
# Copyright (C) 2026 Abhinaba Chanda
param([string]$Exe = 'installer\dist\Kaka-Setup.exe', [string]$Out = 'ci-out')
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$Exe = (Resolve-Path $Exe).Path
$Dir = Join-Path (Split-Path $env:GITHUB_WORKSPACE -Parent) 'KakaTest'
$script:fails = 0
function Check ($name, $ok, $detail) {
    if ($ok) { Write-Host "PASS  $name  $detail" -ForegroundColor Green }
    else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ }
    Add-Content -Path (Join-Path $Out 'results.txt') -Value ("{0}  {1}  {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
}
function RunExe ($argList, $label, $minutes) {
    Write-Host "=== $label : Kaka-Setup.exe $($argList -join ' ')"
    $t0 = Get-Date
    $p = Start-Process -FilePath $Exe -ArgumentList $argList -PassThru
    if (-not $p.WaitForExit($minutes * 60 * 1000)) { try { $p.Kill() } catch { }; Check "$label finished in time" $false "killed after $minutes min"; return -999 }
    $sec = [int]((Get-Date) - $t0).TotalSeconds
    Write-Host "=== $label exit code $($p.ExitCode) after $sec s"
    return $p.ExitCode
}
function UserEnv ($n) { return [Environment]::GetEnvironmentVariable($n, 'User') }
function Keys { try { return (Get-Content (Join-Path $Dir 'app\config\api_keys.json') -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null } }
$Programs = [Environment]::GetFolderPath('Programs')
$Desktop = [Environment]::GetFolderPath('Desktop')
$LogFile = Join-Path $env:USERPROFILE 'kaka-setup-log.txt'

# ---------- version info
$vi = (Get-Item $Exe).VersionInfo
foreach ($pair in @(@('CompanyName', 'Abhinaba Chanda'), @('ProductName', 'Kaka Setup'), @('FileDescription', 'Kaka Setup'),
                    @('LegalCopyright', 'Copyright (C) 2026 Abhinaba Chanda'), @('OriginalFilename', 'Kaka-Setup.exe'))) {
    Check "version info $($pair[0])" ($vi.($pair[0]) -eq $pair[1]) "'$($vi.($pair[0]))'"
}
$size = (Get-Item $Exe).Length
Check 'exe size under 200 MB' ($size -lt 200MB) ("{0:N1} MB" -f ($size / 1MB))
Write-Host ("exe sha256 " + (Get-FileHash $Exe -Algorithm SHA256).Hash.ToLower())
$py = Get-ChildItem 'installer\build\python-3.13.*-amd64.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($py) {
    $sig = Get-AuthenticodeSignature $py.FullName
    Check 'bundled Python installer signed by the PSF' (($sig.Status -eq 'Valid') -and ([string]$sig.SignerCertificate.Subject -like '*Python Software Foundation*')) "$($py.Name) $($sig.Status)"
}
$py313Before = $null
try { $py313Before = (& py -3.13 -c "import sys;print(sys.executable)" 2>$null) } catch { }
Write-Host "python 3.13 on this PC before Setup: $py313Before"

# ---------- 0a) fresh-install pages through the window (screenshots only, nothing is run)
$env:QT_SCALE_FACTOR = '1.25'
$rc = RunExe @('--shots', (Join-Path $Out 'shots-fresh'), '--autopilot', '--shots-no-run', '--no-launch', '--name', 'Abhinaba', '--city', 'Kolkata', '--dir', $Dir) 'fresh-install pages' 5
Remove-Item env:QT_SCALE_FACTOR
$shots0 = @(Get-ChildItem (Join-Path $Out 'shots-fresh') -Filter *.png -ErrorAction SilentlyContinue)
Check 'fresh-install page screenshots' ($shots0.Count -ge 6) ("{0} files" -f $shots0.Count)
Check 'nothing installed by the page-only run' (-not (Test-Path (Join-Path $Dir 'app'))) ''

# ---------- 0b) the bundled Python installer code path (the exact functions from the engine)
if ($py) {
    $errs = $null; $tok = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'installer\build\kaka_setup_engine.ps1').Path, [ref]$tok, [ref]$errs)
    $want = 'Say','Ok','Info','Warn','Bad','P2','Fmt-MB','Get-File','Get-Text','Run-Live','Test-Py','Find-Python313','Get-LatestPy313','Install-Python313'
    $defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $want -contains $_.Name }
    $code = '$OnWindows = $true; $Mock = $false; $script:KakaGui = $true; $PyFallback = ''3.13.17''' + "`n" + (($defs | ForEach-Object { $_.Extent.Text }) -join "`n")
    $code += "`n" + '$t = Join-Path $env:RUNNER_TEMP ''pytest''; New-Item -ItemType Directory -Force -Path $t | Out-Null; $r = Install-Python313 $t; Write-Host "RESULT=$r"'
    $env:KAKA_GUI_PY_EXE = $py.FullName
    $env:KAKA_GUI_PY_VER = ($py.Name -replace '^python-(3\.13\.\d+)-amd64\.exe$', '$1')
    $codeFile = Join-Path $env:RUNNER_TEMP 'pytest.ps1'
    Set-Content -Path $codeFile -Value $code -Encoding ASCII
    $pyOut = (powershell -NoProfile -ExecutionPolicy Bypass -File $codeFile 2>&1 | Out-String)
    Remove-Item env:KAKA_GUI_PY_EXE, env:KAKA_GUI_PY_VER
    Set-Content -Path (Join-Path $Out 'bundled-python-test.txt') -Value $pyOut
    Check 'bundled Python installer used (no download)' ($pyOut -match 'packed inside Kaka-Setup\.exe') ''
    Check 'bundled Python installer signature accepted' (-not ($pyOut -match 'signature did not check out')) ''
    Check 'Python 3.13 found after the bundled installer' ($pyOut -match 'RESULT=\S+python\.exe') (($pyOut -split "`n" | Where-Object { $_ -match 'RESULT=' }) -join ' ')
}

# ---------- 1) silent fresh install
$rc = RunExe @('--silent', '--name', 'Abhinaba', '--city', 'Kolkata', '--dir', $Dir, '--key', 'DUMMY-KEY-CI-1234', '--no-launch', '--log-out', (Join-Path $Out 'install-output.txt')) 'install' 50
Check 'install exit code 0' ($rc -eq 0) "rc=$rc"
foreach ($f in 'app\main.py', 'app\kaka_user.py', 'Kaka-src\kaka_launcher.py', 'Kaka-src\kaka.ico', '.venv\Scripts\python.exe', '.venv\Scripts\pythonw.exe', 'Data', 'Kaka-Setup.bat') {
    Check "file $f" (Test-Path (Join-Path $Dir $f)) ''
}
$batSha = (Get-FileHash (Join-Path $Dir 'Kaka-Setup.bat') -Algorithm SHA256).Hash.ToLower()
$rootSha = (Get-FileHash 'Kaka-Setup-V1.1.bat' -Algorithm SHA256).Hash.ToLower()
Check 'repair copy Kaka-Setup.bat == Kaka-Setup-V1.1.bat' ($batSha -eq $rootSha) $batSha
$k = Keys
Check 'api_keys.json user_name' ($k.user_name -eq 'Abhinaba') "'$($k.user_name)'"
Check 'api_keys.json user_city' ($k.user_city -eq 'Kolkata') "'$($k.user_city)'"
Check 'api_keys.json gemini_api_key' ($k.gemini_api_key -eq 'DUMMY-KEY-CI-1234') ''
Check 'env KAKA_ROOT' ((UserEnv 'KAKA_ROOT') -eq $Dir) (UserEnv 'KAKA_ROOT')
Check 'env KAKA_DATA' ((UserEnv 'KAKA_DATA') -eq (Join-Path $Dir 'Data')) (UserEnv 'KAKA_DATA')
Check 'env KAKA_TOOLS' ((UserEnv 'KAKA_TOOLS') -eq (Join-Path $Dir 'Tools')) (UserEnv 'KAKA_TOOLS')
Check 'env KAKA_USER_NAME' ((UserEnv 'KAKA_USER_NAME') -eq 'Abhinaba') (UserEnv 'KAKA_USER_NAME')
Check 'env KAKA_USER_CITY' ((UserEnv 'KAKA_USER_CITY') -eq 'Kolkata') (UserEnv 'KAKA_USER_CITY')
Check 'Start Menu: Kaka by Abhinaba Chanda' (Test-Path (Join-Path $Programs 'Kaka by Abhinaba Chanda.lnk')) ''
Check 'Start Menu: Kaka Setup - repair or uninstall' (Test-Path (Join-Path $Programs 'Kaka Setup - repair or uninstall.lnk')) ''
Check 'Desktop: Kaka.lnk' (Test-Path (Join-Path $Desktop 'Kaka.lnk')) ''
Check 'log file' (Test-Path $LogFile) $LogFile
$vpy = Join-Path $Dir '.venv\Scripts\python.exe'
$imp = (& $vpy -c "import PyQt6, google.genai; print('imports-ok')" 2>&1 | Out-String).Trim()
Check 'venv imports PyQt6 + google.genai' ($imp -match 'imports-ok') $imp
$o = Get-Content (Join-Path $Out 'install-output.txt') -Raw
Check 'all 8 steps ran' (($o -match '\[8/8\]') -and ($o -match '\[1/8\]')) ''
Check 'wake word step ran' ($o -match 'Wake word') ''

# ---------- 2) repair through the window (autopilot), screenshots at 125 %
$env:QT_SCALE_FACTOR = '1.25'
$rc = RunExe @('--shots', (Join-Path $Out 'shots-repair'), '--autopilot', '--no-launch') 'repair (window)' 40
Check 'repair exit code 0' ($rc -eq 0) "rc=$rc"
$k = Keys
Check 'repair kept user_name' ($k.user_name -eq 'Abhinaba') "'$($k.user_name)'"
Check 'repair kept user_city' ($k.user_city -eq 'Kolkata') "'$($k.user_city)'"
Check 'repair kept gemini_api_key' ($k.gemini_api_key -eq 'DUMMY-KEY-CI-1234') ''
Check 'repair kept env KAKA_USER_NAME' ((UserEnv 'KAKA_USER_NAME') -eq 'Abhinaba') ''
Check 'repair kept .venv' (Test-Path $vpy) ''
$shots = @(Get-ChildItem (Join-Path $Out 'shots-repair') -Filter *.png -ErrorAction SilentlyContinue)
Check 'repair screenshots taken' ($shots.Count -ge 8) ("{0} files: {1}" -f $shots.Count, (($shots | ForEach-Object { $_.Name }) -join ', '))

# ---------- 3) uninstall pages (screenshots only, nothing is run)
$rc = RunExe @('--shots', (Join-Path $Out 'shots-uninstall'), '--autopilot', '--uninstall', '--shots-no-run', '--no-launch') 'uninstall pages' 5
$shots2 = @(Get-ChildItem (Join-Path $Out 'shots-uninstall') -Filter *.png -ErrorAction SilentlyContinue)
Check 'uninstall page screenshots' ($shots2.Count -ge 3) ("{0} files" -f $shots2.Count)
Check 'still installed after page-only run' (Test-Path (Join-Path $Dir 'app\main.py')) ''
Remove-Item env:QT_SCALE_FACTOR

# ---------- 4) silent uninstall (keeps Data, keeps Python)
$basePy = $null
try { $basePy = (& $vpy -c "import sys;print(sys.base_prefix)" 2>$null) } catch { }
$rc = RunExe @('--silent', '--mode', 'uninstall', '--dir', $Dir, '--log-out', (Join-Path $Out 'uninstall-output.txt')) 'uninstall' 10
Check 'uninstall exit code 0' ($rc -eq 0) "rc=$rc"
Check 'app removed' (-not (Test-Path (Join-Path $Dir 'app'))) ''
Check 'Data kept' (Test-Path (Join-Path $Dir 'Data')) ''
Check 'API key file backed up in Data\Backup' (Test-Path (Join-Path $Dir 'Data\Backup\api_keys.json')) ''
Check 'Start Menu shortcut removed' (-not (Test-Path (Join-Path $Programs 'Kaka by Abhinaba Chanda.lnk'))) ''
Check 'env KAKA_ROOT removed' (-not (UserEnv 'KAKA_ROOT')) ''
if ($basePy) { Check 'Python 3.13 still installed' (Test-Path (Join-Path $basePy.Trim() 'python.exe')) $basePy.Trim() }

Copy-Item $LogFile (Join-Path $Out 'kaka-setup-log.txt') -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "failures: $script:fails"
if ($script:fails -gt 0) { exit 1 }
exit 0
