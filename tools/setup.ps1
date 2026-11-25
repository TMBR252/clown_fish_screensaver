<#
    One-step setup: find a usable Python, build a private virtual environment, install
    what the tank needs, and check that this machine can actually run it.

    Everything is resolved relative to this script, so the project works from any
    folder, including one with spaces in the path, and from a fresh clone.
#>
[CmdletBinding()]
param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venv = Join-Path $root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$req = Join-Path $root 'requirements.txt'

function Say($text) { if (-not $Quiet) { Write-Host $text } }

Say ""
Say "Clownfish wallpaper - setup"
Say "  project: $root"
Say ""

# --- 1. find a Python we can use ---------------------------------------------------
$MIN = [version]'3.9'
$candidates = @()
foreach ($probe in @('py -3 -c "import sys;print(sys.executable)"',
                     'python -c "import sys;print(sys.executable)"')) {
    try {
        $out = (cmd /c $probe 2>$null)
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates += $out.Trim() }
    } catch { }
}
$candidates = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

$python = $null
foreach ($c in $candidates) {
    $v = & $c -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$v -ge $MIN) { $python = $c; $pyver = $v; break }
}
if (-not $python) {
    Write-Host ""
    Write-Host "No Python $MIN or newer was found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add to PATH'),"
    Write-Host "then run this again."
    exit 1
}
Say "  python : $python  ($pyver)"

# --- 2. the virtual environment ----------------------------------------------------
if (-not (Test-Path $venvPy)) {
    Say "  making a virtual environment in .venv ..."
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "venv creation failed." -ForegroundColor Red; exit 1 }
} else {
    Say "  .venv already there"
}

Say "  installing dependencies ..."
& $venvPy -m pip install --quiet --upgrade pip 2>&1 | Out-Null
& $venvPy -m pip install --quiet -r $req
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Dependency install failed." -ForegroundColor Red
    Write-Host "If it was moderngl, your Python may be too new for a prebuilt wheel."
    Write-Host "The tank still runs without it, on the CPU: start it with --no-gpu."
    exit 1
}

# --- 3. does this machine actually work? -------------------------------------------
Say ""
Say "  checking this machine ..."
$check = Join-Path $root 'tools\selfcheck.py'
& $venvPy $check
$ok = $LASTEXITCODE

Say ""
if ($ok -eq 0) {
    Say "Ready. Start it with:   start.cmd"
    Say "Stop it with:           stop.cmd   (or Ctrl+Alt+Shift+F)"
    Say "Run at login:           install-startup.cmd"
} else {
    Write-Host "Setup finished, but the self-check found problems above." -ForegroundColor Yellow
}
exit $ok
