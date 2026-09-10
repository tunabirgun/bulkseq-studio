# Publish the verified Windows and Linux packages.
# Download both CI artifact sets first (this script does not build).
# The tag/version is read from app\constants.py (APP_VERSION).
# Requires the GitHub CLI (gh) authenticated: gh auth login.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Get-Sha256Hex([string] $path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

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
    $hash = Get-Sha256Hex $f
    "$hash  $(Split-Path -Leaf $f)"
}
# LF line endings: the manifest is consumed by `sha256sum -c` on Linux and macOS, which
# treats a trailing CR as part of the file name and verifies nothing.
[System.IO.File]::WriteAllText($checksumManifest, (($checksumLines -join "`n") + "`n"), (New-Object System.Text.ASCIIEncoding))
$recorded = Get-Content -LiteralPath $checksumManifest
if ($recorded.Count -ne $packageAssets.Count) { throw "Checksum manifest entry count mismatch" }
foreach ($f in $packageAssets) {
    $name = Split-Path -Leaf $f
    $expectedLine = $recorded | Where-Object { $_ -match "^[0-9a-f]{64}  $([regex]::Escape($name))$" }
    if (@($expectedLine).Count -ne 1) { throw "Missing or duplicate checksum for $name" }
    $expected = ($expectedLine -split "  ", 2)[0]
    $actual = Get-Sha256Hex $f
    if ($actual -ne $expected) { throw "Checksum mismatch for $name" }
}
$assets = @($packageAssets) + @($checksumManifest)

# Locate gh (PATH, or the default winget install location).
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { throw "GitHub CLI (gh) not found. Install it and run 'gh auth login'." }

# The tag must name the commit that was built and verified. `gh release create` without
# --target tags the REMOTE default-branch head, which is not necessarily this working tree:
# an unpushed commit, a dirty tree or a stale local branch all publish a different tree than
# the one the artifacts came from, silently.
$dirty = & git status --porcelain
if ($LASTEXITCODE -ne 0) { throw "git status failed; cannot verify the tree before publishing." }
if ($dirty) { throw "Working tree is not clean; commit or stash first:`n$($dirty -join "`n")" }
& git fetch --quiet
if ($LASTEXITCODE -ne 0) { throw "git fetch failed; cannot compare HEAD with its upstream." }
$head = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "git rev-parse HEAD failed." }
$upstream = (& git rev-parse '@{u}').Trim()
if ($LASTEXITCODE -ne 0) { throw "No upstream branch; push this branch before publishing." }
if ($head -ne $upstream) {
    throw "HEAD ($head) differs from its upstream ($upstream). Push (or pull) before publishing."
}

# Verify CI workflows are successful before creating/updating the release.
Write-Host "Checking GitHub Actions workflows for $head ..."
$runListJson = & $gh run list --commit $head --json workflowName,status,conclusion --limit 20
if ($LASTEXITCODE -ne 0) { throw "gh run list failed" }

$runs = $runListJson | ConvertFrom-Json
$testsRun = $null
$buildRun = $null

foreach ($run in $runs) {
    if ($run.workflowName -eq "Tests" -and -not $testsRun) {
        $testsRun = $run
    } elseif ($run.workflowName -eq "Build packages" -and -not $buildRun) {
        $buildRun = $run
    }
}

if (-not $testsRun) { throw "Tests workflow not found for commit $head" }
if (-not $buildRun) { throw "Build packages workflow not found for commit $head" }

if ($testsRun.status -ne "completed") { throw "Tests workflow status is $($testsRun.status), expected completed" }
if ($buildRun.status -ne "completed") { throw "Build packages workflow status is $($buildRun.status), expected completed" }

if ($testsRun.conclusion -ne "success") { throw "Tests workflow conclusion is $($testsRun.conclusion), expected success" }
if ($buildRun.conclusion -ne "success") { throw "Build packages workflow conclusion is $($buildRun.conclusion), expected success" }

Write-Host "Tests: $($testsRun.conclusion)"
Write-Host "Build packages: $($buildRun.conclusion)"

Write-Host "Publishing $tag from $head ..."
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    & $gh release view $tag *> $null
    $releaseViewExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($releaseViewExit -ne 0) {
    # New release: tag the verified commit (not the remote default-branch head) and attach
    # every supported package.
    & $gh release create $tag @assets `
        --target $head `
        --title "BulkSeq Studio $tag" `
        --notes "Verified Windows and Linux packages for $tag. See the changelog and SHA256SUMS.txt for details."
} else {
    # Release exists: replace the attached assets with the fresh build.
    & $gh release upload $tag @assets --clobber
}
if ($LASTEXITCODE -ne 0) { throw "gh release failed" }

# Read the published release back. A green upload is not evidence the release is healthy:
# an asset can be missing or truncated, and the tag can point at another commit.
$viewJson = & $gh release view $tag --json assets,tagName,targetCommitish
if ($LASTEXITCODE -ne 0) { throw "gh release view failed; the release could not be verified." }
$release = $viewJson | ConvertFrom-Json
if ($release.tagName -ne $tag) { throw "Published tag is $($release.tagName), expected $tag" }
$commitish = [string] $release.targetCommitish
if ($commitish) {
    $resolved = & git rev-parse --verify --quiet "$commitish^{commit}"
    if ($LASTEXITCODE -eq 0 -and $resolved) {
        if ($resolved.Trim() -ne $head) {
            throw "Release $tag points at $($resolved.Trim()), not the published commit $head"
        }
    } else {
        Write-Host "Note: release target '$commitish' is not resolvable locally; not compared."
    }
}

# Every name in SHA256SUMS.txt must be attached, at the byte size of the file whose digest
# the manifest recorded. The manifest is the authority here, so a file that never made it
# into it cannot pass this check either.
$localByName = @{}
foreach ($f in $assets) { $localByName[(Split-Path -Leaf $f)] = (Get-Item -LiteralPath $f).Length }
$publishedByName = @{}
foreach ($a in $release.assets) { $publishedByName[$a.name] = [int64] $a.size }
$manifestNames = @($recorded | ForEach-Object { ($_ -split "  ", 2)[1] }) + @(Split-Path -Leaf $checksumManifest)
foreach ($name in $manifestNames) {
    if (-not $publishedByName.ContainsKey($name)) { throw "Release $tag is missing asset $name" }
    if ($publishedByName[$name] -ne $localByName[$name]) {
        throw "Asset $name is $($publishedByName[$name]) bytes on the release, $($localByName[$name]) locally"
    }
}
Write-Host "Verified $($manifestNames.Count) assets on $tag at commit $head."
Write-Host "Done. Release: https://github.com/tunabirgun/bulkseq-studio/releases/tag/$tag"
