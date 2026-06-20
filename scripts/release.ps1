# Publish the built installer + portable ZIP as a GitHub Release.
# Run scripts\build_release.ps1 first (this script does not build).
# The tag/version is read from app\constants.py (APP_VERSION).
# Requires the GitHub CLI (gh) authenticated: gh auth login.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = ((Select-String -Path "app\constants.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"').Matches.Groups[1].Value)
$tag = "v$version"
$installer = Join-Path $root "installer_output\BulkSeqStudio-Setup-$version.exe"
$portable  = Join-Path $root "installer_output\BulkSeqStudio-Portable-$version.zip"
foreach ($f in @($installer, $portable)) {
    if (-not (Test-Path $f)) { throw "Missing artifact: $f  (run scripts\build_release.ps1 first)" }
}

# Locate gh (PATH, or the default winget install location).
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { throw "GitHub CLI (gh) not found. Install it and run 'gh auth login'." }

Write-Host "Publishing $tag ..."
& $gh release view $tag *> $null
if ($LASTEXITCODE -ne 0) {
    # New release: tag the current commit and attach both assets.
    & $gh release create $tag $installer $portable `
        --title "BulkSeq Studio $tag" `
        --notes "Windows installer and portable build for $tag. See the commit history for changes."
} else {
    # Release exists: replace the attached assets with the fresh build.
    & $gh release upload $tag $installer $portable --clobber
}
if ($LASTEXITCODE -ne 0) { throw "gh release failed" }
Write-Host "Done. Release: https://github.com/tunabirgun/bulkseq-studio/releases/tag/$tag"
