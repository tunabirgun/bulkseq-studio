# Publish only the verified CI packages. Local checksums stay in ignored staging.
# The tag/version is read from app\constants.py (APP_VERSION).
# Requires the GitHub CLI (gh) authenticated: gh auth login.
param([string] $DownloadRoot = "")
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
Write-Host "Checking GitHub Actions workflows for the current commit..."
$runListJson = & $gh run list --commit $head --json workflowName,status,conclusion,databaseId --limit 20
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

# The Environment workflow stays out of the release gate on purpose: it is path-filtered and
# scheduled, so it usually has no run for this commit at all. Its last result is still worth
# seeing -- a failing or long-stale run means the pinned channels may no longer solve for a
# new user, which no package check here would notice. Reported, never thrown.
$environmentJson = & $gh run list --workflow "Environment" --limit 1 --json conclusion,status,createdAt
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Environment workflow could not be queried; its state is unknown (not a release gate)."
} else {
    $environmentRun = $null
    try { $environmentRun = @($environmentJson | ConvertFrom-Json)[0] } catch { }
    if (-not $environmentRun) {
        Write-Warning "Environment workflow has no runs; the live installer check is unproven (not a release gate)."
    } else {
        $age = "an unknown number of"
        try {
            $created = [datetime]::Parse(
                [string] $environmentRun.createdAt,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind)
            $age = [int] ((Get-Date).ToUniversalTime() - $created.ToUniversalTime()).TotalDays
        } catch { }
        Write-Warning "Environment workflow last run: $($environmentRun.conclusion) ($($environmentRun.status)), $age days old (not a release gate)."
    }
}

# Refuse an existing tag or release; never edit notes or clobber assets in place.
$tagRefs = & git ls-remote --tags origin "refs/tags/$tag"
if ($LASTEXITCODE -ne 0) { throw "Could not verify the remote tag state" }
if ($tagRefs) { throw "Tag $tag already exists; refusing to replace it" }
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $releaseProbe = & $gh release view $tag --json tagName 2>&1
    $releaseViewExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($releaseViewExit -eq 0) { throw "Release $tag already exists; refusing to replace it" }
if (($releaseProbe -join " ") -notmatch '(?i)(release not found|not found)') {
    throw "Release lookup failed for a reason other than absence"
}

$releaseRoot = [IO.Path]::GetFullPath((Join-Path $root "tmp\release-$version"))
if (-not $DownloadRoot) { $DownloadRoot = Join-Path $releaseRoot "ci-run-$($buildRun.databaseId)" }
if (-not [IO.Path]::IsPathRooted($DownloadRoot)) { $DownloadRoot = Join-Path $root $DownloadRoot }
$outputDir = [IO.Path]::GetFullPath($DownloadRoot)
if (-not $outputDir.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "DownloadRoot must be a child of $releaseRoot"
}
if (Test-Path -LiteralPath $outputDir) { throw "Download stage already exists; preserving it: $outputDir" }
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputDir -ErrorAction Stop | Out-Null
$installer = Join-Path $outputDir "BulkSeqStudio-Setup-$version.exe"
$portable = Join-Path $outputDir "BulkSeqStudio-Portable-$version.zip"
$appImage = Join-Path $outputDir "BulkSeqStudio-$version-x86_64.AppImage"
$zsync = "$appImage.zsync"
$linuxPortable = Join-Path $outputDir "BulkSeqStudio-Portable-$version-linux-x86_64.tar.gz"
$packageAssets = @($installer, $portable, $appImage, $zsync, $linuxPortable)
$checksumManifest = Join-Path $outputDir "SHA256SUMS.txt"
foreach ($artifact in @("BulkSeqStudio-windows", "BulkSeqStudio-linux")) {
    Write-Host "Downloading verified $artifact CI artifact..."
    & $gh run download $buildRun.databaseId -n $artifact -D $outputDir
    if ($LASTEXITCODE -ne 0) { throw "gh run download failed for artifact $artifact" }
}

foreach ($f in $packageAssets) {
    if (-not (Test-Path $f)) { throw "Missing artifact: $f  (run $($buildRun.databaseId) did not attach it)" }
    if ((Get-Item -LiteralPath $f).Length -le 0) { throw "Empty artifact: $f" }
}

