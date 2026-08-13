# Publish the verified Windows and Linux packages.
# Download both CI artifact sets first (this script does not build).
# The tag/version is read from app\constants.py (APP_VERSION).
# Requires the GitHub CLI (gh) authenticated: gh auth login.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = ((Select-String -Path "app\constants.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"').Matches.Groups[1].Value)
$tag = "v$version"
$installer = Join-Path $root "installer_output\BulkSeqStudio-Setup-$version.exe"
$portable  = Join-Path $root "installer_output\BulkSeqStudio-Portable-$version.zip"
$appImage = Join-Path $root "installer_output\BulkSeqStudio-$version-x86_64.AppImage"
$zsync = "$appImage.zsync"
$linuxPortable = Join-Path $root "installer_output\BulkSeqStudio-Portable-$version-linux-x86_64.tar.gz"
$packageAssets = @($installer, $portable, $appImage, $zsync, $linuxPortable)
foreach ($f in $packageAssets) {
    if (-not (Test-Path $f)) { throw "Missing artifact: $f  (build locally or download the verified CI artifact first)" }
    if ((Get-Item -LiteralPath $f).Length -le 0) { throw "Empty artifact: $f" }
}

# Derive the checksum manifest from the exact payload being released, then read it
# back and independently recompute every digest before any tag or upload is made.
$checksumManifest = Join-Path $root "installer_output\SHA256SUMS.txt"
$checksumLines = foreach ($f in $packageAssets) {
    $hash = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $f)"
}
Set-Content -LiteralPath $checksumManifest -Value $checksumLines -Encoding ascii
$recorded = Get-Content -LiteralPath $checksumManifest
if ($recorded.Count -ne $packageAssets.Count) { throw "Checksum manifest entry count mismatch" }
foreach ($f in $packageAssets) {
    $name = Split-Path -Leaf $f
    $expectedLine = $recorded | Where-Object { $_ -match "^[0-9a-f]{64}  $([regex]::Escape($name))$" }
    if (@($expectedLine).Count -ne 1) { throw "Missing or duplicate checksum for $name" }
    $expected = ($expectedLine -split "  ", 2)[0]
    $actual = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Checksum mismatch for $name" }
}
$assets = @($packageAssets) + @($checksumManifest)

# Locate gh (PATH, or the default winget install location).
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { throw "GitHub CLI (gh) not found. Install it and run 'gh auth login'." }

Write-Host "Publishing $tag ..."
& $gh release view $tag *> $null
if ($LASTEXITCODE -ne 0) {
    # New release: tag the current commit and attach every supported package.
    & $gh release create $tag @assets `
        --title "BulkSeq Studio $tag" `
        --notes "Verified Windows and Linux packages for $tag. See the changelog and SHA256SUMS.txt for details."
} else {
    # Release exists: replace the attached assets with the fresh build.
    & $gh release upload $tag @assets --clobber
}
if ($LASTEXITCODE -ne 0) { throw "gh release failed" }
Write-Host "Done. Release: https://github.com/tunabirgun/bulkseq-studio/releases/tag/$tag"
