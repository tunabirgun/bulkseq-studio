param(
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$script:ExitCode = 0
$LogDir = Join-Path $PSScriptRoot "logs"
$LogPath = Join-Path $LogDir "wsl_setup.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogPath -Value $line
}

function Finish {
    param([int]$Code)
    Write-Step "Setup finished with exit code $Code."
    Write-Host ""
    Write-Host "Log written to: $LogPath"
    Write-Host ""
    Read-Host "Press Enter to close this setup window"
    exit $Code
}

try {
    Set-Content -Path $LogPath -Value "BulkSeq Studio WSL setup log"
    Write-Step "BulkSeq Studio Windows/WSL setup"
    Write-Step "Requested distro: $Distro"

    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Step "ERROR: This script must be run as Administrator."
        Finish 1
    }

    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
        Write-Step "wsl.exe was not found. Running: wsl --install -d $Distro"
        & wsl --install -d $Distro 2>&1 | ForEach-Object { Write-Step $_ }
        Write-Step "WSL installation command finished. Reboot Windows if prompted, then open BulkSeq Studio again."
        Finish $LASTEXITCODE
    }

    Write-Step "wsl.exe found at: $($wsl.Source)"
    Write-Step "Checking WSL status..."
    & wsl --status 2>&1 | ForEach-Object { Write-Step $_ }

    Write-Step "Checking installed WSL distributions..."
    $distros = & wsl -l -q 2>&1
    $distros | ForEach-Object { Write-Step $_ }
    if ($distros -match $Distro) {
        Write-Step "$Distro is already installed."
        Finish 0
    }

    Write-Step "Installing distro $Distro."
    & wsl --install -d $Distro 2>&1 | ForEach-Object { Write-Step $_ }
    $script:ExitCode = $LASTEXITCODE
    Write-Step "Distro installation command finished. Reboot Windows if prompted, then open BulkSeq Studio again."
    Finish $script:ExitCode
}
catch {
    Write-Step "ERROR: $($_.Exception.Message)"
    Finish 1
}