# Derive the checksum manifest from the exact payload being released, then read it
# back and independently recompute every digest before any tag or upload is made.
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
$assets = @($packageAssets)

Write-Host "Publishing verified CI packages for $tag..."
# The release page is where people download from, so it carries this version's own changelog
# entry rather than a line pointing at a file they would have to go and find. A release that
# changes scientific output has to say so at the point of download. The entry is read from
# CHANGELOG.md between this version's heading and the next, so the notes cannot drift from the
# changelog; an absent entry is a hard failure, not a quiet fallback to boilerplate.
$changelogLines = Get-Content -Path "CHANGELOG.md" -Encoding UTF8
$startIndex = -1
for ($i = 0; $i -lt $changelogLines.Count; $i++) {
    if ($changelogLines[$i] -match "^##\s+$([regex]::Escape($version))(\s|$)") { $startIndex = $i; break }
}
if ($startIndex -lt 0) { throw "CHANGELOG.md has no '## $version' entry; write it before releasing." }
$endIndex = $changelogLines.Count
for ($i = $startIndex + 1; $i -lt $changelogLines.Count; $i++) {
    if ($changelogLines[$i] -match "^##\s+\d+\.\d+\.\d+") { $endIndex = $i; break }
}
$entry = ($changelogLines[($startIndex + 1)..($endIndex - 1)] -join "`n").Trim()
if (-not $entry) { throw "CHANGELOG.md's '## $version' entry is empty; write it before releasing." }
$notesPath = Join-Path $outputDir "release-notes.md"
$notesBody = $entry
[System.IO.File]::WriteAllText($notesPath, $notesBody, (New-Object System.Text.UTF8Encoding($false)))

& $gh release create $tag @assets `
    --target $head `
    --title "BulkSeq Studio $tag" `
    --notes-file $notesPath
if ($LASTEXITCODE -ne 0) { throw "gh release failed" }

# Read the published release back. A green upload is not evidence the release is healthy:
# an asset can be missing or truncated, and the tag can point at another commit.
$viewJson = & $gh release view $tag --json assets,tagName,targetCommitish
if ($LASTEXITCODE -ne 0) { throw "gh release view failed; the release could not be verified." }
$release = $viewJson | ConvertFrom-Json
if ($release.tagName -ne $tag) { throw "Published tag is $($release.tagName), expected $tag" }
$remoteTag = @()
foreach ($attempt in 1..5) {
    $remoteTag = @(& git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")
    if ($LASTEXITCODE -ne 0) { throw "Could not verify the published remote tag" }
    if ($remoteTag.Count) { break }
    Start-Sleep -Seconds 2
}
$peeled = @($remoteTag | Where-Object { $_ -match '\^\{\}$' })
$direct = @($remoteTag | Where-Object { $_ -match "refs/tags/$([regex]::Escape($tag))$" })
$target = if ($peeled.Count -eq 1) { ($peeled[0] -split '\s+')[0] }
          elseif ($direct.Count -eq 1) { ($direct[0] -split '\s+')[0] }
          else { "" }
if (-not $target -or $target -ne $head) { throw "Published tag does not resolve to the verified commit" }

# The local manifest was verified before upload. Public assets are the five packages only.
$localByName = @{}
foreach ($f in $assets) { $localByName[(Split-Path -Leaf $f)] = (Get-Item -LiteralPath $f).Length }
$publishedByName = @{}
foreach ($a in $release.assets) { $publishedByName[$a.name] = [int64] $a.size }
$assetNames = @($assets | ForEach-Object { Split-Path -Leaf $_ })
if (@($release.assets).Count -ne $assetNames.Count) { throw "Published asset inventory differs from the approved package list" }
foreach ($name in $assetNames) {
    if (-not $publishedByName.ContainsKey($name)) { throw "Release $tag is missing asset $name" }
    if ($publishedByName[$name] -ne $localByName[$name]) {
        throw "Asset $name is $($publishedByName[$name]) bytes on the release, $($localByName[$name]) locally"
    }
}
Write-Host "Verified $($assetNames.Count) package assets on $tag."
Write-Host "Done. Release: https://github.com/tunabirgun/bulkseq-studio/releases/tag/$tag"
