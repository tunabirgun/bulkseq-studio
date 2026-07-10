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

    # Install a distribution non-interactively: --no-launch skips the Ubuntu account-creation
    # prompt (which would otherwise hang this window), so BulkSeq Studio can set up the tools as
    # root afterward. Falls back to a plain install if the installed wsl.exe predates --no-launch.
    function Install-Distro {
        param([string]$Name)
        Write-Step "Installing distribution '$Name' (no-launch)..."
        & wsl --install -d $Name --no-launch 2>&1 | ForEach-Object { Write-Step $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Step "--no-launch was not accepted (exit $LASTEXITCODE); retrying a plain install."
            Write-Step "An interactive Ubuntu setup window may open on older WSL; complete or close it to continue."
            & wsl --install -d $Name 2>&1 | ForEach-Object { Write-Step $_ }
        }
        return $LASTEXITCODE
    }

    # A distribution can be registered yet fail to start (a missing/broken ext4.vhdx). Probe a
    # real launch as root so "present" is not mistaken for "usable".
    function Test-DistroStarts {
        param([string]$Name)
        $out = & wsl -d $Name -u root -- echo BULKSEQ_OK 2>&1
        return ($LASTEXITCODE -eq 0 -and ($out -match "BULKSEQ_OK"))
    }

    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
        Write-Step "wsl.exe was not found. Installing WSL2 and the '$Distro' distribution."
        Install-Distro $Distro | Out-Null
        Write-Step "WSL installation command finished. Reboot Windows if prompted, then open BulkSeq Studio again."
        Finish $LASTEXITCODE
    }

    Write-Step "wsl.exe found at: $($wsl.Source)"
    Write-Step "Checking WSL status..."
    & wsl --status 2>&1 | ForEach-Object { Write-Step $_ }

    Write-Step "Checking installed WSL distributions..."
    # wsl.exe emits UTF-16LE; Windows PowerShell captures it NUL-interleaved, so strip the NUL
    # bytes before the name match -- otherwise a registered distro never matches and the
    # broken-distro guidance below is skipped in favor of an install-over.
    $distros = @(& wsl -l -q 2>&1) | ForEach-Object { ($_ -replace "`0", "").Trim() }
    $distros | Where-Object { $_ } | ForEach-Object { Write-Step $_ }

    if ($distros -match $Distro) {
        Write-Step "'$Distro' is registered. Checking that it starts..."
        if (Test-DistroStarts $Distro) {
            Write-Step "'$Distro' starts correctly."
            & wsl --set-default $Distro 2>&1 | ForEach-Object { Write-Step $_ }
            Finish 0
        }
        Write-Step "WARNING: '$Distro' is registered but will not start (a missing or broken virtual disk)."
        Write-Step "Its Linux filesystem may be unrecoverable. To reinstall it FROM SCRATCH -- this DELETES"
        Write-Step "everything stored inside that distribution -- run these two commands yourself, then reopen BulkSeq Studio:"
        Write-Step "    wsl --unregister $Distro"
        Write-Step "    wsl --install -d $Distro --no-launch"
        Finish 1
    }

    Write-Step "No '$Distro' distribution found. Installing it now."
    Install-Distro $Distro | Out-Null
    $script:ExitCode = $LASTEXITCODE
    if ($script:ExitCode -eq 0) {
        & wsl --set-default $Distro 2>&1 | ForEach-Object { Write-Step $_ }
        if (Test-DistroStarts $Distro) {
            Write-Step "'$Distro' installed and starts correctly."
        } else {
            Write-Step "'$Distro' installed. If it does not start yet, reboot Windows, then reopen BulkSeq Studio."
        }
    }
    Write-Step "Distro installation command finished. Reboot Windows if prompted, then open BulkSeq Studio again."
    Finish $script:ExitCode
}
catch {
    Write-Step "ERROR: $($_.Exception.Message)"
    Finish 1
}
