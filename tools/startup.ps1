<#
    Add or remove a Startup-folder shortcut for the clownfish wallpaper.

    A shortcut rather than a registry Run entry, because you can see it, move it and
    delete it by hand, and Windows lists it in Task Manager's Startup tab where you can
    disable it without touching these files.

    It points at pythonw.exe so nothing flashes a console at login. The tank itself
    waits for the shell to build its WorkerW before deciding where to live -- at login
    we can easily be ahead of explorer.

    Usage:  powershell -ExecutionPolicy Bypass -File startup.ps1 -Install
            powershell -ExecutionPolicy Bypass -File startup.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pyw = Join-Path $root '.venv\Scripts\pythonw.exe'
$run = Join-Path $root 'run.pyw'
$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'Clownfish Wallpaper.lnk'

if ($Remove) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        "Removed: $link"
    } else {
        "Nothing to remove; no shortcut at $link"
    }
    exit 0
}

if (-not $Install) {
    "Pass -Install or -Remove."
    exit 1
}

foreach ($needed in @($pyw, $run)) {
    if (-not (Test-Path $needed)) {
        Write-Error "Missing: $needed"
        exit 1
    }
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $pyw
$sc.Arguments = '"{0}"' -f $run
$sc.WorkingDirectory = $root
$sc.Description = 'ASCII clownfish desktop wallpaper'
$sc.WindowStyle = 7              # minimised; pythonw shows nothing anyway
$sc.Save()

"Installed: $link"
"  runs: $pyw `"$run`""
"  Paths are absolute -- moving $root breaks the shortcut; re-run this to fix it."
"  Disable it any time in Task Manager > Startup apps, or run this with -Remove."
